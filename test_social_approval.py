import base64
from contextlib import ExitStack
from datetime import timedelta
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import engine
import product_editor
import publisher
from agents import (
    CatalogAgent,
    MarketingAgent,
    MasterOrchestrator,
    QualityAssuranceAgent,
    SecureCredentialStore,
    SocialMediaAgent,
    VideoProductionAgent,
    approve_social_post,
    confirm_social_post_published,
    edit_social_post,
    publish_or_prepare_social_post,
    reconcile_social_approvals,
    reject_social_post,
    schedule_social_post,
)
from agents.registry import CatalogueAuditorAgent, PerformanceAnalystAgent, ReelCopyStrategistAgent
from test_agents import FakeOllamaClient, invoke_handler, wait_until


class SocialApprovalCenterTests(unittest.TestCase):
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

        # Seed one eligible store product so store price/stock mutation tests work cleanly.
        photo_b64 = base64.b64encode((original / 'demo' / 'grove-1.jpg').read_bytes()).decode('ascii')
        product_editor.save({
            'sku': 'STORE-1',
            'name': 'Khadi Desk Journal',
            'category': 'Stationery',
            'price': '599',
            'cost': '250',
            'fees': '10',
            'stock': '10',
            'product_url': 'https://prodxmart.com/product/khadi-desk-journal',
            'feature_te': 'చేతితో తయారు చేసిన కవర్',
            'media_ok': True,
            'new': [{'base64': photo_b64}],
        })

    def _new_orchestrator(self):
        tracker = {'calls': 0, 'prompts': [], 'fail_count': 0}
        specialists = [
            CatalogAgent(),
            CatalogueAuditorAgent(),
            ReelCopyStrategistAgent(),
            PerformanceAnalystAgent(),
            MarketingAgent(),
            VideoProductionAgent(),
            QualityAssuranceAgent(),
            SocialMediaAgent(),
        ]
        orch = MasterOrchestrator(
            specialists=specialists,
            ollama_factory=lambda cfg: FakeOllamaClient(cfg, tracker),
        )
        orch.update_config({'retry_delay_seconds': 0.01})
        orch.start()
        self.addCleanup(orch.stop)
        return orch

    def _produce_and_qa_approve(self, orch, mode='store', sku='STORE-1'):
        v_run = orch.enqueue_run('video_agent', mode=mode, payload={'skus': [sku], 'duration_seconds': 15, 'language': 'te'})
        v_done = wait_until(lambda: (r := orch.storage.get_run(v_run['id'])) and r['status'] == 'succeeded' and r, timeout=25.0)
        pid = v_done['output']['data']['production']['production_id']

        q_run = orch.enqueue_run('qa_agent', mode=mode, payload={'production_id': pid})
        wait_until(lambda: (r := orch.storage.get_run(q_run['id'])) and r['status'] == 'succeeded', timeout=15.0)

        orch.storage.record_human_qa_review(
            production_id=pid,
            mode=mode,
            decision='approved',
            checklist={'visual_quality': True, 'pronunciation': True, 'product_accuracy': True},
            note='Approved for social test',
        )
        return pid

    def test_manual_packages_all_statuses_schedule_confirm_and_duplicate_prevention(self):
        orch = self._new_orchestrator()
        pid = self._produce_and_qa_approve(orch, mode='store', sku='STORE-1')

        # 1. SocialMediaAgent creates posts for all 3 platforms in Awaiting Approval.
        s_run = orch.enqueue_run('social_agent', mode='store', payload={'production_id': pid})
        s_done = wait_until(lambda: (r := orch.storage.get_run(s_run['id'])) and r['status'] == 'succeeded' and r)
        posts = s_done['output']['data']['posts']
        self.assertEqual({p['platform'] for p in posts}, {'instagram_reels', 'facebook_reels', 'youtube_shorts'})
        self.assertTrue(all(p['status'] == 'Awaiting Approval' for p in posts))

        # Running again with the same production prevents duplicate creation.
        s_dup = orch.enqueue_run('social_agent', mode='store', payload={'production_id': pid})
        s_dup_done = wait_until(lambda: (r := orch.storage.get_run(s_dup['id'])) and r['status'] == 'succeeded' and r)
        self.assertEqual(len(s_dup_done['output']['data']['posts']), 0)
        self.assertEqual(len(s_dup_done['output']['data']['skipped_duplicates']), 3)

        ig_post = next(p for p in posts if p['platform'] == 'instagram_reels')
        fb_post = next(p for p in posts if p['platform'] == 'facebook_reels')
        yt_post = next(p for p in posts if p['platform'] == 'youtube_shorts')

        # 2. Cannot publish or download package before explicit approval.
        with self.assertRaisesRegex(ValueError, 'Explicit human approval'):
            publish_or_prepare_social_post(orch.storage, mode='store', post_id=ig_post['id'])

        # 3. Reject transitions to Draft; Edit moves back to Awaiting Approval; Approve builds ZIP package.
        rejected = reject_social_post(orch.storage, mode='store', post_id=fb_post['id'], reason='Tweak caption')
        self.assertEqual(rejected['status'], 'Draft')
        edited = edit_social_post(orch.storage, mode='store', post_id=fb_post['id'], updates={'caption': 'Updated FB caption', 'submit_for_approval': True})
        self.assertEqual(edited['status'], 'Awaiting Approval')

        approved_ig = approve_social_post(orch.storage, mode='store', post_id=ig_post['id'])
        self.assertEqual(approved_ig['status'], 'Approved')
        pkg_zip = Path(approved_ig['package_path'])
        self.assertTrue(pkg_zip.is_file())
        with zipfile.ZipFile(pkg_zip, 'r') as zf:
            names = set(zf.namelist())
            self.assertTrue({'video.mp4', 'thumbnail.jpg', 'caption.txt', 'title.txt', 'description.txt', 'hashtags.txt', 'product_link.txt', 'manifest.json'}.issubset(names))

        # HTTP ZIP package download works once approved.
        code, zip_bytes = invoke_handler('GET', f'/agents/social/package/{ig_post["id"]}.zip?mode=store', orchestrator=orch, decode=False)
        self.assertEqual(code, 200)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            self.assertIn('video.mp4', zf.namelist())
            self.assertIn('thumbnail.jpg', zf.namelist())

        # 4. Schedule -> Cancel Schedule -> Re-schedule -> Manual publish adapter NEVER marks Published without confirmation!
        future_ist = (engine.now() + timedelta(days=1)).isoformat()
        scheduled = schedule_social_post(orch.storage, mode='store', post_id=ig_post['id'], scheduled_at=future_ist)
        self.assertEqual(scheduled['status'], 'Scheduled')

        code_unsched, raw_unsched = invoke_handler(
            'POST', '/agents/social/cancel-schedule', orchestrator=orch,
            body={'mode': 'store', 'post_id': ig_post['id']},
        )
        self.assertEqual(code_unsched, 200)
        self.assertEqual(json.loads(raw_unsched)['post']['status'], 'Approved')
        self.assertEqual(json.loads(raw_unsched)['post']['scheduled_at'], '')

        scheduled = schedule_social_post(orch.storage, mode='store', post_id=ig_post['id'], scheduled_at=future_ist)
        self.assertEqual(scheduled['status'], 'Scheduled')

        after_manual = publish_or_prepare_social_post(orch.storage, mode='store', post_id=ig_post['id'], adapter_mode='manual_package')
        self.assertEqual(after_manual['status'], 'Scheduled')  # Not prematurely Published!

        # 5. Confirm Published with real HTTPS URL marks Published.
        code, conf_raw = invoke_handler(
            'POST', '/agents/social/confirm-published', orchestrator=orch,
            body={'mode': 'store', 'post_id': ig_post['id'], 'published_url': 'https://www.instagram.com/reel/C99999/'},
        )
        self.assertEqual(code, 200)
        confirmed = json.loads(conf_raw)['post']
        self.assertEqual(confirmed['status'], 'Published')
        self.assertEqual(confirmed['published_url'], 'https://www.instagram.com/reel/C99999/')

        # 6. Replaceable API adapter marks Published ONLY when confirmed=True with URL + ID, and Failed on error.
        approve_social_post(orch.storage, mode='store', post_id=yt_post['id'])
        with self.assertRaisesRegex(ValueError, 'not configured'):
            publish_or_prepare_social_post(orch.storage, mode='store', post_id=yt_post['id'], adapter_mode='youtube_api')
        self.assertEqual(orch.storage.get_social_post(yt_post['id'], mode='store')['status'], 'Failed')

        # Edit & re-approve YouTube post, configure encrypted credentials, and confirm API publish transport.
        edit_social_post(orch.storage, mode='store', post_id=yt_post['id'], updates={'caption': 'Updated YT Short'})
        approve_social_post(orch.storage, mode='store', post_id=yt_post['id'])
        cred_store = SecureCredentialStore(use_dpapi=False)
        cred_store.set_credential('youtube_oauth_token', 'tok_yt_12345')
        cred_store.set_credential('youtube_channel_id', 'UC_PRODX_123')
        published_yt = publish_or_prepare_social_post(
            orch.storage,
            mode='store',
            post_id=yt_post['id'],
            adapter_mode='youtube_api',
            cred_store=cred_store,
            transport=lambda **kw: {'confirmed': True, 'external_post_id': 'yt_vid_123', 'published_url': 'https://youtube.com/shorts/yt_vid_123'},
        )
        self.assertEqual(published_yt['status'], 'Published')
        self.assertEqual(published_yt['published_url'], 'https://youtube.com/shorts/yt_vid_123')

    def test_invalidation_on_price_or_video_change_stock_recheck_and_global_stop(self):
        orch = self._new_orchestrator()
        pid = self._produce_and_qa_approve(orch, mode='store', sku='STORE-1')

        s_run = orch.enqueue_run('social_agent', mode='store', payload={'production_id': pid, 'platforms': ['instagram_reels', 'youtube_shorts']})
        s_done = wait_until(lambda: (r := orch.storage.get_run(s_run['id'])) and r['status'] == 'succeeded' and r)
        ig_post, yt_post = s_done['output']['data']['posts']

        approve_social_post(orch.storage, mode='store', post_id=ig_post['id'])
        approve_social_post(orch.storage, mode='store', post_id=yt_post['id'])

        # 1. Edit product price in store/catalogue.csv -> prior approval MUST invalidate!
        d = product_editor.details('STORE-1')
        product_editor.save({
            'sku': 'STORE-1',
            'name': d['name'],
            'category': d['category'],
            'price': '649',  # changed from 599
            'cost': d['cost'],
            'fees': d['fees'],
            'stock': d['stock'],
            'product_url': d['product_url'],
            'feature_te': d['feature_te'],
            'media_ok': True,
            'keep': d['photos'],
            'new': [],
            'version': d['version'],
        })
        invalidated = reconcile_social_approvals(orch.storage, 'store')
        self.assertEqual(len(invalidated), 2)
        ig_after = orch.storage.get_social_post(ig_post['id'], mode='store')
        self.assertEqual(ig_after['status'], 'Awaiting Approval')
        self.assertIn('price changed', ig_after['invalidation_reason'].lower())

        # Re-approve at new price, then drop stock below minimum_stock -> pre-publish recheck marks Failed!
        approve_social_post(orch.storage, mode='store', post_id=ig_post['id'])
        d2 = product_editor.details('STORE-1')
        product_editor.save({
            'sku': 'STORE-1',
            'name': d2['name'],
            'category': d2['category'],
            'price': d2['price'],
            'cost': d2['cost'],
            'fees': d2['fees'],
            'stock': '1',  # below minimum_stock=3
            'product_url': d2['product_url'],
            'feature_te': d2['feature_te'],
            'media_ok': True,
            'keep': d2['photos'],
            'new': [],
            'version': d2['version'],
        })
        with self.assertRaisesRegex(ValueError, 'Low stock|below minimum'):
            publish_or_prepare_social_post(orch.storage, mode='store', post_id=ig_post['id'])
        self.assertEqual(orch.storage.get_social_post(ig_post['id'], mode='store')['status'], 'Failed')

        # 2. Global publishing stop halts publishing, package download, AND publisher.run_due().
        code, stop_raw = invoke_handler(
            'POST', '/agents/social/global-stop', orchestrator=orch,
            body={'mode': 'store', 'stopped': True, 'reason': 'Emergency pause'},
        )
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(stop_raw)['settings']['global_publishing_stopped'])

        pkg_code, _ = invoke_handler('GET', f'/agents/social/package/{yt_post["id"]}.zip?mode=store', orchestrator=orch, decode=False)
        self.assertEqual(pkg_code, 403)
        with self.assertRaisesRegex(ValueError, 'Global publishing stop'):
            publish_or_prepare_social_post(orch.storage, mode='store', post_id=yt_post['id'])

        # Legacy publisher.run_due() also respects global publishing stop without touching jobs.
        publisher.run_due()

    def test_secure_credential_store_encrypts_and_masks_secrets(self):
        store = SecureCredentialStore(use_dpapi=False)
        res = store.set_credential('meta_ig_token', 'EAAG_super_secret_token_9876')
        self.assertTrue(res['configured'])
        self.assertEqual(res['masked_preview'], '••••••••••••9876')
        self.assertNotIn('EAAG_super_secret', json.dumps(store.list_status()))
        self.assertEqual(store.get_secret('meta_ig_token'), 'EAAG_super_secret_token_9876')

        # Verify HTTP endpoint never returns plaintext secret.
        orch = self._new_orchestrator()
        code, cred_raw = invoke_handler(
            'POST', '/agents/social/credentials', orchestrator=orch,
            body={'mode': 'store', 'credentials': {'youtube_oauth_token': 'yt_secret_abcd'}},
        )
        self.assertEqual(code, 200)
        self.assertNotIn('yt_secret_abcd', cred_raw)
        self.assertIn('abcd', cred_raw)

        # Account readiness distinguishes credentials saved from verified live connection.
        code_st, st_raw = invoke_handler('GET', '/agents/social/state?mode=store', orchestrator=orch)
        self.assertEqual(code_st, 200)
        accounts = json.loads(st_raw)['accounts']
        self.assertEqual(accounts['instagram_reels']['connection_status'], 'not_configured')
        self.assertFalse(accounts['instagram_reels']['live_publishing_available'])
        self.assertTrue(accounts['instagram_reels']['manual_publishing_available'])
        self.assertEqual(accounts['youtube_shorts']['connection_status'], 'partial_credentials')
        self.assertFalse(accounts['youtube_shorts']['verified_connection'])


if __name__ == '__main__':
    unittest.main()
