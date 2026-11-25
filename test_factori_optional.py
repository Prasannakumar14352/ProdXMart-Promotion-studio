"""End-to-end tests proving complete workflows run cleanly with Official Factori API disabled."""

import base64
from contextlib import ExitStack
import csv
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import engine
import product_editor
from agents.catalog_adapter import FactoriApiAdapter
from agents.base import AgentConfigurationError
from agents.excel_io import write_xlsx_sheets
from agents.orchestrator import MasterOrchestrator
from agents.sales_analytics import build_sales_analytics, import_financial_upload
from agents.social_agent import approve_social_post, publish_or_prepare_social_post
from agents.storage import AgentStorage
from test_agents import FakeOllamaClient, invoke_handler, wait_until


def _sample_photo_bytes(color=(30, 120, 90)):
    buf = BytesIO()
    Image.new('RGB', (1200, 1200), color).save(buf, 'PNG')
    return buf.getvalue()


class FactoriOptionalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(tmp)
        original = engine.ROOT
        shutil.copytree(original / 'templates', self.root / 'templates')
        shutil.copytree(original / 'demo', self.root / 'demo')
        shutil.copy(original / 'settings.json', self.root / 'settings.json')
        self.data = self.root / 'data'
        (self.data / 'reels').mkdir(parents=True)
        self.stack.enter_context(patch.object(engine, 'ROOT', self.root))
        self.stack.enter_context(patch.object(engine, 'DATA', self.data))
        self.stack.enter_context(patch.object(engine, 'OUT', self.data / 'reels'))
        product_editor.prepare_store()

    def test_full_platform_workflow_without_official_factori_api(self):
        # 1. Modular FactoriApiAdapter raises clear optional guidance if explicitly invoked without keys.
        with self.assertRaisesRegex(AgentConfigurationError, 'not required'):
            list(FactoriApiAdapter().fetch_pages())

        img_bytes = _sample_photo_bytes()
        b64_img = base64.b64encode(img_bytes).decode()

        # 2. Import product catalogue directly from Excel (.xlsx) WITHOUT Factori API, preserving ₹35 positive margin.
        xlsx_products = write_xlsx_sheets([
            ('Products', [
                ['sku', 'name', 'category', 'price', 'cost', 'fees', 'stock', 'product_url', 'images', 'media_ok'],
                ['XLS-BAG-1', 'Canvas Tote Bag', 'Bags', 250, 180, 35, 10, 'https://prodxmart.com/product/canvas-tote', 'https://cdn.example.com/tote.jpg', 'yes'],
            ])
        ])
        tracker = {'calls': 0, 'prompts': [], 'fail_count': 0}
        orch = MasterOrchestrator(ollama_factory=lambda cfg: FakeOllamaClient(cfg, tracker))
        orch.update_config({'retry_delay_seconds': 0.02})
        orch.start()
        self.addCleanup(orch.stop)

        with patch('product_editor.photo_bytes', return_value=img_bytes), patch(
            'product_editor.store_photos', return_value={'images': ['https://cdn.example.com/tote.jpg'], 'identifiers': []}
        ):
            cat_run = orch.enqueue_run(
                'catalog_agent',
                mode='store',
                payload={'source': 'csv', 'xlsx_base64': base64.b64encode(xlsx_products).decode()},
            )
            cat_done = wait_until(lambda: (r := orch.storage.get_run(cat_run['id'])) and r['status'] == 'succeeded' and r, timeout=20.0)
        self.assertEqual(cat_done['output']['data']['stats']['imported_products'], 1)
        cat_prod = engine.catalogue('store')['XLS-BAG-1']
        self.assertEqual(float(cat_prod['margin']), 35.0)
        self.assertEqual(cat_prod['issues'], [])
        fresh_after_xlsx = orch.storage.get_catalog_freshness_summary(mode='store', max_age_hours=72)
        self.assertEqual(fresh_after_xlsx['data_source'], 'CSV / Excel import')

        # 3. Run ALL remaining orchestrator agents end-to-end without Factori API access.
        with patch('product_editor.photo_bytes', return_value=img_bytes), patch(
            'product_editor.store_photos', return_value={'images': ['https://cdn.example.com/tote.jpg'], 'identifiers': []}
        ):
            master_run = orch.enqueue_run('master_orchestrator', mode='store')
            master_done = wait_until(lambda: (r := orch.storage.get_run(master_run['id'])) and r['status'] == 'succeeded' and r, timeout=45.0)
        self.assertEqual(len(master_done['output']['data']['executed_agents']), 8)

        # 4. Check Integrations Center & Freshness Indicators.
        code, int_raw = invoke_handler('GET', '/api/system/integrations?mode=store', orchestrator=orch)
        self.assertEqual(code, 200)
        int_data = json.loads(int_raw)
        self.assertEqual(int_data['integrations']['factori_official_api']['status'], 'optional_pending')
        self.assertEqual(int_data['integrations']['csv_excel_import']['status'], 'ready')
        self.assertEqual(int_data['integrations']['manual_catalogue']['status'], 'ready')
        fresh = int_data['catalog_freshness']
        self.assertEqual(fresh['data_source'], 'Manual / Local catalogue')
        self.assertTrue(bool(fresh['last_synced_at']))
        self.assertTrue(bool(fresh['last_successful_import_at']))
        self.assertEqual(fresh['image_availability']['with_images'], 1)

        # 5. Stale stock/price timestamp blocks publishing until re-verified (Requirement 7).
        vprods = orch.storage.list_video_productions(mode='store', limit=5)
        self.assertTrue(vprods)
        pid = vprods[0]['id']
        orch.storage.record_human_qa_review(
            production_id=pid,
            mode='store',
            decision='approved',
            checklist={'visual_quality': True, 'pronunciation': True, 'product_accuracy': True},
        )
        social_posts = orch.storage.list_social_posts(mode='store', limit=5)
        self.assertTrue(social_posts)
        post = social_posts[0]

        # Backdate _studio_verified_at past maximum_catalogue_age_hours (72h) so imported item is stale.
        csv_path = self.root / 'store' / 'catalogue.csv'
        with csv_path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fields = list(reader.fieldnames)
        stale_ts = str((engine.now() - timedelta(hours=120)).timestamp())
        for r in rows:
            r['_studio_verified_at'] = stale_ts
        with csv_path.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

        with self.assertRaisesRegex(ValueError, 'Reconfirm price and stock|Stock and price verification required'):
            approve_social_post(orch.storage, mode='store', post_id=post['id'])

        # 6. Financial CSV/Excel import (orders + returns + refunds + expenses + bank settlement CSV auto-detect).
        orders_csv = (
            'order_id,line_id,sku,delivered_units,returned_units,sales,refunds,net_product_cost,fees,campaign\n'
            'ST-101,1,XLS-BAG-1,2,1,500,250,180,35,REEL-TOTE\n'
        )
        settlements_csv = (
            'reference,settled_at,amount,notes\n'
            'UTR-AUTO-01,2026-11-25T12:00:00+05:30,215,Weekly bank payout\n'
        )
        import_financial_upload('store', csv_text=orders_csv, target='orders', storage=orch.storage)
        set_imp = import_financial_upload('store', csv_text=settlements_csv, target='auto', storage=orch.storage)
        self.assertEqual(set_imp['imported_settlements'], 1)
        analytics = build_sales_analytics('store', storage=orch.storage)
        self.assertTrue(analytics['reconciliation']['matches_engine'])
        self.assertEqual(analytics['summary']['net_sales'], 250.0)
        self.assertEqual(analytics['summary']['settlement_total'], 215.0)


if __name__ == '__main__':
    unittest.main()
