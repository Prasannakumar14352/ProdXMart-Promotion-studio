"""Catalog Agent: multi-page sync, SKU+variant deduplication, image validation/retry and freshness tracking."""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
import hashlib
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import engine
import product_editor
from agents.base import (
    AgentConfigurationError,
    AgentContext,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.catalog_adapter import (
    SupplierCatalogAdapter,
    SupplierProductRecord,
    build_supplier_adapter,
)

MAX_PHOTOS = product_editor.MAX_PHOTOS


def _dedup_key(sku: str, variant: str) -> str:
    return f"{sku.strip().upper()}::{(variant or '').strip().casefold()}"


def _variant_sku(base_sku: str, variant: str) -> str:
    clean_base = base_sku.strip()[:96]
    slug = re.sub(r'[^A-Za-z0-9]+', '-', variant.strip()).strip('-').upper()
    if not slug:
        return clean_base
    candidate = f'{clean_base}-{slug}'
    return candidate[:120]


def _valid_decimal_str(raw: Optional[str], *, positive: bool = False) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    try:
        dec = engine.amount(str(raw).strip())
        if positive and dec <= 0:
            return None
        return f'{dec.quantize(Decimal("0.01"))}'
    except (ValueError, InvalidOperation):
        return None


def _valid_store_url(url: str, mode: str) -> bool:
    if not url:
        return False
    u = urlsplit(url)
    allowed = ('prodxmart.com', 'www.prodxmart.com') if mode == 'store' else ('example.com', 'prodxmart.com', 'www.prodxmart.com')
    return u.scheme == 'https' and (u.hostname or '').lower() in allowed and u.path not in ('', '/') and not u.username and not u.password


class CatalogAgent(BaseAgent):
    """Synchronizes multi-page supplier catalogs, images, stock freshness and diagnostics."""

    metadata = AgentMetadata(
        agent_id='catalog_agent',
        name='Catalog Agent',
        role='specialist',
        description='Synchronizes multi-page Factori Bridge extension, CSV, or local catalogue products with SKU+variant deduplication, ordered image validation/retries, stock freshness tracking, and detailed diagnostics.',
        capabilities=('catalog_sync', 'multi_page_ingestion', 'image_validation', 'freshness_tracking'),
        default_enabled=True,
        requires_ollama=False,
        can_publish=False,
        can_spend=False,
    )

    def __init__(
        self,
        *,
        storage_provider: Optional[Callable[[], Any]] = None,
        store_photos_fn: Optional[Callable[[str, str, str], dict[str, Any]]] = None,
        photo_bytes_fn: Optional[Callable[[dict[str, Any]], bytes]] = None,
        adapter_factory: Optional[Callable[..., SupplierCatalogAdapter]] = None,
    ):
        self._storage_provider = storage_provider
        self._store_photos_fn = store_photos_fn
        self._photo_bytes_fn = photo_bytes_fn
        self._adapter_factory = adapter_factory

    def _download_image_with_retry(
        self,
        ctx: AgentContext,
        url: str,
        *,
        max_retries: int,
        retry_delay: float,
    ) -> tuple[Optional[bytes], Optional[str], int]:
        fetch_photo = self._photo_bytes_fn or product_editor.photo_bytes
        attempts = 0
        last_error = ''
        for attempt in range(1, max(1, max_retries + 1) + 1):
            ctx.check_cancelled()
            attempts = attempt
            try:
                body = fetch_photo({'url': url})
                return body, None, attempts
            except ValueError as exc:
                last_error = str(exc)
                # Retry transient network/host download failures; validation errors (invalid format/size) fail fast.
                transient = any(token in last_error.lower() for token in ('reach', 'download', 'http 5', 'http 429', 'timed out'))
                if not transient or attempt >= max_retries + 1:
                    break
            except Exception as exc:
                last_error = str(exc)
                if attempt >= max_retries + 1:
                    break
            if ctx.cancel_event.wait(timeout=retry_delay):
                ctx.check_cancelled()
        return None, last_error or 'Image download failed.', attempts

    def execute(self, ctx: AgentContext) -> AgentResult:
        if self._storage_provider is not None:
            storage = self._storage_provider()
        else:
            from agents.storage import AgentStorage
            storage = AgentStorage()
        staged_batch = storage.get_staged_catalog_batch(ctx.mode) if storage else None

        source = str(ctx.payload.get('source') or '').strip().lower()
        if not source:
            if ctx.payload.get('csv_content') or ctx.payload.get('csv_path'):
                source = 'csv'
            elif ctx.payload.get('items') or ctx.payload.get('cards') or ctx.payload.get('pages'):
                source = 'extension'
            elif staged_batch and (staged_batch.get('cards') or staged_batch.get('pages')):
                source = 'extension'
            else:
                source = 'local_catalogue'

        make_adapter = self._adapter_factory or build_supplier_adapter
        adapter: SupplierCatalogAdapter = make_adapter(
            source,
            mode=ctx.mode,
            payload=ctx.payload,
            staged_batch=staged_batch,
        )

        fetch_store_gallery = bool(ctx.payload.get('fetch_store_gallery', source in ('extension', 'csv', 'factori_api')))
        image_retries = int(ctx.payload.get('image_retries', 2))
        image_retry_delay = float(ctx.payload.get('image_retry_delay', 0.25))

        ctx.progress(10, f'Reading supplier pages from {adapter.display_name}...')
        pages_scanned = 0
        raw_count = 0
        deduped_items: dict[str, SupplierProductRecord] = {}
        deduped_order: list[str] = []
        deduplicated_count = 0

        missing_sku: list[dict[str, Any]] = []
        unknown_stock: list[dict[str, Any]] = []
        missing_prices: list[dict[str, Any]] = []
        image_failures: list[dict[str, Any]] = []

        seen_page_numbers: set[int] = set()
        for page in adapter.fetch_pages():
            ctx.check_cancelled()
            if page.page_number in seen_page_numbers and not page.items:
                break
            seen_page_numbers.add(page.page_number)
            pages_scanned += 1
            for rec in page.items:
                raw_count += 1
                if not rec.sku:
                    missing_sku.append({
                        'name': rec.name or '(Unnamed product)',
                        'variant': rec.variant,
                        'product_url': rec.product_url,
                        'page': rec.source_page,
                        'reason': 'Missing SKU; cannot uniquely identify product.',
                    })
                    continue
                key = _dedup_key(rec.sku, rec.variant)
                if key in deduped_items:
                    deduplicated_count += 1
                    existing = deduped_items[key]
                    # Merge ordered unique images and prefer populated fields.
                    merged_images = list(existing.images)
                    for img in rec.images:
                        if img not in merged_images:
                            merged_images.append(img)
                    existing.images = merged_images
                    if not existing.name and rec.name:
                        existing.name = rec.name
                    if not existing.product_url and rec.product_url:
                        existing.product_url = rec.product_url
                    if existing.price is None and rec.price is not None:
                        existing.price = rec.price
                    if existing.cost is None and rec.cost is not None:
                        existing.cost = rec.cost
                    if existing.stock is None and rec.stock is not None:
                        existing.stock = rec.stock
                        existing.stock_raw = rec.stock_raw
                else:
                    deduped_items[key] = rec
                    deduped_order.append(key)

        ctx.log('INFO', f'Scanned {pages_scanned} page(s), {raw_count} raw row(s), {len(deduped_order)} unique SKU+variant item(s).')
        ctx.progress(30, f'Processing {len(deduped_order)} deduplicated product(s)...')

        # Load existing local catalogue state so we detect new vs updated and handle base-SKU variant collisions.
        existing_by_sku: dict[str, dict[str, Any]] = {}
        existing_variant_map: dict[str, str] = {}
        if ctx.mode == 'store':
            _, existing_rows = product_editor.read_rows()
            cmap = engine.config().get('catalogue_columns') or {}
            col = lambda k: cmap.get(k, k)
            for row in existing_rows:
                rsku = (row.get(col('sku')) or '').strip()
                if not rsku:
                    continue
                existing_by_sku[rsku.upper()] = row
                rvar = (row.get('variant') or '').strip()
                existing_variant_map[_dedup_key(rsku, rvar)] = rsku
                if rvar:
                    existing_variant_map[_dedup_key(rsku.split('-')[0], rvar)] = rsku
        else:
            for sku, item in engine.catalogue(ctx.mode).items():
                existing_by_sku[sku.upper()] = item
                existing_variant_map[_dedup_key(sku, '')] = sku

        if storage:
            for synced in storage.list_catalog_sync_items(mode=ctx.mode):
                dkey = synced['dedup_key']
                if synced['sku'].upper() in existing_by_sku:
                    existing_variant_map[dkey] = synced['sku']

        base_sku_counts: dict[str, int] = {}
        for key in deduped_order:
            rec = deduped_items[key]
            base_sku_counts[rec.sku.upper()] = base_sku_counts.get(rec.sku.upper(), 0) + 1

        imported_products = 0
        updated_products = 0
        unchanged_or_skipped = 0
        failed_products = len(missing_sku)
        missing_images_products = 0

        get_store_photos = self._store_photos_fn or product_editor.store_photos
        synced_now = engine.now().isoformat()
        total_items = max(1, len(deduped_order))

        for idx, key in enumerate(deduped_order):
            ctx.check_cancelled()
            rec = deduped_items[key]
            pct = 30 + int(((idx + 1) / total_items) * 62)
            ctx.progress(pct, f'Syncing {rec.sku} ({idx + 1}/{len(deduped_order)})...')

            # Resolve target SKU in catalogue.csv (preserving existing match or disambiguating multi-variant base SKUs).
            target_sku = existing_variant_map.get(key)
            if not target_sku:
                if rec.variant and (base_sku_counts.get(rec.sku.upper(), 0) > 1 or rec.sku.upper() in existing_by_sku):
                    target_sku = _variant_sku(rec.sku, rec.variant)
                else:
                    target_sku = rec.sku

            existing_details = None
            if ctx.mode == 'store' and target_sku.upper() in existing_by_sku:
                try:
                    existing_details = product_editor.details(existing_by_sku[target_sku.upper()].get((engine.config().get('catalogue_columns') or {}).get('sku', 'sku'), target_sku))
                except ValueError:
                    existing_details = None
            elif ctx.mode == 'demo' and target_sku.upper() in existing_by_sku:
                existing_details = existing_by_sku[target_sku.upper()]

            price_str = _valid_decimal_str(rec.price, positive=True)
            cost_str = _valid_decimal_str(rec.cost, positive=False)
            missing_price_fields = []
            if price_str is None:
                missing_price_fields.append('price')
            if cost_str is None:
                missing_price_fields.append('cost')

            if missing_price_fields:
                failed_products += 1
                missing_prices.append({
                    'sku': target_sku,
                    'variant': rec.variant,
                    'name': rec.name,
                    'missing_fields': missing_price_fields,
                    'reason': 'Missing or invalid ' + ' and '.join(missing_price_fields) + '.',
                })
                if storage:
                    storage.upsert_catalog_sync_item(
                        dedup_key=key,
                        mode=ctx.mode,
                        sku=target_sku,
                        base_sku=rec.sku,
                        variant=rec.variant,
                        name=rec.name or target_sku,
                        product_url=rec.product_url,
                        supplier_source=adapter.adapter_id,
                        price=price_str or '',
                        cost=cost_str or '',
                        stock=rec.stock,
                        stock_status='unknown' if rec.stock is None else ('out_of_stock' if rec.stock == 0 else 'verified'),
                        last_synced_at=synced_now,
                        stock_verified_at=synced_now if rec.stock is not None else '',
                        image_count=0,
                        missing_images_count=0,
                        last_run_id=ctx.run_id,
                        last_error='Missing or invalid price/cost.',
                    )
                continue

            if not rec.name:
                failed_products += 1
                ctx.log('WARNING', f'Skipped {target_sku}: missing product name.')
                continue

            stock_is_unknown = rec.stock is None
            if stock_is_unknown:
                unknown_stock.append({
                    'sku': target_sku,
                    'variant': rec.variant,
                    'name': rec.name,
                    'raw_value': rec.stock_raw,
                    'reason': 'Available stock quantity could not be confirmed.',
                })

            effective_url = rec.product_url or (existing_details.get('product_url', '') if isinstance(existing_details, dict) else '')
            if not _valid_store_url(effective_url, ctx.mode):
                failed_products += 1
                ctx.log('WARNING', f'Skipped {target_sku}: valid ProdXMart product URL is required.')
                if storage:
                    storage.upsert_catalog_sync_item(
                        dedup_key=key,
                        mode=ctx.mode,
                        sku=target_sku,
                        base_sku=rec.sku,
                        variant=rec.variant,
                        name=rec.name,
                        product_url=effective_url,
                        supplier_source=adapter.adapter_id,
                        price=price_str,
                        cost=cost_str,
                        stock=rec.stock,
                        stock_status='unknown' if stock_is_unknown else ('out_of_stock' if rec.stock == 0 else 'verified'),
                        last_synced_at=synced_now,
                        stock_verified_at=synced_now if not stock_is_unknown else '',
                        image_count=len(existing_details.get('photos', [])) if existing_details else 0,
                        missing_images_count=1 if not (existing_details and existing_details.get('photos')) else 0,
                        last_run_id=ctx.run_id,
                        last_error='Missing or invalid ProdXMart product URL.',
                    )
                continue

            # Gather ordered unique candidate image URLs from supplier record + authorized storefront gallery.
            candidate_urls: list[str] = []
            seen_urls: set[str] = set()

            def add_url(u: str) -> None:
                cleaned = (u or '').strip()
                if cleaned.startswith('https://') and cleaned not in seen_urls:
                    seen_urls.add(cleaned)
                    candidate_urls.append(cleaned)

            if fetch_store_gallery and source != 'local_catalogue':
                try:
                    gallery = get_store_photos(effective_url, target_sku, rec.name)
                    for u in (gallery or {}).get('images') or []:
                        add_url(u)
                except Exception as exc:
                    image_failures.append({
                        'sku': target_sku,
                        'variant': rec.variant,
                        'name': rec.name,
                        'url': effective_url,
                        'attempts': 1,
                        'error': f'Storefront gallery lookup failed: {exc}',
                    })
            elif fetch_store_gallery and source == 'local_catalogue' and existing_details is not None and not existing_details.get('photos'):
                try:
                    gallery = get_store_photos(effective_url, target_sku, rec.name)
                    for u in (gallery or {}).get('images') or []:
                        add_url(u)
                except Exception as exc:
                    image_failures.append({
                        'sku': target_sku,
                        'variant': rec.variant,
                        'name': rec.name,
                        'url': effective_url,
                        'attempts': 1,
                        'error': f'Storefront gallery lookup failed: {exc}',
                    })

            for u in rec.images:
                add_url(u)

            kept_photos = list(existing_details.get('photos') or []) if (ctx.mode == 'store' and isinstance(existing_details, dict)) else []
            existing_hashes: set[str] = set()
            for rel in kept_photos:
                fpath = (engine.ROOT / 'media' / rel).resolve()
                if fpath.is_file():
                    existing_hashes.add(hashlib.sha256(fpath.read_bytes()).hexdigest())

            new_photo_payloads: list[dict[str, str]] = []
            item_img_failures = 0
            for url in candidate_urls:
                if len(kept_photos) + len(new_photo_payloads) >= MAX_PHOTOS:
                    break
                body, err, attempts = self._download_image_with_retry(
                    ctx,
                    url,
                    max_retries=image_retries,
                    retry_delay=image_retry_delay,
                )
                if body is None:
                    item_img_failures += 1
                    image_failures.append({
                        'sku': target_sku,
                        'variant': rec.variant,
                        'name': rec.name,
                        'url': url,
                        'attempts': attempts,
                        'error': err or 'Image validation failed.',
                    })
                    continue
                digest = hashlib.sha256(body).hexdigest()
                if digest in existing_hashes:
                    continue
                existing_hashes.add(digest)
                new_photo_payloads.append({'base64': base64.b64encode(body).decode('ascii')})

            total_photos_for_item = (
                len(kept_photos) + len(new_photo_payloads)
                if ctx.mode == 'store'
                else len(existing_details.get('files', [])) if isinstance(existing_details, dict) else len(new_photo_payloads)
            )
            if total_photos_for_item == 0 or item_img_failures > 0:
                missing_images_products += 1

            stock_status = 'unknown' if stock_is_unknown else ('out_of_stock' if rec.stock == 0 else 'verified')
            stock_verified_at = synced_now if not stock_is_unknown else (
                storage.get_catalog_sync_item(key).get('stock_verified_at', '') if (storage and storage.get_catalog_sync_item(key)) else ''
            )

            if ctx.mode == 'store':
                # Persist into store/catalogue.csv via product_editor.save() unless local_catalogue had nothing to change.
                if source == 'local_catalogue' and existing_details is not None and not new_photo_payloads:
                    unchanged_or_skipped += 1
                else:
                    if existing_details is not None:
                        resolved_stock = str(rec.stock) if rec.stock is not None else str(existing_details.get('stock', '0'))
                        resolved_category = rec.category or existing_details.get('category') or 'General'
                        resolved_fees = rec.fees if rec.fees is not None else (existing_details.get('fees') or '0')
                        resolved_feature = rec.feature_te or existing_details.get('feature_te') or ''
                        resolved_media_ok = rec.media_ok if rec.media_ok is not None else (str(existing_details.get('media_ok', '')).lower() == 'yes')
                        version = existing_details['version']
                    else:
                        resolved_stock = str(rec.stock) if rec.stock is not None else '0'
                        resolved_category = rec.category or 'General'
                        resolved_fees = rec.fees if rec.fees is not None else '0'
                        resolved_feature = rec.feature_te or ''
                        resolved_media_ok = bool(rec.media_ok) if rec.media_ok is not None else False
                        version = ''

                    save_payload = {
                        'sku': target_sku,
                        'name': rec.name,
                        'category': resolved_category,
                        'price': price_str,
                        'cost': cost_str,
                        'fees': resolved_fees,
                        'stock': resolved_stock,
                        'product_url': effective_url,
                        'feature_te': resolved_feature,
                        'media_ok': resolved_media_ok,
                        'keep': kept_photos,
                        'new': new_photo_payloads,
                        'version': version,
                    }
                    try:
                        product_editor.save(save_payload)
                        if existing_details is None:
                            imported_products += 1
                        else:
                            updated_products += 1
                    except Exception as exc:
                        failed_products += 1
                        ctx.log('ERROR', f'Failed saving {target_sku}: {exc}')
                        if storage:
                            storage.upsert_catalog_sync_item(
                                dedup_key=key,
                                mode=ctx.mode,
                                sku=target_sku,
                                base_sku=rec.sku,
                                variant=rec.variant,
                                name=rec.name,
                                product_url=effective_url,
                                supplier_source=adapter.adapter_id,
                                price=price_str,
                                cost=cost_str,
                                stock=rec.stock,
                                stock_status=stock_status,
                                last_synced_at=synced_now,
                                stock_verified_at=stock_verified_at,
                                image_count=total_photos_for_item,
                                missing_images_count=item_img_failures + (1 if total_photos_for_item == 0 else 0),
                                last_run_id=ctx.run_id,
                                last_error=str(exc)[:300],
                            )
                        continue
            else:
                # Demo mode stays non-destructive on demo/catalogue.csv while recording genuine verification/audit stats.
                if existing_details is None:
                    imported_products += 1
                else:
                    unchanged_or_skipped += 1

            if storage:
                storage.upsert_catalog_sync_item(
                    dedup_key=key,
                    mode=ctx.mode,
                    sku=target_sku,
                    base_sku=rec.sku,
                    variant=rec.variant,
                    name=rec.name,
                    product_url=effective_url,
                    supplier_source=adapter.adapter_id,
                    price=price_str,
                    cost=cost_str,
                    stock=rec.stock if not stock_is_unknown else (int(existing_details['stock']) if existing_details else None),
                    stock_status=stock_status,
                    last_synced_at=synced_now,
                    stock_verified_at=stock_verified_at,
                    image_count=total_photos_for_item,
                    missing_images_count=item_img_failures + (1 if total_photos_for_item == 0 else 0),
                    last_run_id=ctx.run_id,
                    last_error='',
                )

        # Compute stock freshness summary from SQLite sync records (or current catalogue).
        freshness = storage.get_catalog_freshness_summary(mode=ctx.mode, max_age_hours=engine.config()['maximum_catalogue_age_hours']) if storage else {
            'fresh': 0,
            'stale': 0,
            'unknown': len(unknown_stock),
            'last_synced_at': synced_now,
        }

        stats = {
            'source': adapter.adapter_id,
            'mode': ctx.mode,
            'pages_scanned': pages_scanned,
            'total_products': len(deduped_order) + len(missing_sku),
            'unique_products': len(deduped_order),
            'imported_products': imported_products,
            'updated_products': updated_products,
            'unchanged_or_skipped': unchanged_or_skipped,
            'deduplicated_count': deduplicated_count,
            'failed_products': failed_products,
            'missing_images': missing_images_products,
            'last_synced_at': synced_now,
            'stock_freshness': freshness,
        }
        diagnostics = {
            'missing_sku': missing_sku,
            'unknown_stock': unknown_stock,
            'missing_prices': missing_prices,
            'image_failures': image_failures,
        }

        ctx.progress(98, 'Recording sync summary...')
        ctx.log(
            'INFO',
            f"Catalog sync complete ({adapter.adapter_id}): {stats['total_products']} total, "
            f"{imported_products} imported, {updated_products} updated, {failed_products} failed, {missing_images_products} missing images.",
        )

        return AgentResult(
            summary=(
                f"Synced {stats['total_products']} products across {pages_scanned} page(s): "
                f"{imported_products} imported, {updated_products} updated, {failed_products} failed, {missing_images_products} missing images."
            ),
            data={'stats': stats, 'diagnostics': diagnostics},
            model_used='local-deterministic',
        )
