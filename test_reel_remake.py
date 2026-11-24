from contextlib import closing, ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import engine


class RemakeTest(TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(TemporaryDirectory()))
        stack.enter_context(patch.object(engine, 'DATA', root))
        stack.enter_context(patch.object(engine, 'OUT', root))

        def render(product, target, demo):
            target.write_bytes(b'new reel')

        self.renderer = stack.enter_context(patch.object(engine, 'render', side_effect=render))
        engine.batch('demo')
        with closing(engine.db()) as con:
            self.job = con.execute('SELECT * FROM jobs LIMIT 1').fetchone()
        self.jid = self.job['id']
        self.original = root / (self.jid + '.mp4')
        self.original.write_bytes(b'original reel')

    def test_existing_draft_keeps_its_campaign_and_slot(self):
        engine.remake('demo', self.jid)
        with closing(engine.db()) as con:
            job = con.execute('SELECT * FROM jobs WHERE id=?', (self.jid,)).fetchone()
            for field in ('status', 'week', 'link', 'sku'):
                self.assertEqual(job[field], self.job[field])
            self.assertEqual(engine.video_path(con, self.jid).read_bytes(), b'new reel')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 3)
        self.assertFalse(self.original.exists())

    def test_failed_render_preserves_previous_video(self):
        self.renderer.side_effect = RuntimeError('render failed')
        with self.assertRaisesRegex(RuntimeError, 'render failed'):
            engine.remake('demo', self.jid)
        with closing(engine.db()) as con:
            self.assertEqual(engine.video_path(con, self.jid).read_bytes(), b'original reel')

    def test_scheduled_reel_is_protected(self):
        with closing(engine.db()) as con, con:
            con.execute("UPDATE jobs SET status='Scheduled' WHERE id=?", (self.jid,))
        with self.assertRaisesRegex(ValueError, 'Only a draft'):
            engine.remake('demo', self.jid)
