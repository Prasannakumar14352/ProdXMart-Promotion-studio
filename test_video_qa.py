from array import array
from contextlib import ExitStack, closing
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import creative
import engine
import publisher
from agents import (
    MasterOrchestrator,
    QualityAssuranceAgent,
    VideoProductionAgent,
    VoiceoverProvider,
    inspect_video_with_ffprobe,
)
from agents.registry import CatalogueAuditorAgent, PerformanceAnalystAgent, ReelCopyStrategistAgent
from agents.catalog_agent import CatalogAgent
from agents.marketing_agent import MarketingAgent
from test_agents import FakeOllamaClient, invoke_handler, wait_until


def write_test_tone_wav(path: Path, seconds: float = 2.0, freq: float = 440.0, amplitude: float = 0.6) -> None:
    rate = 48000
    n = int(rate * seconds)
    samples = array('h', (int(32767 * amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    with wave.open(str(path), 'wb') as wf:
        wf.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wf.writeframes(samples.tobytes())


class StubVoiceoverProvider(VoiceoverProvider):
    provider_id = 'stub_tts'

    def supports_language(self, language: str) -> bool:
        return language in ('en', 'te', 'bilingual')

    def synthesize_to_wav(self, text: str, *, language: str, out_wav: Path) -> bool:
        write_test_tone_wav(out_wav, seconds=1.8, freq=520.0, amplitude=0.7)
        return True


class VideoAndQaTests(unittest.TestCase):
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

    def _new_orchestrator(self, *, voiceover_provider=None):
        tracker = {'calls': 0, 'prompts': [], 'fail_count': 0}
        specialists = [
            CatalogAgent(),
            CatalogueAuditorAgent(),
            ReelCopyStrategistAgent(),
            PerformanceAnalystAgent(),
            MarketingAgent(),
            VideoProductionAgent(voiceover_provider=voiceover_provider),
            QualityAssuranceAgent(),
        ]
        orch = MasterOrchestrator(
            specialists=specialists,
            ollama_factory=lambda cfg: FakeOllamaClient(cfg, tracker),
        )
        orch.update_config({'retry_delay_seconds': 0.01})
        orch.start()
        self.addCleanup(orch.stop)
        return orch

    def test_video_generation_15s_single_and_30s_multiproduct_with_qa_and_ducking(self):
        orch = self._new_orchestrator(voiceover_provider=StubVoiceoverProvider())

        # 1. Generate 15s Telugu video for D01 with stub TTS + music ducking + QA verification.
        v1 = orch.enqueue_run(
            'video_agent',
            mode='demo',
            payload={'skus': ['D01'], 'duration_seconds': 15, 'language': 'te', 'duck_music': True},
        )
        d1 = wait_until(lambda: (r := orch.storage.get_run(v1['id'])) and r['status'] == 'succeeded' and r, timeout=25.0)
        prod1 = d1['output']['data']['production']
        self.assertEqual(prod1['width'], 1080)
        self.assertEqual(prod1['height'], 1920)
        self.assertEqual(prod1['voiceover_status'], 'tts_generated')
        self.assertTrue(prod1['ducking_applied'])
        self.assertTrue(Path(prod1['video_path']).is_file())

        q1 = orch.enqueue_run('qa_agent', mode='demo', payload={'production_id': prod1['production_id']})
        qd1 = wait_until(lambda: (r := orch.storage.get_run(q1['id'])) and r['status'] == 'succeeded' and r, timeout=15.0)
        rep1 = qd1['output']['data']['reports'][0]
        self.assertTrue(rep1['automated_passed'], rep1['failures'])
        self.assertEqual(rep1['overall_status'], 'needs_human_review')
        self.assertFalse(rep1['publish_allowed'])
        self.assertTrue(rep1['checks']['audio_stream'])
        self.assertTrue(rep1['checks']['audible_audio'])
        self.assertEqual(rep1['probe']['video_stream']['width'], 1080)
        self.assertEqual(rep1['probe']['video_stream']['height'], 1920)
        self.assertEqual(rep1['probe']['audio_stream']['codec'], 'aac')

        # 2. Generate 30s multi-product English video (D02 + D03) with recorded audio file.
        rec_wav = self.root / 'recorded.wav'
        write_test_tone_wav(rec_wav, seconds=4.0, freq=330.0, amplitude=0.5)
        v2 = orch.enqueue_run(
            'video_agent',
            mode='demo',
            payload={
                'skus': ['D02', 'D03'],
                'duration_seconds': 30,
                'language': 'en',
                'voiceover_mode': 'recorded',
                'recorded_audio_path': str(rec_wav),
            },
        )
        d2 = wait_until(lambda: (r := orch.storage.get_run(v2['id'])) and r['status'] == 'succeeded' and r, timeout=35.0)
        prod2 = d2['output']['data']['production']
        self.assertEqual(prod2['skus'], ['D02', 'D03'])
        self.assertEqual(prod2['voiceover_status'], 'recorded_audio')
        self.assertGreaterEqual(prod2['scene_count'], 6)

        q2 = orch.enqueue_run('qa_agent', mode='demo', payload={'production_id': prod2['production_id']})
        qd2 = wait_until(lambda: (r := orch.storage.get_run(q2['id'])) and r['status'] == 'succeeded' and r, timeout=15.0)
        rep2 = qd2['output']['data']['reports'][0]
        self.assertTrue(rep2['automated_passed'], rep2['failures'])
        self.assertAlmostEqual(rep2['probe']['duration_seconds'], 30.0, delta=1.0)

        # Human review endpoint enforces all 3 checklist items before approving.
        code, bad_rev = invoke_handler(
            'POST', '/agents/qa/review', orchestrator=orch,
            body={'mode': 'demo', 'production_id': prod1['production_id'], 'decision': 'approved',
                  'checklist': {'visual_quality': True, 'pronunciation': False, 'product_accuracy': True}},
        )
        self.assertEqual(code, 400)

        code, ok_rev = invoke_handler(
            'POST', '/agents/qa/review', orchestrator=orch,
            body={'mode': 'demo', 'production_id': prod1['production_id'], 'decision': 'approved',
                  'checklist': {'visual_quality': True, 'pronunciation': True, 'product_accuracy': True},
                  'note': 'Verified look & Telugu audio'},
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(ok_rev)['production']['human_review_status'], 'approved')

    def test_qa_detects_missing_audio_silent_audio_price_mismatch_and_blocks_publishing(self):
        orch = self._new_orchestrator()
        ff = creative.ffmpeg()

        # Create a 720x1280 video-only MP4 (reproducing the legacy silent-reel bug).
        no_audio_mp4 = engine.OUT / 'legacy_no_audio.mp4'
        subprocess.run(
            [
                ff, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=green:s=720x1280:d=5',
                '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(no_audio_mp4),
            ],
            check=True,
            timeout=30,
        )
        probe_no_audio = inspect_video_with_ffprobe(no_audio_mp4)
        self.assertFalse(probe_no_audio['ok'])
        self.assertFalse(probe_no_audio['checks']['audio_stream'])
        self.assertTrue(any('missing audio' in f.lower() for f in probe_no_audio['failures']))

        # Create a 1080x1920 MP4 with a completely silent AAC audio track.
        silent_mp4 = engine.OUT / 'silent_track.mp4'
        subprocess.run(
            [
                ff, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:d=5',
                '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
                '-t', '5', '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(silent_mp4),
            ],
            check=True,
            timeout=30,
        )
        probe_silent = inspect_video_with_ffprobe(silent_mp4, expected_duration=5)
        self.assertFalse(probe_silent['ok'])
        self.assertTrue(probe_silent['checks']['audio_stream'])
        self.assertFalse(probe_silent['checks']['audible_audio'])
        self.assertTrue(any('silent' in f.lower() for f in probe_silent['failures']))

        # Price mismatch check in QualityAssuranceAgent.
        demo_p = engine.catalogue('demo')['D01']
        bad_prod = orch.storage.save_video_production(
            production_id='badprod0000000001',
            run_id='run1',
            mode='demo',
            campaign_id='',
            skus=['D01'],
            duration_seconds=5,
            language='te',
            video_path=str(silent_mp4),
            metadata={
                'width': 1080,
                'height': 1920,
                'category_layouts': ['stationery'],
                'products': [{'sku': 'D01', 'price': float(demo_p['price']) + 100.0}],
                'scenes': [{'caption_text': 'Hook', 'hook_text': 'Hook', 'cta_text': 'prodxmart.com'}],
            },
        )
        qa_run = orch.enqueue_run('qa_agent', mode='demo', payload={'production_id': bad_prod['id']})
        qa_done = wait_until(lambda: (r := orch.storage.get_run(qa_run['id'])) and r['status'] == 'succeeded' and r, timeout=15.0)
        rep = qa_done['output']['data']['reports'][0]
        self.assertFalse(rep['automated_passed'])
        self.assertFalse(rep['publish_allowed'])
        self.assertFalse(rep['checks']['price_accuracy'])
        self.assertFalse(rep['checks']['audible_audio'])

        # Publisher gate MUST hold a scheduled job whose video has missing/silent audio!
        past_due = (engine.now()).isoformat()
        with closing(engine.db()) as con, con:
            con.execute(
                '''INSERT INTO jobs (id, mode, week, sku, name, snapshot, caption, link, status, due, created)
                   VALUES (?, 'demo', '2026-W48', ?, ?, ?, ?, ?, 'Scheduled', ?, ?)''',
                (
                    'legacy_no_audio',
                    demo_p['sku'],
                    demo_p['name'],
                    engine.signature(demo_p),
                    engine.reel_caption(demo_p, 'demo'),
                    demo_p['product_url'],
                    past_due,
                    past_due,
                ),
            )
        publisher.run_due()
        with closing(engine.db()) as con:
            held_job = con.execute("SELECT status, note FROM jobs WHERE id='legacy_no_audio'").fetchone()
            self.assertEqual(held_job['status'], 'Held')
            self.assertIn('QA blocked', held_job['note'])

    def test_60_second_video_and_regeneration_http(self):
        orch = self._new_orchestrator()
        v60 = orch.enqueue_run(
            'video_agent',
            mode='demo',
            payload={'skus': ['D04'], 'duration_seconds': 60, 'language': 'te', 'voiceover_mode': 'music_only'},
        )
        d60 = wait_until(lambda: (r := orch.storage.get_run(v60['id'])) and r['status'] == 'succeeded' and r, timeout=45.0)
        prod60 = d60['output']['data']['production']
        probe60 = inspect_video_with_ffprobe(Path(prod60['video_path']), expected_duration=60)
        self.assertTrue(probe60['ok'], probe60['failures'])
        self.assertAlmostEqual(probe60['duration_seconds'], 60.0, delta=1.0)

        # Regenerate to 15s via HTTP endpoint.
        code, regen_raw = invoke_handler(
            'POST', '/agents/video/regenerate', orchestrator=orch,
            body={'mode': 'demo', 'production_id': prod60['production_id'], 'duration_seconds': 15, 'language': 'en'},
        )
        self.assertEqual(code, 200)
        regen_run_id = json.loads(regen_raw)['run']['id']
        wait_until(lambda: (r := orch.storage.get_run(regen_run_id)) and r['status'] == 'succeeded', timeout=25.0)
        updated_probe = inspect_video_with_ffprobe(Path(prod60['video_path']), expected_duration=15)
        self.assertTrue(updated_probe['ok'], updated_probe['failures'])


if __name__ == '__main__':
    unittest.main()
