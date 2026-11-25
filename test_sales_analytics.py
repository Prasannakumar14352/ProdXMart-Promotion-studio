"""Financial reconciliation, duplicate prevention, settlement exclusion & Excel tests."""

import base64
from contextlib import ExitStack
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import product_editor
from agents.excel_io import read_xlsx_sheets, write_xlsx_sheets
from agents.orchestrator import MasterOrchestrator
from agents.sales_analytics import (
    add_expense_entry,
    build_sales_analytics,
    export_analytics_workbook,
    import_financial_upload,
)
from agents.storage import AgentStorage
from test_agents import FakeOllamaClient, invoke_handler


class SalesAnalyticsTests(unittest.TestCase):
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

    def test_reconciles_with_engine_and_excludes_settlements_from_revenue(self):
        demo_data = build_sales_analytics('demo')
        eng = engine.earnings('demo')
        self.assertTrue(demo_data['reconciliation']['matches_engine'])
        self.assertEqual(demo_data['summary']['orders'], eng['orders'])
        self.assertEqual(demo_data['summary']['net_units'], eng['units'])
        self.assertAlmostEqual(demo_data['summary']['net_sales'], eng['sales'], places=2)
        self.assertAlmostEqual(demo_data['summary']['operating_profit'], eng['profit'], places=2)
        # Demo orders have no campaign attribution -> ROAS must remain None rather than invented.
        self.assertIsNone(demo_data['summary']['roas'])

        # Adding a bank settlement in store mode must NOT alter net_sales or operating_profit.
        import json
        orch = MasterOrchestrator(ollama_factory=lambda cfg: FakeOllamaClient(cfg))
        orch.start()
        self.addCleanup(orch.stop)

        store_orders = (
            'order_id,line_id,sku,delivered_units,returned_units,sales,refunds,net_product_cost,fees,campaign\n'
            'ORD-1,1,SKU-1,2,0,1000,0,500,50,REEL-1\n'
        )
        imp = import_financial_upload('store', csv_text=store_orders, target='orders')
        self.assertEqual(imp['imported_orders'], 1)
        # Re-importing the exact same order skips duplicate cleanly.
        dup = import_financial_upload('store', csv_text=store_orders, target='orders')
        self.assertEqual(dup['imported_orders'], 0)
        self.assertEqual(dup['skipped_duplicate_orders'], 1)

        add_expense_entry('store', reference='AD-100', expense_type='Advertising', amount_val='200')
        storage = AgentStorage()
        storage.add_bank_settlement(mode='store', reference='NEFT-901', settled_at='2026-11-25T10:00:00+05:30', amount=950.0, notes='Payout')

        store_data = build_sales_analytics('store', storage=storage)
        self.assertTrue(store_data['reconciliation']['matches_engine'])
        self.assertEqual(store_data['summary']['net_sales'], 1000.0)
        self.assertEqual(store_data['summary']['operating_profit'], 250.0)  # 1000 - 500 - 50 - 200
        self.assertEqual(store_data['summary']['settlement_total'], 950.0)
        # Valid attributable revenue + ad spend -> exact ROAS = 1000 / 200 = 5.0
        self.assertEqual(store_data['summary']['roas'], 5.0)

        code, body = invoke_handler('GET', '/api/analytics?mode=store', orchestrator=orch)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['summary']['operating_profit'], 250.0)

    def test_xlsx_export_and_import_roundtrip(self):
        xlsx_bytes = export_analytics_workbook('demo')
        sheets = read_xlsx_sheets(xlsx_bytes)
        self.assertIn('Summary & Reconciliation', sheets)
        self.assertIn('Orders', sheets)
        self.assertIn('Expenses', sheets)
        self.assertEqual(len(sheets['Orders']), 2)

        # Upload workbook bytes directly into store mode.
        res = import_financial_upload(
            'store',
            filename='orders_export.xlsx',
            base64_data=base64.b64encode(xlsx_bytes).decode(),
        )
        self.assertEqual(res['imported_orders'], 2)
        self.assertEqual(res['imported_expenses'], 1)

        store_data = build_sales_analytics('store')
        self.assertTrue(store_data['reconciliation']['matches_engine'])
        self.assertAlmostEqual(store_data['summary']['operating_profit'], build_sales_analytics('demo')['summary']['operating_profit'], places=2)


if __name__ == '__main__':
    unittest.main()
