"""Focused tests for product-scoped automatic preparation; no external publishing."""
import unittest
from unittest.mock import Mock, patch
from agents.base import AgentConfigurationError, AgentExecutionError
from agents.promotion_workflow import validate_preferences, promotion_payload, prepare_product_promotion


class ProductPromotionTests(unittest.TestCase):
    def test_preferences_reject_empty_targets(self):
        with self.assertRaises(AgentConfigurationError):
            validate_preferences('en', 30, [])

    @patch('agents.promotion_workflow.engine.signature', return_value='snapshot')
    @patch('agents.promotion_workflow.engine.catalogue')
    def test_deterministic_key_and_eligibility(self, catalogue, signature):
        catalogue.return_value = {'SKU': {'issues': []}}
        a = promotion_payload('SKU', 'en', 30, ['youtube_shorts', 'instagram_reels'])
        b = promotion_payload('SKU', 'en', 30, ['instagram_reels', 'youtube_shorts'])
        self.assertEqual(a['promotion_key'], b['promotion_key'])
        catalogue.return_value['SKU']['issues'] = ['Low margin']
        with self.assertRaises(AgentConfigurationError):
            promotion_payload('SKU', 'en', 30, ['youtube_shorts'])

    @patch('agents.promotion_workflow.engine.signature', return_value='snapshot')
    @patch('agents.promotion_workflow.engine.catalogue', return_value={'SKU': {'issues': []}})
    def test_exact_handoffs_and_qa_gate(self, catalogue, signature):
        ctx = Mock()
        ctx.mode = 'store'
        ctx.payload = promotion_payload('SKU', 'en', 30, ['youtube_shorts'])
        storage = Mock()
        storage.get_agent_configs.return_value = {a: {'enabled': True} for a in
            ('marketing_agent', 'video_agent', 'qa_agent', 'social_agent')}
        def output(data):
            return {'status': 'succeeded', 'id': 'run', 'output': {'data': data}}
        ctx.execute_child.side_effect = [
            output({'video_handoff': {'campaign_id': 'C'}}),
            output({'qa_handoff': {'production_id': 'V'}}),
            output({'failed_count': 0, 'reports': [{'production_id': 'V', 'automated_passed': True}]}),
            output({'posts': [{'id': 'P'}]})]
        result = prepare_product_promotion(ctx, storage)
        self.assertEqual(result.data['production_id'], 'V')
        self.assertEqual(ctx.execute_child.call_args_list[-1].args[1]['production_id'], 'V')
        self.assertEqual(ctx.execute_child.call_count, 4)
        ctx.execute_child.reset_mock()
        ctx.execute_child.side_effect = [
            output({'video_handoff': {'campaign_id': 'C'}}),
            output({'qa_handoff': {'production_id': 'V'}}),
            output({'failed_count': 1, 'reports': []})]
        with self.assertRaises(AgentExecutionError):
            prepare_product_promotion(ctx, storage)
        self.assertEqual(ctx.execute_child.call_count, 3)


if __name__ == '__main__':
    unittest.main()
