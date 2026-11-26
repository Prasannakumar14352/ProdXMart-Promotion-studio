"""Regression tests for Telugu Unicode RAQM text shaping, mixed-script rendering, cluster wrapping, and DLL setup."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont, features

import creative
import engine
from agents.marketing_schema import build_fallback_product_marketing, validate_product_marketing_item
from agents.qa_agent import inspect_video_with_ffprobe
import telugu_support
from telugu_support import (
    TeluguShapingError,
    contains_telugu,
    get_telugu_rendering_status,
    segment_script_runs,
    split_text_clusters,
)
from test_agents import invoke_handler, wait_until


class TeluguRenderingTests(unittest.TestCase):
    def test_raqm_harfbuzz_fribidi_and_noto_font_active(self):
        status = get_telugu_rendering_status()
        self.assertTrue(status['raqm'], status)
        self.assertTrue(status['harfbuzz'], status)
        self.assertTrue(status['fribidi'], status)
        self.assertTrue(status['font_ok'], status)
        self.assertTrue(status['ready'], status)
        self.assertTrue(features.check('raqm'))
        self.assertTrue(features.check('harfbuzz'))
        self.assertTrue(features.check('fribidi'))
        te_face = creative.font(48, telugu=True)
        self.assertEqual(te_face.layout_engine, ImageFont.Layout.RAQM)

    def test_refuses_silently_rendering_telugu_with_basic_layout(self):
        img = Image.new('RGB', (400, 200), '#143E36')
        draw = ImageDraw.Draw(img)
        with patch.object(telugu_support, 'is_raqm_ready', return_value=False):
            with self.assertRaisesRegex(TeluguShapingError, 'Layout\\.BASIC'):
                creative.copy_block(draw, 'మరిన్ని ఫోటోలు చూడండి.', (20, 20), 360, 120, 42, '#FFFFFF', telugu=True)
            with self.assertRaises(TeluguShapingError):
                creative.font(42, telugu=True)

    def test_mixed_script_runs_and_cluster_truncation_preserve_syllables(self):
        mixed = 'మరిన్ని వివరాల కోసం ProdXMart వెబ్సైట్ చూడండి. ధర ₹499!'
        self.assertTrue(contains_telugu(mixed))
        runs = segment_script_runs(mixed)
        self.assertTrue(any('ProdXMart' in seg and not is_te for seg, is_te in runs), runs)
        self.assertTrue(any('వెబ్సైట్' in seg and is_te for seg, is_te in runs), runs)
        self.assertTrue(any('₹499' in seg and not is_te for seg, is_te in runs), runs)

        # Verify cluster splitting keeps conjuncts intact (never leaves trailing halant before ellipsis).
        sample = 'మరిన్ని ఫోటోలు చూడండి స్టైల్కి కొత్త'
        clusters = split_text_clusters(sample)
        self.assertIn('న్ని', clusters)
        self.assertIn('స్టై', clusters)
        self.assertIn('ల్కి', clusters)
        self.assertIn('త్త', clusters)

        img = Image.new('RGB', (600, 140), '#143E36')
        draw = ImageDraw.Draw(img)
        creative.copy_block(
            draw,
            'మరిన్ని వివరాల కోసం ProdXMart వెబ్సైట్ చూడండి. మీకు నచ్చితే ఇప్పుడే ఆర్డర్ చేయండి.',
            (20, 20),
            260,
            90,
            42,
            '#FFFFFF',
            telugu=True,
        )
        # Ensure non-empty pixels were drawn on the canvas.
        bbox = img.getbbox()
        self.assertIsNotNone(bbox)

    def test_english_rendering_and_telugu_sample_video_pass_qa(self):
        demo = engine.catalogue('demo')
        prod = demo['D01']
        test_sentences = [
            'మరిన్ని ఫోటోలు చూడండి.',
            'మరిన్ని వివరాల కోసం ProdXMart వెబ్సైట్ చూడండి.',
            'మీకు నచ్చితే ఇప్పుడే ఆర్డర్ చేయండి.',
            'మీ స్టైల్కి కొత్త లుక్!',
        ]
        scenes = []
        for idx, sentence in enumerate(test_sentences):
            img = creative.render_scene_card(
                prod,
                scene_index=idx,
                total_scenes=len(test_sentences),
                demo=True,
                hook_text='మీ స్టైల్కి కొత్త లుక్!' if idx == 0 else sentence,
                heading_text=prod['name'] if idx in (0, 2) else sentence,
                caption_text=sentence,
                cta_text='మీకు నచ్చితే ఇప్పుడే ఆర్డర్ చేయండి.' if idx == len(test_sentences) - 1 else '',
                image_index=idx,
            )
            self.assertEqual(img.size, (1080, 1920))
            scenes.append({'image': img, 'duration_seconds': 2})

        with tempfile.TemporaryDirectory() as tmp:
            out_mp4 = Path(tmp) / 'telugu_test.mp4'
            info = creative.render_composed_video(
                scene_specs=scenes,
                music_wav=None,
                narration_wav=None,
                outfile=out_mp4,
            )
            self.assertEqual(info['width'], 1080)
            self.assertEqual(info['height'], 1920)
            probe = inspect_video_with_ffprobe(out_mp4, expected_duration=8)
            self.assertTrue(probe['ok'], probe['failures'])

        # Verify natural conversational Telugu fallback copy passes schema + grounding validation.
        fact = {
            'sku': prod['sku'],
            'name': prod['name'],
            'category': prod['category'],
            'price_inr': f"{prod['price']:,.2f}".removesuffix('.00'),
            'price_numeric': float(prod['price']),
            'feature_te': 'రోజూ వాడటానికి బాగుండే డిజైన్',
            'product_url': prod['product_url'],
            'photo_count': len(prod['files']),
        }
        fb = build_fallback_product_marketing(fact, campaign_type='single_product', language='te')
        validated = validate_product_marketing_item(fb, fact, language='te')
        self.assertIn(prod['product_url'], validated['call_to_action'])
        self.assertTrue(contains_telugu(validated['opening_hook']))


if __name__ == '__main__':
    unittest.main()
