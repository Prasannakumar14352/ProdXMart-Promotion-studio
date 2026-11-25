from contextlib import ExitStack
import base64
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

import engine
import product_editor
from agents import (
    AgentConfigurationError,
    CatalogAgent,
    FactoriApiAdapter,
    MasterOrchestrator,
)
from agents.ollama_client import OllamaConfig
from test_agents import FakeOllamaClient, invoke_handler, wait_until


def make_jpeg_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (64, 64), color).save(buf, format='JPEG', quality=90)
    return buf.getvalue()


class CatalogAgentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        original = engine.ROOT
        shutil.copytree(original / 'templates', self.root / 'templates')
        shutil.copytree(original / 'demo', self.root / 'demo')
        shutil.copy(original / 'settings.json', self.root / 'settings.json')
        self.data = self.root / 'data'
        (self.data / 'reels').mkdir(parents=True)
        self.stack.enter_context(patch.object(engine, 'ROOT', self.root))
        self.stack.enter_context(patch.object(engine, 'DATA', self.data))
        self.stack.enter_context(patch.object(engine, 'OUT', self.data / 'reels'))

        self.img_red = make_jpeg_bytes((200, 20, 20))
        self.img_blue = make_jpeg_bytes((20, 20, 200))
        self.img_green = make_jpeg_bytes((20, 180, 20))

    def _new_orchestrator(self, *, catalog_agent=None):
        tracker = {'calls': 0, 'prompts': [], 'fail_count': 0}
        from agents.registry import CatalogueAuditorAgent, PerformanceAnalystAgent, ReelCopyStrategistAgent

        specialists = [
            catalog_agent or CatalogAgent(),
            CatalogueAuditorAgent(),
            ReelCopyStrategistAgent(),
            PerformanceAnalystAgent(),
        ]
        orch = MasterOrchestrator(
            specialists=specialists,
            ollama_factory=lambda cfg: FakeOllamaClient(cfg, tracker),
        )
        orch.update_config({'retry_delay_seconds': 0.02})
        orch.start()
        self.addCleanup(orch.stop)
        return orch, tracker

    def test_multipage_beyond_32_pages_sku_variant_dedup_and_updates(self):
        # 45 pages exceeds the old 32-page assumption; includes duplicate SKU+variant and distinct variant.
        img_map = {'https://cdn.factori.com/p1.jpg': self.img_red}
        agent = CatalogAgent(
            store_photos_fn=lambda url, sku, name: {'images': []},
            photo_bytes_fn=lambda item: img_map[item['url']],
        )
        orch, _ = self._new_orchestrator(catalog_agent=agent)

        pages = []
        for p in range(1, 46):
            cards = [{
                'sku': f'SKU{p:03d}',
                'variant': 'Standard',
                'name': f'Product {p}',
                'price': '499',
                'cost': '250',
                'stock': 10 + p,
                'product_url': f'https://prodxmart.com/product/p-{p}',
                'images': ['https://cdn.factori.com/p1.jpg'],
            }]
            if p == 1:
                cards.append({
                    'sku': 'SKU001',
                    'variant': 'Blue',
                    'name': 'Product 1 Blue',
                    'price': '549',
                    'cost': '260',
                    'stock': 7,
                    'product_url': 'https://prodxmart.com/product/p-1-blue',
                    'images': ['https://cdn.factori.com/p1.jpg'],
                })
            if p == 40:
                # Duplicate of page 1 (SKU001 + Standard) across distant pages.
                cards.append({
                    'sku': 'sku001',
                    'variant': 'standard',
                    'name': 'Product 1',
                    'price': '499',
                    'cost': '250',
                    'stock': 11,
                    'product_url': 'https://prodxmart.com/product/p-1',
                    'images': ['https://cdn.factori.com/p1.jpg'],
                })
            pages.append({'page_number': p, 'total_pages': 45, 'has_next': p < 45, 'cards': cards})

        run = orch.enqueue_run('catalog_agent', mode='store', payload={'source': 'extension', 'pages': pages})
        done = wait_until(lambda: (r := orch.storage.get_run(run['id'])) and r['status'] == 'succeeded' and r, timeout=15.0)
        stats = done['output']['data']['stats']
        self.assertEqual(stats['pages_scanned'], 45)
        self.assertEqual(stats['deduplicated_count'], 1)
        self.assertEqual(stats['imported_products'], 46)
        self.assertEqual(stats['updated_products'], 0)
        self.assertEqual(stats['failed_products'], 0)
        self.assertEqual(stats['missing_images'], 0)

        catalogue = engine.catalogue('store')
        self.assertIn('SKU001-STANDARD', catalogue)
        self.assertIn('SKU001-BLUE', catalogue)
        self.assertIn('SKU045', catalogue)

        # Second sync updates existing items without creating duplicates.
        run2 = orch.enqueue_run('catalog_agent', mode='store', payload={'source': 'extension', 'pages': pages[:2]})
        done2 = wait_until(lambda: (r := orch.storage.get_run(run2['id'])) and r['status'] == 'succeeded' and r)
        stats2 = done2['output']['data']['stats']
        self.assertEqual(stats2['imported_products'], 0)
        self.assertEqual(stats2['updated_products'], 3)

    def test_ordered_images_dedup_retry_and_separate_diagnostics(self):
        attempts = {'flaky': 0}

        def fake_photo_bytes(item):
            url = item['url']
            if url.endswith('flaky.jpg'):
                attempts['flaky'] += 1
                if attempts['flaky'] < 2:
                    raise ValueError('Could not reach the image host or finish the download.')
                return self.img_green
            if url.endswith('404.jpg'):
                raise ValueError('The host returned HTTP 404.')
            if url.endswith('red.jpg') or url.endswith('red-dup.jpg'):
                return self.img_red
            if url.endswith('blue.jpg'):
                return self.img_blue
            raise ValueError('Unexpected test URL')

        def fake_store_photos(url, sku, name):
            if 'good' in url:
                return {
                    'images': [
                        'https://cdn.factori.com/red.jpg',
                        'https://cdn.factori.com/blue.jpg',
                        'https://cdn.factori.com/red-dup.jpg',  # byte-identical duplicate
                        'https://cdn.factori.com/flaky.jpg',    # transient error then succeeds
                        'https://cdn.factori.com/404.jpg',      # permanent error
                    ]
                }
            return {'images': []}

        agent = CatalogAgent(store_photos_fn=fake_store_photos, photo_bytes_fn=fake_photo_bytes)
        orch, _ = self._new_orchestrator(catalog_agent=agent)

        cards = [
            {
                'sku': 'GOOD-1',
                'name': 'Ordered Gallery Product',
                'price': '999',
                'cost': '500',
                'stock': 12,
                'product_url': 'https://prodxmart.com/product/good-1',
                'images': ['https://cdn.factori.com/red.jpg'],  # duplicate URL across sources
            },
            {
                'sku': '',
                'name': 'Nameless SKU Item',
                'price': '400',
                'cost': '200',
                'stock': 5,
                'product_url': 'https://prodxmart.com/product/no-sku',
            },
            {
                'sku': 'NOSTOCK-1',
                'name': 'Unknown Stock Item',
                'price': '450',
                'cost': '210',
                'stock': None,
                'product_url': 'https://prodxmart.com/product/nostock-1',
                'images': ['https://cdn.factori.com/blue.jpg'],
            },
            {
                'sku': 'NOPRICE-1',
                'name': 'Missing Cost Item',
                'price': '300',
                'cost': '',
                'stock': 4,
                'product_url': 'https://prodxmart.com/product/noprice-1',
            },
        ]

        run = orch.enqueue_run(
            'catalog_agent',
            mode='store',
            payload={'source': 'extension', 'cards': cards, 'image_retries': 2, 'image_retry_delay': 0.01},
        )
        done = wait_until(lambda: (r := orch.storage.get_run(run['id'])) and r['status'] == 'succeeded' and r)
        data = done['output']['data']
        stats = data['stats']
        diag = data['diagnostics']

        # Image order preserved (red, blue, green), byte/URL duplicates removed, flaky retried.
        self.assertEqual(attempts['flaky'], 2)
        saved_photos = product_editor.details('GOOD-1')['photos']
        self.assertEqual(len(saved_photos), 3)
        expected_hashes = [
            hashlib.sha256(product_editor.photo_bytes({'base64': base64.b64encode(b).decode('ascii')})).hexdigest() + '.jpg'
            for b in (self.img_red, self.img_blue, self.img_green)
        ]
        self.assertEqual([Path(p).name for p in saved_photos], expected_hashes)

        # Separate diagnostic reporting.
        self.assertEqual(len(diag['missing_sku']), 1)
        self.assertEqual(diag['missing_sku'][0]['name'], 'Nameless SKU Item')
        self.assertEqual(len(diag['unknown_stock']), 1)
        self.assertEqual(diag['unknown_stock'][0]['sku'], 'NOSTOCK-1')
        self.assertEqual(len(diag['missing_prices']), 1)
        self.assertEqual(diag['missing_prices'][0]['sku'], 'NOPRICE-1')
        self.assertEqual(len(diag['image_failures']), 1)
        self.assertEqual(diag['image_failures'][0]['url'], 'https://cdn.factori.com/404.jpg')

        self.assertEqual(stats['total_products'], 4)
        self.assertEqual(stats['imported_products'], 2)  # GOOD-1 + NOSTOCK-1 (held with stock=0)
        self.assertEqual(stats['failed_products'], 2)    # missing SKU + missing cost
        self.assertEqual(stats['missing_images'], 1)     # GOOD-1 had 1 failed image URL

        # Unknown stock is tracked as unknown in SQLite and held in catalogue safely.
        self.assertIn('Low stock', engine.catalogue('store')['NOSTOCK-1']['issues'])
        sync_row = orch.storage.get_catalog_sync_item('NOSTOCK-1::', mode='store')
        self.assertEqual(sync_row['stock_status'], 'unknown')

    def test_csv_import_and_replaceable_factori_api_adapter(self):
        agent = CatalogAgent(
            store_photos_fn=lambda url, sku, name: {'images': []},
            photo_bytes_fn=lambda item: self.img_red,
        )
        orch, _ = self._new_orchestrator(catalog_agent=agent)

        csv_text = (
            'product_name,sku,variant,selling_price,supplier_cost,available_units,url,image_urls,media_ok\n'
            'Khadi Tote,TOTE-1,Natural,650,320,9,https://prodxmart.com/product/khadi-tote,https://cdn.factori.com/tote.jpg,yes\n'
        )
        run = orch.enqueue_run('catalog_agent', mode='store', payload={'source': 'csv', 'csv_content': csv_text})
        done = wait_until(lambda: (r := orch.storage.get_run(run['id'])) and r['status'] == 'succeeded' and r)
        self.assertEqual(done['output']['data']['stats']['imported_products'], 1)
        self.assertEqual(engine.catalogue('store')['TOTE-1']['issues'], [])

        # Unconfigured Factori API adapter fails cleanly without requiring Factori API access.
        api_run = orch.enqueue_run('catalog_agent', mode='store', payload={'source': 'factori_api'})
        api_failed = wait_until(lambda: (r := orch.storage.get_run(api_run['id'])) and r['status'] == 'failed' and r)
        self.assertIn('not required', api_failed['error_message'].lower())

        # Replaceable FactoriApiAdapter with a custom paginated fetcher works identically through CatalogAgent.
        def mock_api(page):
            return {
                'total_pages': 2,
                'has_next': page < 2,
                'items': [{
                    'sku': f'API-{page}',
                    'name': f'API Product {page}',
                    'price': '750',
                    'cost': '400',
                    'stock': 8,
                    'product_url': f'https://prodxmart.com/product/api-{page}',
                    'images': ['https://cdn.factori.com/api.jpg'],
                }],
            }

        custom_adapter = FactoriApiAdapter(fetcher=mock_api)
        replaceable_agent = CatalogAgent(
            store_photos_fn=lambda url, sku, name: {'images': []},
            photo_bytes_fn=lambda item: self.img_red,
            adapter_factory=lambda source, **kw: custom_adapter if source == 'factori_api' else None,
        )
        orch2, _ = self._new_orchestrator(catalog_agent=replaceable_agent)
        run_api = orch2.enqueue_run('catalog_agent', mode='store', payload={'source': 'factori_api'})
        done_api = wait_until(lambda: (r := orch2.storage.get_run(run_api['id'])) and r['status'] == 'succeeded' and r)
        self.assertEqual(done_api['output']['data']['stats']['pages_scanned'], 2)
        self.assertEqual(done_api['output']['data']['stats']['imported_products'], 2)

    def test_freshness_tracking_and_http_staging_with_master_orchestrator(self):
        agent = CatalogAgent(
            store_photos_fn=lambda url, sku, name: {'images': []},
            photo_bytes_fn=lambda item: self.img_red,
        )
        orch, _ = self._new_orchestrator(catalog_agent=agent)

        # Stage multi-page batch via HTTP endpoint (does not auto-execute).
        stage_payload = {
            'mode': 'store',
            'source': 'extension',
            'pages_count': 2,
            'cards': [{
                'sku': 'HTTP-1',
                'name': 'Staged Extension Product',
                'price': '599',
                'cost': '300',
                'stock': 15,
                'product_url': 'https://prodxmart.com/product/http-1',
                'images': ['https://cdn.factori.com/http1.jpg'],
                'media_ok': True,
                'page': 2,
            }],
        }
        code, staged_raw = invoke_handler('POST', '/agents/catalog/stage', orchestrator=orch, body=stage_payload)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(staged_raw)['staged']['pages_count'], 2)

        # Run Master Orchestrator via HTTP — runs catalog_agent first, then specialists!
        code, run_raw = invoke_handler('POST', '/agents/run', orchestrator=orch, body={'mode': 'store', 'agent_id': 'master_orchestrator'})
        self.assertEqual(code, 200)
        run_id = json.loads(run_raw)['run']['id']
        done = wait_until(lambda: (r := orch.storage.get_run(run_id)) and r['status'] == 'succeeded' and r)
        self.assertEqual(done['output']['data']['executed_agents'][0], 'catalog_agent')

        code, cat_status_raw = invoke_handler('GET', '/agents/catalog/status?mode=store', orchestrator=orch)
        self.assertEqual(code, 200)
        cat_status = json.loads(cat_status_raw)
        self.assertEqual(cat_status['freshness']['fresh'], 1)
        self.assertEqual(cat_status['items'][0]['sku'], 'HTTP-1')

        # Simulate stale stock timestamp older than 24h and verify freshness calculation reports stale.
        old_stamp = (engine.now() - timedelta(hours=30)).isoformat()
        orch.storage.upsert_catalog_sync_item(
            dedup_key='HTTP-1::',
            mode='store',
            sku='HTTP-1',
            base_sku='HTTP-1',
            variant='',
            name='Staged Extension Product',
            product_url='https://prodxmart.com/product/http-1',
            supplier_source='extension',
            price='599.00',
            cost='300.00',
            stock=15,
            stock_status='verified',
            last_synced_at=old_stamp,
            stock_verified_at=old_stamp,
            image_count=1,
            missing_images_count=0,
            last_run_id=run_id,
        )
        freshness = orch.storage.get_catalog_freshness_summary(mode='store', max_age_hours=24)
        self.assertEqual(freshness['stale'], 1)
        self.assertEqual(freshness['fresh'], 0)


if __name__ == '__main__':
    unittest.main()
