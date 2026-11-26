"""Video Production Agent: renders synchronized 1080x1920 H.264+AAC MP4 videos from Marketing storyboards."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import tempfile
from typing import Any, Callable, Optional
import uuid

import creative
import engine
from soundtrack import write_music
from agents.base import (
    AgentConfigurationError,
    AgentContext,
    AgentExecutionError,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.tts_provider import VoiceoverProvider, build_narration_track

SUPPORTED_DURATIONS = (15, 30, 60)
SUPPORTED_VO_MODES = ('auto', 'recorded', 'tts', 'music_only')


def _distribute_durations(total_duration: int, count: int) -> list[int]:
    base = total_duration // count
    rem = total_duration % count
    return [base + (1 if i < rem else 0) for i in range(count)]


def _build_scene_plan(
    *,
    products: list[dict[str, Any]],
    handoff_items: dict[str, dict[str, Any]],
    duration_seconds: int,
    language: str,
    demo: bool,
) -> list[dict[str, Any]]:
    """Build synchronized scene specs across 1 or multiple products for 15s, 30s, or 60s."""
    # Single product with a matching 4-scene storyboard at 15s preserves exact storyboard timing.
    if len(products) == 1 and duration_seconds == 15:
        p = products[0]
        sb = (handoff_items.get(p['sku']) or {}).get('storyboard') or []
        if len(sb) >= 4 and sum(int(s.get('duration_seconds', 0)) for s in sb[:4]) == 15:
            scenes = []
            for idx, sc in enumerate(sb[:4]):
                scenes.append({
                    'product': p,
                    'duration_seconds': int(sc['duration_seconds']),
                    'hook_text': handoff_items[p['sku']].get('opening_hook', '') if idx == 0 else f'Scene {idx + 1}',
                    'heading_text': p['name'] if idx in (0, 2) else sc.get('on_screen_text', p['name']),
                    'caption_text': sc.get('on_screen_text', ''),
                    'voiceover': sc.get('voiceover', ''),
                    'cta_text': handoff_items[p['sku']].get('call_to_action', 'prodxmart.com') if idx == 3 else '',
                    'image_index': idx,
                })
            return scenes

    # Dynamic multi-scene allocation for 15s, 30s, and 60s across 1..N products.
    target_scenes = {15: 4, 30: 6, 60: 8}[duration_seconds]
    if len(products) > 1:
        # Ensure every product gets showcase scenes plus dedicated opening hook and closing CTA.
        target_scenes = max(target_scenes, min(12, len(products) * 2 + 2))
    durations = _distribute_durations(duration_seconds, target_scenes)

    scenes: list[dict[str, Any]] = []
    for idx, dur in enumerate(durations):
        if idx == 0:
            p = products[0]
            h = handoff_items.get(p['sku']) or {}
            hook = h.get('opening_hook') or ('మీ స్టైల్‌కి కొత్త లుక్!' if language == 'te' else 'Discover ProdXMart Picks')
            scenes.append({
                'product': p,
                'duration_seconds': dur,
                'hook_text': hook,
                'heading_text': p['name'],
                'caption_text': f"₹{p['price']:,.2f}".removesuffix('.00'),
                'voiceover': (h.get('storyboard') or [{}])[0].get('voiceover', hook),
                'cta_text': '',
                'image_index': 0,
            })
        elif idx == target_scenes - 1:
            p = products[-1]
            h = handoff_items.get(p['sku']) or {}
            cta = h.get('call_to_action') or p['product_url']
            scenes.append({
                'product': p,
                'duration_seconds': dur,
                'hook_text': 'మీకు నచ్చితే ఇప్పుడే ఆర్డర్ చేయండి.' if language == 'te' else 'Shop Now on ProdXMart',
                'heading_text': p['name'],
                'caption_text': cta,
                'voiceover': (h.get('storyboard') or [{}])[-1].get('voiceover', cta),
                'cta_text': 'prodxmart.com',
                'image_index': max(0, len(p.get('files') or []) - 1),
            })
        else:
            prod_idx = (idx - 1) % len(products)
            p = products[prod_idx]
            h = handoff_items.get(p['sku']) or {}
            sb = h.get('storyboard') or []
            sb_entry = sb[min(len(sb) - 1, idx)] if sb else {}
            img_idx = (idx - 1) // len(products) + 1
            price_str = f"₹{p['price']:,.2f}".removesuffix('.00')
            caption = sb_entry.get('on_screen_text') or p.get('feature_te') or f'{p["name"]} · {price_str}'
            scenes.append({
                'product': p,
                'duration_seconds': dur,
                'hook_text': p['category'].upper(),
                'heading_text': price_str if idx % 2 == 0 else p['name'],
                'caption_text': caption,
                'voiceover': sb_entry.get('voiceover', caption),
                'cta_text': '',
                'image_index': img_idx,
            })
    return scenes


class VideoProductionAgent(BaseAgent):
    """Renders 1080x1920 H.264+AAC promotional videos with synchronized music, voiceover & ducking."""

    metadata = AgentMetadata(
        agent_id='video_agent',
        name='Video Production Agent',
        role='specialist',
        description='Renders 1080x1920 vertical H.264+AAC promotional videos (15s, 30s, or 60s) with category layouts, multiple product images, voiceover ducking and rights-cleared music.',
        capabilities=(
            'video_rendering_1080x1920',
            'multi_duration_15_30_60',
            'audio_ducking',
            'storyboard_execution',
        ),
        default_enabled=True,
        requires_ollama=False,
        can_publish=False,
        can_spend=False,
    )

    def __init__(
        self,
        *,
        storage_provider: Optional[Callable[[], Any]] = None,
        voiceover_provider: Optional[VoiceoverProvider] = None,
    ):
        self._storage_provider = storage_provider
        self._voiceover_provider = voiceover_provider

    def _get_storage(self):
        if self._storage_provider is not None:
            return self._storage_provider()
        from agents.storage import AgentStorage
        return AgentStorage()

    def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = super().validate_input(payload)
        try:
            duration = int(data.get('duration_seconds', 15))
        except (TypeError, ValueError) as exc:
            raise AgentConfigurationError('duration_seconds must be 15, 30, or 60.') from exc
        if duration not in SUPPORTED_DURATIONS:
            raise AgentConfigurationError('Supported video durations are 15, 30, or 60 seconds.')

        handoff = data.get('video_handoff') if isinstance(data.get('video_handoff'), dict) else None
        language = str(data.get('language') or (handoff.get('language') if handoff else None) or 'te').strip().lower()
        if language not in ('te', 'en', 'bilingual'):
            raise AgentConfigurationError('Unsupported video language. Choose te, en, or bilingual.')

        voiceover_mode = str(data.get('voiceover_mode') or 'auto').strip().lower()
        if voiceover_mode not in SUPPORTED_VO_MODES:
            raise AgentConfigurationError(f'Unsupported voiceover_mode "{voiceover_mode}".')

        raw_skus = data.get('skus')
        if raw_skus is None and data.get('sku'):
            raw_skus = [data.get('sku')]
        skus = [str(s).strip() for s in raw_skus if str(s).strip()] if isinstance(raw_skus, list) else []

        return {
            'duration_seconds': duration,
            'language': language,
            'skus': skus,
            'video_handoff': handoff,
            'recorded_audio_path': str(data.get('recorded_audio_path') or '').strip(),
            'voiceover_mode': voiceover_mode,
            'require_voiceover': bool(data.get('require_voiceover', False)),
            'duck_music': bool(data.get('duck_music', True)),
            'regenerate_production_id': str(data.get('regenerate_production_id') or '').strip(),
        }

    def execute(self, ctx: AgentContext) -> AgentResult:
        storage = self._get_storage()
        params = self.validate_input(ctx.payload)
        catalogue = engine.catalogue(ctx.mode)
        if not catalogue:
            raise AgentConfigurationError(f'No products available in {ctx.mode} catalogue.')

        handoff = params['video_handoff']
        if handoff is None and storage:
            latest = storage.get_latest_handoff('video_agent', mode=ctx.mode)
            if latest and isinstance(latest.get('payload'), dict):
                handoff = latest['payload']

        handoff_items = {
            str(item['sku']): item
            for item in (handoff.get('items') or [])
            if isinstance(item, dict) and item.get('sku')
        } if handoff else {}

        # Select target products dynamically (explicit SKUs -> Marketing handoff SKUs -> eligible catalogue).
        selected: list[dict[str, Any]] = []
        if params['skus']:
            for sku in params['skus']:
                if sku not in catalogue:
                    raise AgentConfigurationError(f'SKU "{sku}" not found in {ctx.mode} catalogue.')
                selected.append(catalogue[sku])
        elif handoff_items:
            for sku in handoff_items:
                if sku in catalogue and not catalogue[sku]['issues']:
                    selected.append(catalogue[sku])
        if not selected:
            eligible = [p for p in catalogue.values() if not p['issues']]
            if not eligible:
                raise AgentConfigurationError('No promotion-ready products with images and valid margins found.')
            selected = sorted(eligible, key=lambda p: (-p['margin'], -p['stock'], p['sku']))[:1]

        for p in selected:
            if not p.get('files') or not all(f.is_file() for f in p['files']):
                raise AgentExecutionError(f'Cannot render video for {p["sku"]}: product images are missing.', retryable=False)

        ctx.progress(15, f'Building {params["duration_seconds"]}s storyboard for {len(selected)} product(s)...')
        scene_plan = _build_scene_plan(
            products=selected,
            handoff_items=handoff_items,
            duration_seconds=params['duration_seconds'],
            language=params['language'],
            demo=(ctx.mode == 'demo'),
        )

        # Render PIL 1080x1920 frames with category-specific visual layouts.
        ctx.progress(35, 'Rendering 1080x1920 vertical scene graphics...')
        rendered_specs: list[dict[str, Any]] = []
        category_layouts: list[str] = []
        for idx, sc in enumerate(scene_plan):
            ctx.check_cancelled()
            p = sc['product']
            theme = creative.layout_for_category(p.get('category', ''), p.get('name', ''))
            if theme['layout_id'] not in category_layouts:
                category_layouts.append(theme['layout_id'])
            img = creative.render_scene_card(
                p,
                scene_index=idx,
                total_scenes=len(scene_plan),
                demo=(ctx.mode == 'demo'),
                hook_text=sc['hook_text'],
                heading_text=sc['heading_text'],
                caption_text=sc['caption_text'],
                cta_text=sc['cta_text'],
                image_index=sc['image_index'],
                layout=theme,
            )
            rendered_specs.append({
                'image': img,
                'duration_seconds': sc['duration_seconds'],
                'voiceover': sc['voiceover'],
            })

        production_id = params['regenerate_production_id'] or uuid.uuid4().hex[:16]
        outfile = engine.OUT / f'{production_id}.mp4'

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            music_wav = tmp_dir / 'music.wav'
            narration_wav = tmp_dir / 'narration.wav'

            ctx.progress(55, 'Generating rights-cleared music and voiceover audio...')
            write_music(music_wav, seconds=params['duration_seconds'])

            rec_path = Path(params['recorded_audio_path']) if params['recorded_audio_path'] else None
            if params['voiceover_mode'] == 'recorded' and rec_path is None:
                raise AgentConfigurationError('voiceover_mode="recorded" requires recorded_audio_path.')

            if params['voiceover_mode'] == 'music_only':
                vo_meta = {'status': 'music_only_requested', 'provider': None, 'speech_segments': []}
            else:
                vo_meta = build_narration_track(
                    scenes=scene_plan,
                    total_duration=params['duration_seconds'],
                    language=params['language'],
                    out_wav=narration_wav,
                    provider=self._voiceover_provider,
                    recorded_audio_path=rec_path,
                )

            if params['require_voiceover'] and vo_meta['status'] == 'unavailable_music_only':
                raise AgentConfigurationError(
                    f'No approved TTS voice or recorded audio is available for language "{params["language"]}". '
                    'Provide recorded_audio_path or configure an approved TTS provider.'
                )

            has_narration = narration_wav.is_file() and vo_meta['status'] in ('recorded_audio', 'tts_generated')
            ctx.progress(75, 'Encoding H.264 MP4 with AAC audio...')
            render_info = creative.render_composed_video(
                scene_specs=rendered_specs,
                music_wav=music_wav,
                narration_wav=narration_wav if has_narration else None,
                outfile=outfile,
                duck_music=params['duck_music'],
            )

        campaign_id = (handoff or {}).get('campaign_id', '')
        skus_list = [p['sku'] for p in selected]
        metadata = {
            'production_id': production_id,
            'video_path': str(outfile),
            'video_url': f'/video/{production_id}.mp4',
            'campaign_id': campaign_id,
            'mode': ctx.mode,
            'skus': skus_list,
            'products': [
                {
                    'sku': p['sku'],
                    'name': p['name'],
                    'category': p['category'],
                    'price': float(p['price']),
                    'product_url': p['product_url'],
                    'photo_count': len(p['files']),
                }
                for p in selected
            ],
            'duration_seconds': params['duration_seconds'],
            'language': params['language'],
            'category_layouts': category_layouts,
            'scene_count': len(scene_plan),
            'scenes': [
                {
                    'scene_number': idx + 1,
                    'sku': sc['product']['sku'],
                    'duration_seconds': sc['duration_seconds'],
                    'hook_text': sc['hook_text'],
                    'caption_text': sc['caption_text'],
                    'voiceover': sc['voiceover'],
                    'cta_text': sc['cta_text'],
                }
                for idx, sc in enumerate(scene_plan)
            ],
            'voiceover_status': vo_meta['status'],
            'voiceover_provider': vo_meta['provider'],
            'speech_segments': vo_meta['speech_segments'],
            'ducking_applied': render_info['ducking_applied'],
            'width': render_info['width'],
            'height': render_info['height'],
        }

        if storage:
            storage.save_video_production(
                production_id=production_id,
                run_id=ctx.run_id,
                mode=ctx.mode,
                campaign_id=campaign_id,
                skus=skus_list,
                duration_seconds=params['duration_seconds'],
                language=params['language'],
                video_path=str(outfile),
                metadata=metadata,
            )
            storage.save_agent_handoff(
                source_agent='video_agent',
                target_agent='qa_agent',
                mode=ctx.mode,
                campaign_id=campaign_id,
                run_id=ctx.run_id,
                payload={'production_id': production_id, 'metadata': metadata},
                status='ready',
            )

        ctx.progress(98, 'Video production complete; queued for QA verification...')
        ctx.log('INFO', f'Rendered {params["duration_seconds"]}s 1080x1920 MP4 ({production_id}) for {len(selected)} product(s); voiceover={vo_meta["status"]}.')

        return AgentResult(
            summary=f'Rendered {params["duration_seconds"]}s 1080x1920 MP4 ({production_id}) with AAC audio [{vo_meta["status"]}].',
            data={
                'production': metadata,
                'qa_handoff': {'production_id': production_id, 'metadata': metadata},
            },
            model_used='ffmpeg-local',
            warnings=(
                ['No approved TTS voice or recorded audio found; rendered with rights-cleared background music. Human voiceover/audio review required.']
                if vo_meta['status'] == 'unavailable_music_only'
                else []
            ),
        )
