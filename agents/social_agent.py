"""Social Media Agent & Approval Center business logic (manual publishing packages + future API adapters)."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
import uuid

import engine
from agents.base import (
    AgentConfigurationError,
    AgentContext,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.credentials import SecureCredentialStore
from agents.qa_agent import inspect_video_with_ffprobe
from agents.social_adapters import (
    SUPPORTED_PLATFORMS,
    build_manual_publishing_package,
    build_social_adapter,
)

SOCIAL_STATUSES = (
    'Draft',
    'Awaiting Approval',
    'Approved',
    'Scheduled',
    'Published',
    'Failed',
)


def platform_tracking_url(product_url: str, platform: str, post_id: str) -> str:
    """Attach platform-specific UTM tracking parameters to the actual product URL."""
    u = urlsplit(product_url)
    args = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not k.startswith('utm_')]
    args.extend([
        ('utm_source', platform),
        ('utm_medium', 'organic_social'),
        ('utm_campaign', post_id),
    ])
    return urlunsplit(u._replace(query=urlencode(args)))


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return 'MISSING_VIDEO'
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def compute_approval_fingerprint(
    *,
    post: dict[str, Any],
    product: Optional[dict[str, Any]],
    video_path: Path,
    production: Optional[dict[str, Any]],
) -> str:
    """Compute a tamper-evident fingerprint over video, product price/details, QA state and post copy."""
    if product is None:
        prod_sig = 'MISSING_PRODUCT'
    else:
        prod_sig = '|'.join([
            engine.signature(product),
            str(product['price']),
            product.get('media_ok', ''),
            product.get('category', ''),
        ])
    qa_sig = (
        f"{production['qa_status']}:{production['human_review_status']}"
        if production
        else 'NO_PRODUCTION'
    )
    parts = [
        post['platform'],
        post['sku'],
        post.get('production_id', ''),
        post.get('caption', ''),
        post.get('title', ''),
        post.get('description', ''),
        ','.join(post.get('hashtags') or []),
        post.get('product_link', ''),
        prod_sig,
        _sha256_file(video_path),
        qa_sig,
    ]
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


def validate_product_for_social(
    mode: str,
    sku: str,
    *,
    expected_price: Optional[float] = None,
) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """Recheck stock, current price and readiness rules before approval, scheduling or publishing."""
    cfg = engine.config()
    catalogue = engine.catalogue(mode)
    p = catalogue.get(sku)
    if not p:
        return False, f'Product {sku} no longer exists in {mode} catalogue.', None
    if p['issues']:
        return False, f'Product {sku} needs attention: ' + ', '.join(p['issues']), p
    if p['stock'] < cfg['minimum_stock']:
        return False, f'Product {sku} stock ({p["stock"]}) is below minimum ({cfg["minimum_stock"]}).', p
    if p['margin'] <= 0:
        return False, f'Product {sku} has non-positive margin.', p
    if expected_price is not None and abs(float(p['price']) - float(expected_price)) > 0.01:
        return False, f'Product {sku} price changed from ₹{expected_price:.2f} to ₹{float(p["price"]):.2f}.', p
    return True, '', p


def reconcile_social_approvals(storage: Any, mode: str) -> list[dict[str, Any]]:
    """Invalidate any Approved or Scheduled posts whose video, price, product or QA state changed."""
    posts = storage.list_social_posts(mode=mode, limit=200)
    if not any(p['status'] in ('Approved', 'Scheduled') for p in posts):
        return []
    catalogue = engine.catalogue(mode)
    invalidated: list[dict[str, Any]] = []

    for post in posts:
        if post['status'] not in ('Approved', 'Scheduled'):
            continue
        prod = storage.get_video_production(post['production_id'], mode=mode) if post.get('production_id') else None
        vpath = Path(prod['video_path']) if prod else Path(post.get('video_path') or '')
        product = catalogue.get(post['sku'])

        reason = ''
        if product is None:
            reason = 'Approval invalidated: product removed from catalogue.'
        elif abs(float(product['price']) - float(post.get('price_snapshot', -1))) > 0.01:
            reason = 'Approval invalidated: product price changed.'
        elif not vpath.is_file():
            reason = 'Approval invalidated: approved video file is missing.'
        elif prod and (prod['qa_status'] != 'passed' or prod['human_review_status'] != 'approved'):
            reason = 'Approval invalidated: video QA or human review is no longer approved.'
        else:
            current_fp = compute_approval_fingerprint(post=post, product=product, video_path=vpath, production=prod)
            if not post.get('approval_fingerprint') or current_fp != post['approval_fingerprint']:
                reason = 'Approval invalidated: video, product details, or post content changed.'

        if reason:
            updated = storage.invalidate_social_approval(post['id'], mode=mode, reason=reason)
            if updated:
                invalidated.append(updated)
    return invalidated


def _compose_post_copy(
    *,
    platform: str,
    product: dict[str, Any],
    post_id: str,
    mkt_item: Optional[dict[str, Any]],
) -> dict[str, Any]:
    price_str = f"₹{product['price']:,.2f}".removesuffix('.00')
    link = platform_tracking_url(product['product_url'], platform, post_id)

    if mkt_item:
        hashtags = list(mkt_item.get('hashtags') or ['#ProdXMart'])
        yt = mkt_item.get('youtube_shorts') or {}
        if platform == 'instagram_reels':
            caption = mkt_item.get('instagram_caption') or f"{product['name']} · {price_str}"
            title = f"{product['name']} · {price_str}"
            description = mkt_item.get('call_to_action') or product['product_url']
        elif platform == 'facebook_reels':
            caption = mkt_item.get('facebook_caption') or f"{product['name']} · {price_str}"
            title = f"{product['name']} ({product['category']}) · {price_str}"
            description = f"{caption}\n{link}"
        else:
            title = (yt.get('title') or f"{product['name']} · {price_str} #Shorts")[:100]
            description = yt.get('description') or f"{product['name']} · {price_str}\n{link}"
            caption = description
    else:
        hashtags = ['#ProdXMart', f"#{''.join(c for c in product['category'] if c.isalnum()) or 'Shopping'}"]
        if platform == 'youtube_shorts':
            title = f"{product['name']} · {price_str} | ProdXMart"[:100]
            description = f"{product['name']} ({product['category']}) · {price_str}\n{link}"
            caption = description
        else:
            title = f"{product['name']} · {price_str}"
            caption = f"{product['name']} · {price_str}\n{product.get('feature_te') or ''}\n{link}".strip()
            description = caption

    return {
        'title': title,
        'caption': caption,
        'description': description,
        'hashtags': hashtags,
        'product_link': link,
    }


def approve_social_post(storage: Any, *, mode: str, post_id: str, note: str = '') -> dict[str, Any]:
    reconcile_social_approvals(storage, mode)
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] not in ('Draft', 'Awaiting Approval'):
        raise ValueError(f'Only Draft or Awaiting Approval posts can be approved (current: {post["status"]}).')

    prod = storage.get_video_production(post['production_id'], mode=mode) if post.get('production_id') else None
    vpath = Path(prod['video_path']) if prod else Path(post.get('video_path') or '')
    if prod:
        if prod['qa_status'] != 'passed':
            raise ValueError('Video must pass automated QA before social approval.')
        if prod['human_review_status'] != 'approved':
            raise ValueError('Video must pass human QA review before social approval.')
    probe = inspect_video_with_ffprobe(vpath)
    if not probe['ok']:
        raise ValueError('Video QA check failed: ' + '; '.join(probe['failures']))

    ok, reason, product = validate_product_for_social(mode, post['sku'])
    if not ok or not product:
        raise ValueError(reason)

    dup = storage.find_duplicate_social_post(
        mode=mode,
        platform=post['platform'],
        sku=post['sku'],
        production_id=post['production_id'],
        week=post['week'],
        exclude_post_id=post['id'],
        statuses=('Approved', 'Scheduled', 'Published'),
    )
    if dup:
        raise ValueError(f'Duplicate post blocked: {post["platform"]} post {dup["id"]} for {post["sku"]} is already {dup["status"]}.')

    fp = compute_approval_fingerprint(post=post, product=product, video_path=vpath, production=prod)
    cover = product['files'][0] if product.get('files') else None
    pkg_post = {**post, 'status': 'Approved'}
    pkg_path = build_manual_publishing_package(post=pkg_post, video_path=vpath, product_photo=cover)
    return storage.mark_social_approved(
        post_id=post_id,
        mode=mode,
        approval_fingerprint=fp,
        price_snapshot=float(product['price']),
        product_snapshot=engine.signature(product),
        video_sha256=_sha256_file(vpath),
        package_path=str(pkg_path),
        note=note,
    )


def reject_social_post(storage: Any, *, mode: str, post_id: str, reason: str = '') -> dict[str, Any]:
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] == 'Published':
        raise ValueError('Published posts cannot be rejected.')
    return storage.mark_social_rejected(
        post_id=post_id,
        mode=mode,
        reason=reason or 'Rejected by reviewer; edit or regenerate before resubmitting.',
    )


def edit_social_post(storage: Any, *, mode: str, post_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] == 'Published':
        raise ValueError('Published posts cannot be edited.')
    hashtags = updates.get('hashtags', post['hashtags'])
    if isinstance(hashtags, str):
        hashtags = [t.strip() for t in hashtags.replace(',', ' ').split() if t.strip()]
    cleaned = {
        'title': str(updates.get('title', post['title'])).strip(),
        'caption': str(updates.get('caption', post['caption'])).strip(),
        'description': str(updates.get('description', post['description'])).strip(),
        'hashtags': list(hashtags or []),
        'product_link': str(updates.get('product_link', post['product_link'])).strip(),
    }
    if not cleaned['caption']:
        raise ValueError('Caption cannot be empty.')
    if not cleaned['product_link'].startswith('https://'):
        raise ValueError('Product link must be an HTTPS URL.')

    was_approved = post['status'] in ('Approved', 'Scheduled')
    next_status = 'Awaiting Approval' if (was_approved or updates.get('submit_for_approval')) else post['status']
    if post['status'] == 'Failed':
        next_status = 'Awaiting Approval'
    return storage.update_social_post_content(
        post_id=post_id,
        mode=mode,
        updates=cleaned,
        next_status=next_status,
        invalidate_reason='Post copy edited; re-approval required.' if was_approved else '',
    )


def schedule_social_post(storage: Any, *, mode: str, post_id: str, scheduled_at: str) -> dict[str, Any]:
    reconcile_social_approvals(storage, mode)
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] not in ('Approved', 'Scheduled'):
        raise ValueError('Approve the post explicitly before scheduling.')
    try:
        dt = datetime.fromisoformat(scheduled_at)
    except ValueError as exc:
        raise ValueError('Enter a valid ISO date/time for scheduling.') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=engine.IST)
    if dt <= engine.now():
        raise ValueError('Choose a future posting time in India time (IST).')

    ok, reason, _ = validate_product_for_social(mode, post['sku'], expected_price=post.get('price_snapshot'))
    if not ok:
        raise ValueError(reason)

    return storage.mark_social_scheduled(
        post_id=post_id,
        mode=mode,
        scheduled_at=dt.isoformat(),
        week=dt.strftime('%G-W%V'),
    )


def publish_or_prepare_social_post(
    storage: Any,
    *,
    mode: str,
    post_id: str,
    adapter_mode: str = 'manual_package',
    transport=None,
    cred_store: Optional[SecureCredentialStore] = None,
) -> dict[str, Any]:
    """Execute pre-publish checks and run the configured adapter without ever marking Published unconfirmed."""
    if storage.is_global_publishing_stopped():
        raise ValueError('Global publishing stop is active. Resume publishing before publishing or exporting.')

    reconcile_social_approvals(storage, mode)
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] not in ('Approved', 'Scheduled'):
        raise ValueError(f'Explicit human approval is required before publishing (current status: {post["status"]}).')

    ok, reason, product = validate_product_for_social(mode, post['sku'], expected_price=post.get('price_snapshot'))
    if not ok or not product:
        storage.mark_social_failed(post_id=post_id, mode=mode, error_message=reason)
        raise ValueError(reason)

    dup = storage.find_duplicate_social_post(
        mode=mode,
        platform=post['platform'],
        sku=post['sku'],
        production_id=post['production_id'],
        week=post['week'],
        exclude_post_id=post['id'],
        statuses=('Published',),
    )
    if dup:
        msg = f'Duplicate publish blocked: {post["platform"]} post {dup["id"]} for {post["sku"]} is already Published.'
        storage.mark_social_failed(post_id=post_id, mode=mode, error_message=msg)
        raise ValueError(msg)

    prod = storage.get_video_production(post['production_id'], mode=mode) if post.get('production_id') else None
    vpath = Path(prod['video_path']) if prod else Path(post.get('video_path') or '')
    probe = inspect_video_with_ffprobe(vpath)
    if not probe['ok']:
        msg = 'QA blocked: ' + '; '.join(probe['failures'])
        storage.mark_social_failed(post_id=post_id, mode=mode, error_message=msg)
        raise ValueError(msg)

    store = cred_store or SecureCredentialStore()
    adapter = build_social_adapter(post['platform'], mode=adapter_mode, transport=transport)
    cover = product['files'][0] if product.get('files') else None

    try:
        outcome = adapter.publish(post=post, video_path=vpath, product_photo=cover, cred_store=store)
    except Exception as exc:
        storage.mark_social_failed(post_id=post_id, mode=mode, error_message=str(exc)[:400])
        raise

    if outcome.confirmed and outcome.external_post_id and outcome.published_url:
        return storage.mark_social_published(
            post_id=post_id,
            mode=mode,
            published_url=outcome.published_url,
            external_post_id=outcome.external_post_id,
            confirmed_by='api_confirmed',
        )

    if outcome.package_path:
        return storage.update_social_package_path(post_id=post_id, mode=mode, package_path=outcome.package_path)

    return storage.get_social_post(post_id, mode=mode)


def confirm_social_post_published(
    storage: Any,
    *,
    mode: str,
    post_id: str,
    published_url: str,
    external_post_id: str = '',
) -> dict[str, Any]:
    """Record explicit confirmation and actual published URL before marking Published."""
    reconcile_social_approvals(storage, mode)
    post = storage.get_social_post(post_id, mode=mode)
    if not post:
        raise ValueError('Social post not found.')
    if post['status'] not in ('Approved', 'Scheduled'):
        raise ValueError('Only Approved or Scheduled posts can be confirmed as Published.')

    url = (published_url or '').strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Provide the actual HTTPS post URL to confirm publication.')

    ok, reason, _ = validate_product_for_social(mode, post['sku'], expected_price=post.get('price_snapshot'))
    if not ok:
        raise ValueError(reason)

    return storage.mark_social_published(
        post_id=post_id,
        mode=mode,
        published_url=url,
        external_post_id=(external_post_id or '').strip(),
        confirmed_by='manual_confirmation',
    )


class SocialMediaAgent(BaseAgent):
    """Prepares platform-specific manual publishing packages and manages Approval Center state."""

    metadata = AgentMetadata(
        agent_id='social_agent',
        name='Social Media Agent',
        role='specialist',
        description='Prepares Instagram Reels, Facebook Reels and YouTube Shorts manual publishing packages from QA-verified videos and Marketing copy, requiring explicit human approval before publishing.',
        capabilities=(
            'instagram_facebook_youtube_packages',
            'approval_workflow',
            'duplicate_prevention',
            'publishing_calendar',
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

    def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = super().validate_input(payload)
        raw_platforms = data.get('platforms') or list(SUPPORTED_PLATFORMS)
        if isinstance(raw_platforms, str):
            raw_platforms = [raw_platforms]
        platforms = [str(p).strip().lower() for p in raw_platforms if str(p).strip()]
        invalid = [p for p in platforms if p not in SUPPORTED_PLATFORMS]
        if invalid:
            raise AgentConfigurationError(f'Unsupported social platform(s): {", ".join(invalid)}.')
        if not platforms:
            platforms = list(SUPPORTED_PLATFORMS)
        return {
            'platforms': list(dict.fromkeys(platforms)),
            'production_id': str(data.get('production_id') or '').strip(),
            'campaign_id': str(data.get('campaign_id') or '').strip(),
            'regenerate_post_id': str(data.get('regenerate_post_id') or '').strip(),
        }

    def execute(self, ctx: AgentContext) -> AgentResult:
        storage = self._get_storage()
        params = self.validate_input(ctx.payload)
        reconcile_social_approvals(storage, ctx.mode)

        # Regenerate a single existing post if requested.
        if params['regenerate_post_id']:
            existing = storage.get_social_post(params['regenerate_post_id'], mode=ctx.mode)
            if not existing:
                raise AgentConfigurationError('Social post to regenerate was not found.')
            if existing['status'] == 'Published':
                raise AgentConfigurationError('Published posts cannot be regenerated.')
            catalogue = engine.catalogue(ctx.mode)
            product = catalogue.get(existing['sku'])
            if not product:
                raise AgentConfigurationError(f'Product {existing["sku"]} is no longer in catalogue.')
            campaigns = storage.list_marketing_campaigns(mode=ctx.mode, limit=10)
            mkt_item = None
            for c in campaigns:
                for p in c.get('campaign', {}).get('products', []):
                    if p.get('sku') == existing['sku']:
                        mkt_item = p
                        break
                if mkt_item:
                    break
            copy = _compose_post_copy(platform=existing['platform'], product=product, post_id=existing['id'], mkt_item=mkt_item)
            updated = storage.update_social_post_content(
                post_id=existing['id'],
                mode=ctx.mode,
                updates=copy,
                next_status='Awaiting Approval',
                invalidate_reason='Regenerated post content; requires human approval.',
            )
            return AgentResult(
                summary=f'Regenerated {existing["platform"]} post {existing["id"]} (Awaiting Approval).',
                data={'posts': [updated], 'skipped_duplicates': []},
                model_used='local-deterministic',
            )

        ctx.progress(20, 'Loading QA-verified video production and Marketing copy...')
        prod_id = params['production_id']
        production = None
        if prod_id:
            production = storage.get_video_production(prod_id, mode=ctx.mode)
        else:
            prods = storage.list_video_productions(mode=ctx.mode, limit=10)
            production = next((p for p in prods if p['qa_status'] == 'passed'), prods[0] if prods else None)

        if not production:
            raise AgentConfigurationError('Generate a video with Video Production Agent before preparing social posts.')

        skus = production.get('skus') or []
        if not skus:
            raise AgentConfigurationError('Selected video production has no SKUs attached.')
        primary_sku = skus[0]
        catalogue = engine.catalogue(ctx.mode)
        product = catalogue.get(primary_sku)
        if not product:
            raise AgentConfigurationError(f'Product {primary_sku} no longer exists in {ctx.mode} catalogue.')

        campaigns = storage.list_marketing_campaigns(mode=ctx.mode, limit=15)
        mkt_item = None
        campaign_id = params['campaign_id'] or production.get('campaign_id', '')
        for c in campaigns:
            if campaign_id and c['id'] != campaign_id:
                continue
            for item in c.get('campaign', {}).get('products', []):
                if item.get('sku') == primary_sku:
                    mkt_item = item
                    campaign_id = c['id']
                    break
            if mkt_item:
                break

        week = engine.now().strftime('%G-W%V')
        created_posts: list[dict[str, Any]] = []
        skipped_duplicates: list[dict[str, str]] = []
        vpath = Path(production['video_path'])
        initial_status = 'Awaiting Approval' if production['qa_status'] == 'passed' else 'Draft'

        ctx.progress(60, 'Preparing platform posts and checking duplicates...')
        for platform in params['platforms']:
            ctx.check_cancelled()
            dup = storage.find_duplicate_social_post(
                mode=ctx.mode,
                platform=platform,
                sku=primary_sku,
                production_id=production['id'],
                week=week,
                statuses=('Draft', 'Awaiting Approval', 'Approved', 'Scheduled', 'Published'),
            )
            if dup:
                skipped_duplicates.append({
                    'platform': platform,
                    'sku': primary_sku,
                    'existing_post_id': dup['id'],
                    'status': dup['status'],
                })
                continue

            post_id = uuid.uuid4().hex[:12]
            copy = _compose_post_copy(platform=platform, product=product, post_id=post_id, mkt_item=mkt_item)
            created = storage.create_social_post(
                post_id=post_id,
                run_id=ctx.run_id,
                mode=ctx.mode,
                platform=platform,
                sku=primary_sku,
                skus=skus,
                production_id=production['id'],
                campaign_id=campaign_id,
                video_path=str(vpath),
                video_sha256=_sha256_file(vpath),
                price_snapshot=float(product['price']),
                product_snapshot=engine.signature(product),
                week=week,
                status=initial_status,
                title=copy['title'],
                caption=copy['caption'],
                description=copy['description'],
                hashtags=copy['hashtags'],
                product_link=copy['product_link'],
            )
            created_posts.append(created)

        ctx.progress(98, 'Social posts ready in Approval Center.')
        ctx.log('INFO', f'Prepared {len(created_posts)} social post(s) for {primary_sku} ({len(skipped_duplicates)} duplicate(s) prevented).')

        return AgentResult(
            summary=f'Prepared {len(created_posts)} social post(s) for {primary_sku} ({initial_status}); {len(skipped_duplicates)} duplicate(s) prevented.',
            data={
                'posts': created_posts,
                'skipped_duplicates': skipped_duplicates,
                'production_id': production['id'],
            },
            model_used='local-deterministic',
        )
