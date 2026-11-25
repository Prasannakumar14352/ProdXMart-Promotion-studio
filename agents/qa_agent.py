"""Quality Assurance Agent: FFprobe stream/audio/silence verification, product accuracy checks and human review gate."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Optional
import uuid

import creative
import engine
from agents.base import (
    AgentConfigurationError,
    AgentContext,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)

_MEAN_VOL_RE = re.compile(r'mean_volume:\s*([-\d.]+)\s*dB')
_MAX_VOL_RE = re.compile(r'max_volume:\s*([-\d.]+)\s*dB')
_NSAMPLES_RE = re.compile(r'n_samples:\s*(\d+)')


def inspect_video_with_ffprobe(
    video_path: Path,
    *,
    expected_duration: Optional[int] = None,
    expected_width: int = 1080,
    expected_height: int = 1920,
) -> dict[str, Any]:
    """Inspect an MP4 using FFprobe and FFmpeg volumedetect for integrity, streams and audible audio."""
    path = Path(video_path)
    failures: list[str] = []

    if not path.is_file():
        return {
            'ok': False,
            'video_path': str(path),
            'file_size_bytes': 0,
            'duration_seconds': 0.0,
            'video_stream': None,
            'audio_stream': None,
            'audio_volume': None,
            'checks': {
                'file_integrity': False,
                'video_stream': False,
                'resolution': False,
                'duration': False,
                'audio_stream': False,
                'audible_audio': False,
            },
            'failures': ['Video file does not exist on disk.'],
        }

    size_bytes = path.stat().st_size
    if size_bytes < 4096:
        failures.append(f'Video file is too small or incomplete ({size_bytes} bytes).')

    probe_cmd = [
        creative.ffprobe(),
        '-v', 'error',
        '-show_format',
        '-show_streams',
        '-of', 'json',
        str(path),
    ]
    try:
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {
            'ok': False,
            'video_path': str(path),
            'file_size_bytes': size_bytes,
            'duration_seconds': 0.0,
            'video_stream': None,
            'audio_stream': None,
            'audio_volume': None,
            'checks': {
                'file_integrity': False,
                'video_stream': False,
                'resolution': False,
                'duration': False,
                'audio_stream': False,
                'audible_audio': False,
            },
            'failures': [f'FFprobe failed to run: {exc}'],
        }

    if probe_res.returncode != 0 or not probe_res.stdout.strip():
        return {
            'ok': False,
            'video_path': str(path),
            'file_size_bytes': size_bytes,
            'duration_seconds': 0.0,
            'video_stream': None,
            'audio_stream': None,
            'audio_volume': None,
            'checks': {
                'file_integrity': False,
                'video_stream': False,
                'resolution': False,
                'duration': False,
                'audio_stream': False,
                'audible_audio': False,
            },
            'failures': [f'Corrupted or unreadable video container: {probe_res.stderr.strip() or "ffprobe error"}'],
        }

    try:
        parsed = json.loads(probe_res.stdout)
    except ValueError as exc:
        return {
            'ok': False,
            'video_path': str(path),
            'file_size_bytes': size_bytes,
            'duration_seconds': 0.0,
            'video_stream': None,
            'audio_stream': None,
            'audio_volume': None,
            'checks': {
                'file_integrity': False,
                'video_stream': False,
                'resolution': False,
                'duration': False,
                'audio_stream': False,
                'audible_audio': False,
            },
            'failures': [f'Invalid FFprobe JSON output: {exc}'],
        }

    streams = parsed.get('streams') or []
    fmt = parsed.get('format') or {}
    v_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    a_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)

    file_integrity_ok = size_bytes >= 4096 and bool(streams)
    video_ok = bool(v_stream and v_stream.get('codec_name') == 'h264')
    if not v_stream:
        failures.append('Missing video stream.')
    elif v_stream.get('codec_name') != 'h264':
        failures.append(f'Expected H.264 video codec, found {v_stream.get("codec_name")}.')

    width = int(v_stream.get('width') or 0) if v_stream else 0
    height = int(v_stream.get('height') or 0) if v_stream else 0
    resolution_ok = width == expected_width and height == expected_height
    if v_stream and not resolution_ok:
        failures.append(f'Expected {expected_width}x{expected_height} vertical resolution, found {width}x{height}.')

    raw_dur = fmt.get('duration') or (v_stream.get('duration') if v_stream else None) or 0
    try:
        actual_duration = round(float(raw_dur), 2)
    except (TypeError, ValueError):
        actual_duration = 0.0

    duration_ok = actual_duration >= 1.0
    if not duration_ok:
        failures.append(f'Invalid video duration ({actual_duration}s).')
    elif expected_duration is not None and abs(actual_duration - float(expected_duration)) > 1.0:
        duration_ok = False
        failures.append(f'Duration mismatch: expected ~{expected_duration}s, found {actual_duration}s.')

    audio_stream_ok = bool(a_stream and a_stream.get('codec_name') == 'aac' and int(a_stream.get('sample_rate') or 0) >= 44100)
    if not a_stream:
        failures.append('Missing audio stream (video has no audio track).')
    elif a_stream.get('codec_name') != 'aac':
        failures.append(f'Expected AAC audio codec, found {a_stream.get("codec_name")}.')
    elif int(a_stream.get('sample_rate') or 0) < 44100:
        failures.append(f'Audio sample rate too low ({a_stream.get("sample_rate")} Hz).')

    audible_ok = False
    vol_info: dict[str, Any] = {'n_samples': 0, 'mean_volume_db': None, 'max_volume_db': None}
    if a_stream is not None:
        vol_cmd = [creative.ffmpeg(), '-hide_banner', '-i', str(path), '-af', 'volumedetect', '-f', 'null', '-']
        vol_res = subprocess.run(vol_cmd, capture_output=True, text=True, timeout=45)
        stderr = vol_res.stderr or ''
        ns_matches = _NSAMPLES_RE.findall(stderr)
        mean_m = _MEAN_VOL_RE.search(stderr)
        max_m = _MAX_VOL_RE.search(stderr)
        n_samples = int(ns_matches[-1]) if ns_matches else 0
        mean_db = float(mean_m.group(1)) if mean_m else None
        max_db = float(max_m.group(1)) if max_m else None
        vol_info = {'n_samples': n_samples, 'mean_volume_db': mean_db, 'max_volume_db': max_db}

        if n_samples == 0 or max_db is None or mean_db is None:
            failures.append('Silent or empty audio stream detected (0 decoded audio samples).')
        elif max_db <= -60.0 or mean_db <= -70.0:
            failures.append(f'Silent audio detected (mean {mean_db} dB, max {max_db} dB).')
        else:
            audible_ok = True

    return {
        'ok': len(failures) == 0,
        'video_path': str(path),
        'file_size_bytes': size_bytes,
        'duration_seconds': actual_duration,
        'video_stream': {
            'codec': v_stream.get('codec_name'),
            'width': width,
            'height': height,
            'r_frame_rate': v_stream.get('r_frame_rate'),
        } if v_stream else None,
        'audio_stream': {
            'codec': a_stream.get('codec_name'),
            'sample_rate': int(a_stream.get('sample_rate') or 0),
            'channels': int(a_stream.get('channels') or 0),
        } if a_stream else None,
        'audio_volume': vol_info,
        'checks': {
            'file_integrity': file_integrity_ok,
            'video_stream': video_ok,
            'resolution': resolution_ok,
            'duration': duration_ok,
            'audio_stream': audio_stream_ok,
            'audible_audio': audible_ok,
        },
        'failures': failures,
    }


def verify_job_for_publishing(con, job_row) -> str:
    """Gate called by publisher.py before publishing/simulating any scheduled Reel job."""
    from agents.storage import AgentStorage

    video_file = engine.video_path(con, job_row['id'])
    probe = inspect_video_with_ffprobe(video_file)
    if not probe['ok']:
        return 'QA blocked: ' + '; '.join(probe['failures'])[:180]

    # Check if a QA report / production exists for this job or video file and enforce human review in store mode.
    try:
        storage = AgentStorage()
        storage.init_schema(con)
        prod = con.execute(
            'SELECT id, qa_status, human_review_status FROM video_productions WHERE video_path=? OR id=?',
            (str(video_file), job_row['id']),
        ).fetchone()
        if prod:
            if prod['qa_status'] == 'failed':
                return 'QA blocked: Video failed quality assurance checks. Regenerate before publishing.'
            if job_row['mode'] == 'store' and prod['human_review_status'] != 'approved':
                return 'Human review required: approve visual quality, pronunciation and accuracy in AI Agent Center.'
    except Exception:
        pass
    return ''


class QualityAssuranceAgent(BaseAgent):
    """Verifies generated videos using FFprobe, silence detection, product/price checks and human review."""

    metadata = AgentMetadata(
        agent_id='qa_agent',
        name='Quality Assurance Agent',
        role='specialist',
        description='Verifies H.264 1080x1920 video and AAC audible audio streams via FFprobe, validates duration, product images, prices, captions and layout, and enforces human review before publishing.',
        capabilities=(
            'ffprobe_stream_inspection',
            'silence_detection',
            'price_and_image_verification',
            'publishing_gate',
        ),
        default_enabled=True,
        requires_ollama=False,
        can_publish=False,
        can_spend=False,
    )

    def __init__(self, *, storage_provider: Optional[Callable[[], Any]] = None):
        self._storage_provider = storage_provider

    def _get_storage(self):
        if self._storage_provider is not None:
            return self._storage_provider()
        from agents.storage import AgentStorage
        return AgentStorage()

    def _audit_production(self, ctx: AgentContext, storage: Any, prod: dict[str, Any]) -> dict[str, Any]:
        meta = prod.get('metadata') or {}
        vpath = Path(prod['video_path'])
        expected_dur = int(prod.get('duration_seconds') or 15)

        probe = inspect_video_with_ffprobe(vpath, expected_duration=expected_dur)
        failures = list(probe['failures'])
        warnings: list[str] = []

        catalogue = engine.catalogue(ctx.mode)
        products_meta = meta.get('products') or []
        images_ok = bool(products_meta)
        prices_ok = bool(products_meta)

        for pm in products_meta:
            sku = pm.get('sku', '')
            cat_item = catalogue.get(sku)
            if not cat_item:
                images_ok = False
                prices_ok = False
                failures.append(f'Product {sku} is missing from current {ctx.mode} catalogue.')
                continue
            if not cat_item.get('files') or not all(f.is_file() for f in cat_item['files']):
                images_ok = False
                failures.append(f'Product {sku} is missing local image files.')
            if 'Photo missing' in cat_item.get('issues', []):
                images_ok = False
                failures.append(f'Product {sku} flagged as Photo missing.')
            expected_price = float(cat_item['price'])
            rendered_price = float(pm.get('price', -1))
            if abs(expected_price - rendered_price) > 0.01:
                prices_ok = False
                failures.append(f'Price mismatch for {sku}: video shows ₹{rendered_price}, catalogue is ₹{expected_price}.')

        scenes = meta.get('scenes') or []
        captions_ok = bool(scenes) and all(bool(str(s.get('caption_text') or '').strip()) for s in scenes)
        has_cta = bool(scenes and str(scenes[-1].get('cta_text') or '').strip())
        has_hook = bool(scenes and str(scenes[0].get('hook_text') or '').strip())
        layout_ok = (
            meta.get('width') == 1080
            and meta.get('height') == 1920
            and bool(meta.get('category_layouts'))
            and has_cta
            and has_hook
        )
        if not captions_ok:
            failures.append('One or more scenes are missing on-screen captions.')
        if not layout_ok:
            failures.append('Visual layout check failed (missing hook, CTA, category layout, or 1080x1920 metadata).')

        if meta.get('voiceover_status') == 'unavailable_music_only':
            warnings.append('Voiceover narration unavailable; verify whether recorded/TTS audio is required before publishing.')

        automated_passed = len(failures) == 0
        overall_status = 'needs_human_review' if automated_passed else 'failed'
        publish_allowed = False  # Always blocked until human review approval.

        report = {
            'report_id': uuid.uuid4().hex[:12],
            'production_id': prod['id'],
            'mode': ctx.mode,
            'video_path': str(vpath),
            'video_url': meta.get('video_url', f'/video/{prod["id"]}.mp4'),
            'skus': prod.get('skus') or [],
            'automated_passed': automated_passed,
            'overall_status': overall_status,
            'publish_allowed': publish_allowed,
            'human_review': {
                'required': True,
                'status': prod.get('human_review_status', 'pending') if automated_passed else 'blocked',
                'checklist': {
                    'visual_quality': 'Requires human review',
                    'pronunciation': 'Requires human review',
                    'product_accuracy': 'Requires human review',
                },
            },
            'checks': {
                **probe['checks'],
                'product_images': images_ok,
                'price_accuracy': prices_ok,
                'captions': captions_ok,
                'visual_layout': layout_ok,
            },
            'probe': {
                'file_size_bytes': probe['file_size_bytes'],
                'duration_seconds': probe['duration_seconds'],
                'video_stream': probe['video_stream'],
                'audio_stream': probe['audio_stream'],
                'audio_volume': probe['audio_volume'],
            },
            'failures': failures,
            'warnings': warnings,
        }

        if storage:
            storage.save_qa_report(
                report_id=report['report_id'],
                production_id=prod['id'],
                run_id=ctx.run_id,
                mode=ctx.mode,
                automated_passed=automated_passed,
                overall_status=overall_status,
                publish_allowed=publish_allowed,
                report=report,
            )
            storage.update_video_production_qa(
                prod['id'],
                qa_status='passed' if automated_passed else 'failed',
                human_review_status='pending' if automated_passed else 'blocked',
            )
        return report

    def _audit_existing_reel_jobs(self, ctx: AgentContext) -> list[dict[str, Any]]:
        """Inspect existing Reel queue MP4s in data/reels to catch legacy silent or invalid videos."""
        catalogue = engine.catalogue(ctx.mode)
        reports: list[dict[str, Any]] = []
        with closing(engine.db()) as con:
            jobs = [
                dict(r)
                for r in con.execute('SELECT * FROM jobs WHERE mode=? ORDER BY created DESC', (ctx.mode,))
            ]
            for j in jobs:
                vpath = engine.video_path(con, j['id'])
                probe = inspect_video_with_ffprobe(vpath)
                failures = list(probe['failures'])
                p = catalogue.get(j['sku'])
                if not p:
                    failures.append(f'SKU {j["sku"]} missing from catalogue.')
                elif not p.get('files'):
                    failures.append(f'SKU {j["sku"]} missing product images.')
                elif f"₹{p['price']:,.2f}" not in (j.get('caption') or '') and f"₹{p['price']:,.2f}".removesuffix('.00') not in (j.get('caption') or ''):
                    failures.append(f'Price mismatch in Reel caption for {j["sku"]}.')
                reports.append({
                    'job_id': j['id'],
                    'sku': j['sku'],
                    'status': j['status'],
                    'video_path': str(vpath),
                    'automated_passed': len(failures) == 0,
                    'publish_allowed': False if failures else True,
                    'checks': probe['checks'],
                    'audio_volume': probe['audio_volume'],
                    'failures': failures,
                })
        return reports

    def execute(self, ctx: AgentContext) -> AgentResult:
        storage = self._get_storage()
        payload = self.validate_input(ctx.payload)

        if payload.get('audit_existing_jobs'):
            ctx.progress(30, f'Auditing existing Reel queue MP4s in {ctx.mode}...')
            job_reports = self._audit_existing_reel_jobs(ctx)
            failed_count = sum(1 for r in job_reports if not r['automated_passed'])
            ctx.progress(98, 'Reel queue audio & FFprobe audit complete.')
            return AgentResult(
                summary=f'Audited {len(job_reports)} existing Reel(s): {failed_count} failed QA (missing/silent audio or resolution mismatch).',
                data={'job_audits': job_reports, 'failed_count': failed_count},
                model_used='ffprobe-local',
            )

        if payload.get('video_path'):
            vpath = Path(str(payload['video_path']))
            expected_dur = int(payload['expected_duration']) if payload.get('expected_duration') else None
            probe = inspect_video_with_ffprobe(vpath, expected_duration=expected_dur)
            return AgentResult(
                summary=f'FFprobe QA {"passed" if probe["ok"] else "failed"} for {vpath.name}.',
                data={
                    'reports': [{
                        'production_id': vpath.stem,
                        'automated_passed': probe['ok'],
                        'overall_status': 'needs_human_review' if probe['ok'] else 'failed',
                        'publish_allowed': False,
                        'checks': probe['checks'],
                        'probe': probe,
                        'failures': probe['failures'],
                        'warnings': [],
                    }],
                    'passed_count': 1 if probe['ok'] else 0,
                    'failed_count': 0 if probe['ok'] else 1,
                },
                model_used='ffprobe-local',
            )

        production_id = str(payload.get('production_id') or '').strip()
        if not production_id and isinstance(payload.get('qa_handoff'), dict):
            production_id = str(payload['qa_handoff'].get('production_id') or '').strip()
        if not production_id and storage:
            handoff = storage.get_latest_handoff('qa_agent', mode=ctx.mode)
            if handoff and isinstance(handoff.get('payload'), dict):
                production_id = str(handoff['payload'].get('production_id') or '').strip()

        productions: list[dict[str, Any]] = []
        if production_id and storage:
            prod = storage.get_video_production(production_id, mode=ctx.mode)
            if not prod:
                raise AgentConfigurationError(f'Video production "{production_id}" not found.')
            productions = [prod]
        elif storage:
            all_prods = storage.list_video_productions(mode=ctx.mode, limit=10)
            if all_prods:
                productions = [all_prods[0]]

        if not productions:
            # Fall back to auditing existing Reel jobs when no video_productions exist yet.
            ctx.progress(30, f'No Video Agent productions found; auditing existing {ctx.mode} Reel queue...')
            job_reports = self._audit_existing_reel_jobs(ctx)
            failed_count = sum(1 for r in job_reports if not r['automated_passed'])
            return AgentResult(
                summary=f'Audited {len(job_reports)} existing Reel(s): {failed_count} failed QA.',
                data={'reports': [], 'job_audits': job_reports, 'failed_count': failed_count},
                model_used='ffprobe-local',
            )

        ctx.progress(35, f'Running FFprobe & quality checks on {len(productions)} video(s)...')
        reports = [self._audit_production(ctx, storage, prod) for prod in productions]
        passed_count = sum(1 for r in reports if r['automated_passed'])
        failed_count = len(reports) - passed_count

        for r in reports:
            if not r['automated_passed']:
                ctx.log('ERROR', f"QA failed for {r['production_id']}: {'; '.join(r['failures'])}")
            else:
                ctx.log('INFO', f"Automated QA passed for {r['production_id']}; awaiting human review (visual, pronunciation, product accuracy).")

        ctx.progress(98, 'QA verification complete.')
        return AgentResult(
            summary=(
                f'QA checked {len(reports)} video(s): {passed_count} automated pass (pending human review), '
                f'{failed_count} failed (blocked from publishing).'
            ),
            data={
                'reports': reports,
                'passed_count': passed_count,
                'failed_count': failed_count,
            },
            model_used='ffprobe-local',
        )
