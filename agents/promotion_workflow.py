"""Product-scoped preparation pipeline. Never approves or publishes."""
from __future__ import annotations

import hashlib
import json
import engine
from agents.base import AgentResult, AgentConfigurationError, AgentExecutionError, AgentCancelledError
from agents.social_adapters import SUPPORTED_PLATFORMS


def validate_preferences(language, duration, platforms):
    if language not in ('en', 'te'):
        raise AgentConfigurationError('Choose English or Telugu narration.')
    try:
        duration = int(duration)
    except (ValueError, TypeError) as exc:
        raise AgentConfigurationError('Choose a valid video duration.') from exc
    if duration not in (15, 30, 60):
        raise AgentConfigurationError('Choose 15, 30 or 60 seconds.')
    if (not isinstance(platforms, list) or not platforms
            or any(p not in SUPPORTED_PLATFORMS for p in platforms)):
        raise AgentConfigurationError('Select supported publishing platforms.')
    return language, duration, sorted(set(platforms))


def _eligible_product(sku, mode):
    product = engine.catalogue(mode).get(sku)
    if not product:
        raise AgentConfigurationError(f'Product {sku} not found.')
    if product['issues']:
        raise AgentConfigurationError(f'{sku} needs attention: ' + '; '.join(product['issues']))
    return product


def promotion_payload(sku, language, duration, platforms):
    language, duration, platforms = validate_preferences(language, duration, platforms)
    product = _eligible_product(sku, 'store')
    signature = engine.signature(product)
    key = hashlib.sha256(json.dumps([sku, signature, language, duration, platforms]).encode()).hexdigest()
    return {'product_promotion': True, 'sku': sku, 'product_signature': signature,
            'promotion_key': key, 'language': language, 'duration_seconds': duration, 'platforms': platforms}


def prepare_product_promotion(ctx, storage):
    if ctx.mode != 'store':
        raise AgentConfigurationError('Automatic product preparation is available in My Store only.')
    sku = str(ctx.payload.get('sku') or '').strip()
    language, duration, platforms = validate_preferences(
        ctx.payload.get('language'), ctx.payload.get('duration_seconds'), ctx.payload.get('platforms'))
    expected_signature = ctx.payload.get('product_signature')
    def verify():
        ctx.check_cancelled()
        product = _eligible_product(sku, ctx.mode)
        if engine.signature(product) != expected_signature:
            raise AgentConfigurationError('Product content changed; save again to prepare a fresh promotion.')
    verify()
    required = ('marketing_agent', 'video_agent', 'qa_agent', 'social_agent')
    configs = storage.get_agent_configs()
    disabled = [aid for aid in required if not configs.get(aid, {}).get('enabled', False)]
    if disabled:
        raise AgentConfigurationError('Enable required agents: ' + ', '.join(disabled))
    outputs = {}
    def run(aid, payload, percent):
        verify()
        ctx.progress(percent, f'Preparing {sku}: {aid}')
        child = ctx.execute_child(aid, payload)
        if child['status'] == 'cancelled':
            raise AgentCancelledError(f'{aid} cancelled.')
        if child['status'] != 'succeeded':
            raise AgentExecutionError(f"{aid}: {child.get('error_message') or 'failed'}", retryable=False)
        data = (child.get('output') or {}).get('data') or {}
        outputs[aid] = {'run_id': child['id'], 'data': data}
        return data
    marketing = run('marketing_agent', {'skus': [sku], 'campaign_type': 'single_product',
                    'language': language, 'fallback_on_ollama_error': True,
                    'create_campaign_drafts': False}, 10)
    handoff = marketing.get('video_handoff')
    if not isinstance(handoff, dict) or not handoff.get('campaign_id'):
        raise AgentExecutionError('Marketing campaign handoff missing.', retryable=False)
    video = run('video_agent', {'skus': [sku], 'language': language, 'duration_seconds': duration,
                'video_handoff': handoff, 'voiceover_mode': 'auto', 'require_voiceover': True}, 35)
    production_id = (video.get('qa_handoff') or {}).get('production_id')
    if not production_id:
        raise AgentExecutionError('Video production ID missing.', retryable=False)
    qa = run('qa_agent', {'production_id': production_id}, 65)
    if qa.get('failed_count') or not any(
        report.get('production_id') == production_id and report.get('automated_passed')
        for report in qa.get('reports', [])
    ):
        raise AgentExecutionError('Video failed QA; publishing is blocked. Inspect QA results.', retryable=False)
    social = run('social_agent', {'production_id': production_id,
                 'campaign_id': handoff['campaign_id'], 'platforms': platforms}, 85)
    verify()
    posts = social.get('posts', [])
    if len(posts) + len(social.get('skipped_duplicates', [])) != len(platforms):
        raise AgentExecutionError('Not every platform has a prepared draft.', retryable=False)
    ctx.progress(98, 'Promotion prepared; awaiting human QA and social approval.')
    return AgentResult(summary=f'{sku}: video and platform drafts prepared. Human approval required.',
                       data={'sku': sku, 'production_id': production_id, 'campaign_id': handoff['campaign_id'],
                             'steps': outputs, 'posts': posts}, model_used='local-workflow')
