"""Replaceable supplier catalog adapters for CSV, extension and future Factori API."""

from __future__ import annotations

from abc import ABC, abstractmethod
import csv
from dataclasses import dataclass, field
import io
from pathlib import Path
from typing import Any, Iterator, Optional

import engine
from agents.base import AgentConfigurationError


@dataclass
class SupplierProductRecord:
    """Normalized product payload emitted by any supplier catalog adapter."""

    name: str
    sku: str = ''
    variant: str = ''
    price: Optional[str] = None
    cost: Optional[str] = None
    fees: Optional[str] = None
    stock: Optional[int] = None
    stock_raw: str = ''
    product_url: str = ''
    category: str = ''
    feature_te: str = ''
    images: list[str] = field(default_factory=list)
    media_ok: Optional[bool] = None
    source_page: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CatalogPage:
    """One page of supplier records returned by a paginated adapter."""

    page_number: int
    total_pages: Optional[int]
    has_next: bool
    items: list[SupplierProductRecord]


class SupplierCatalogAdapter(ABC):
    """Replaceable supplier adapter interface (CSV, Factori Bridge, or future Factori API)."""

    adapter_id: str
    display_name: str

    @abstractmethod
    def fetch_pages(self) -> Iterator[CatalogPage]:
        """Yield CatalogPage objects across all available pages."""


def _parse_optional_int(value: Any) -> tuple[Optional[int], str]:
    if value is None:
        return None, ''
    if isinstance(value, bool):
        return None, str(value)
    if isinstance(value, int):
        return (value if value >= 0 else None), str(value)
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value), str(int(value))
        return None, str(value)
    text = str(value).strip().replace(',', '')
    if not text:
        return None, ''
    if text.isdigit():
        return int(text), text
    return None, str(value).strip()


def _parse_optional_amount(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().replace('₹', '').replace(',', '').strip()
    if not text:
        return None
    return text


def _parse_images(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if isinstance(v, str) and str(v).strip()]
    elif isinstance(value, str):
        items = [part.strip() for part in value.replace('|', '\n').replace(';', '\n').splitlines() if part.strip()]
    else:
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for url in items:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def _parse_media_ok(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ('yes', 'true', '1', 'y'):
        return True
    if text in ('no', 'false', '0', 'n'):
        return False
    return None


def normalize_record(raw: dict[str, Any], *, page_number: int = 1, column_map: Optional[dict[str, str]] = None) -> SupplierProductRecord:
    """Normalize a dict from extension cards, CSV rows or API objects."""
    cmap = column_map or {}

    def pick(*keys: str) -> Any:
        for k in keys:
            mapped = cmap.get(k, k)
            if mapped in raw and raw[mapped] not in (None, ''):
                return raw[mapped]
            if k in raw and raw[k] not in (None, ''):
                return raw[k]
        lowered = {str(k).strip().lower(): v for k, v in raw.items()}
        for k in keys:
            lk = k.lower()
            if lk in lowered and lowered[lk] not in (None, ''):
                return lowered[lk]
        return None

    name = str(pick('name', 'product_name', 'title', 'Item Name') or '').strip()
    sku = str(pick('sku', 'Item Code', 'Product Code', 'rsp_sku') or '').strip()
    variant = str(pick('variant', 'variant_name', 'size_color', 'option') or '').strip()
    price = _parse_optional_amount(pick('price', 'selling_price', 'customer_pays', 'Retail Price'))
    cost = _parse_optional_amount(pick('cost', 'supplier_cost', 'factori_price', 'unit_cost'))
    fees = _parse_optional_amount(pick('fees', 'direct_fees'))
    stock_raw_val = pick('stock', 'available_units', 'available_stock', 'quantity', 'qty')
    stock, stock_raw = _parse_optional_int(stock_raw_val)
    product_url = str(pick('product_url', 'url', 'store_url', 'product_link', 'link') or '').strip()
    category = str(pick('category', 'product_category') or '').strip()
    feature_te = str(pick('feature_te', 'telugu_feature') or '').strip()
    images = _parse_images(pick('images', 'photos', 'image_urls', 'gallery'))
    media_ok = _parse_media_ok(pick('media_ok', 'permission'))

    return SupplierProductRecord(
        name=name,
        sku=sku,
        variant=variant,
        price=price,
        cost=cost,
        fees=fees,
        stock=stock,
        stock_raw=stock_raw,
        product_url=product_url,
        category=category,
        feature_te=feature_te,
        images=images,
        media_ok=media_ok,
        source_page=int(raw.get('page', page_number) or page_number),
        raw=dict(raw),
    )


class ExtensionBatchAdapter(SupplierCatalogAdapter):
    """Consumes multi-page product cards scanned by the Factori Bridge extension."""

    adapter_id = 'extension'
    display_name = 'Factori Bridge Browser Extension'

    def __init__(self, *, cards: Optional[list[dict[str, Any]]] = None, pages: Optional[list[dict[str, Any]]] = None, page_size: int = 50):
        self._cards = list(cards or [])
        self._pages = list(pages or [])
        self._page_size = max(1, int(page_size))

    def fetch_pages(self) -> Iterator[CatalogPage]:
        if self._pages:
            total = len(self._pages)
            for idx, page_obj in enumerate(self._pages, 1):
                raw_cards = page_obj.get('cards') or page_obj.get('items') or []
                pnum = int(page_obj.get('page_number') or page_obj.get('current') or idx)
                ptotal = page_obj.get('total_pages') or page_obj.get('total') or total
                has_next = bool(page_obj.get('has_next', idx < total))
                items = [normalize_record(c, page_number=pnum) for c in raw_cards if isinstance(c, dict)]
                yield CatalogPage(page_number=pnum, total_pages=ptotal, has_next=has_next, items=items)
            return

        if not self._cards:
            yield CatalogPage(page_number=1, total_pages=1, has_next=False, items=[])
            return

        # Group by explicit card page metadata when present, otherwise chunk cleanly.
        if any(isinstance(c, dict) and c.get('page') for c in self._cards):
            grouped: dict[int, list[dict[str, Any]]] = {}
            for c in self._cards:
                if not isinstance(c, dict):
                    continue
                pnum = int(c.get('page') or 1)
                grouped.setdefault(pnum, []).append(c)
            ordered_pages = sorted(grouped.keys())
            total = len(ordered_pages)
            for idx, pnum in enumerate(ordered_pages):
                items = [normalize_record(c, page_number=pnum) for c in grouped[pnum]]
                yield CatalogPage(page_number=pnum, total_pages=total, has_next=idx < total - 1, items=items)
            return

        total_pages = (len(self._cards) + self._page_size - 1) // self._page_size
        for page_idx in range(total_pages):
            chunk = self._cards[page_idx * self._page_size : (page_idx + 1) * self._page_size]
            pnum = page_idx + 1
            items = [normalize_record(c, page_number=pnum) for c in chunk if isinstance(c, dict)]
            yield CatalogPage(page_number=pnum, total_pages=total_pages, has_next=pnum < total_pages, items=items)


class CsvSupplierAdapter(SupplierCatalogAdapter):
    """Consumes local CSV or OOXML Excel (.xlsx) files without Factori API access."""

    adapter_id = 'csv'
    display_name = 'CSV / Excel Catalogue Import'

    def __init__(
        self,
        *,
        csv_path: Optional[Path] = None,
        csv_content: Optional[str] = None,
        xlsx_path: Optional[Path] = None,
        xlsx_base64: Optional[str] = None,
        page_size: int = 50,
    ):
        self.csv_path = csv_path
        self.csv_content = csv_content
        self.xlsx_path = xlsx_path
        self.xlsx_base64 = xlsx_base64
        self._page_size = max(1, int(page_size))

    def fetch_pages(self) -> Iterator[CatalogPage]:
        rows: list[dict[str, Any]] = []
        if self.xlsx_base64 or self.xlsx_path is not None:
            import base64
            from agents.excel_io import read_xlsx_sheets
            if self.xlsx_base64:
                raw_bytes = base64.b64decode(self.xlsx_base64)
            else:
                if not self.xlsx_path.is_file():
                    raise AgentConfigurationError(f'Excel file not found: {self.xlsx_path}')
                raw_bytes = self.xlsx_path.read_bytes()
            sheets = read_xlsx_sheets(raw_bytes)
            picked = None
            for srows in sheets.values():
                if srows and any(k.strip().lower() in ('sku', 'name', 'product_name', 'item code') for k in srows[0].keys()):
                    picked = srows
                    break
            rows = picked if picked is not None else next(iter(sheets.values()), [])
        else:
            if self.csv_content is not None:
                text = self.csv_content.lstrip('\ufeff')
            elif self.csv_path is not None:
                if not self.csv_path.is_file():
                    raise AgentConfigurationError(f'CSV file not found: {self.csv_path}')
                text = self.csv_path.read_text(encoding='utf-8-sig')
            else:
                raise AgentConfigurationError('Provide csv_content, csv_path, or an Excel .xlsx file for catalog import.')

            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise AgentConfigurationError('CSV file has no header row.')
            rows = [
                row for row in reader
                if None not in row and any((str(v) or '').strip() for v in row.values())
            ]
        if not rows:
            yield CatalogPage(page_number=1, total_pages=1, has_next=False, items=[])
            return

        cmap = engine.config().get('catalogue_columns') or {}
        total_pages = (len(rows) + self._page_size - 1) // self._page_size
        for idx in range(total_pages):
            chunk = rows[idx * self._page_size : (idx + 1) * self._page_size]
            pnum = idx + 1
            items = [normalize_record(row, page_number=pnum, column_map=cmap) for row in chunk]
            yield CatalogPage(page_number=pnum, total_pages=total_pages, has_next=pnum < total_pages, items=items)


class LocalStoreCatalogAdapter(SupplierCatalogAdapter):
    """Reads the active local workspace catalogue.csv for verification and image sync."""

    adapter_id = 'local_catalogue'
    display_name = 'Local Workspace Catalogue'

    def __init__(self, mode: str, page_size: int = 50):
        self.mode = mode
        self._page_size = max(1, int(page_size))

    def fetch_pages(self) -> Iterator[CatalogPage]:
        base = engine.directory(self.mode)
        path = base / 'catalogue.csv'
        if not path.is_file():
            yield CatalogPage(page_number=1, total_pages=1, has_next=False, items=[])
            return
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            rows = [r for r in reader if None not in r and any((v or '').strip() for v in r.values())]
        if not rows:
            yield CatalogPage(page_number=1, total_pages=1, has_next=False, items=[])
            return
        cmap = (engine.config().get('catalogue_columns') or {}) if self.mode == 'store' else {}
        total_pages = (len(rows) + self._page_size - 1) // self._page_size
        for idx in range(total_pages):
            chunk = rows[idx * self._page_size : (idx + 1) * self._page_size]
            pnum = idx + 1
            items = [normalize_record(row, page_number=pnum, column_map=cmap) for row in chunk]
            yield CatalogPage(page_number=pnum, total_pages=total_pages, has_next=pnum < total_pages, items=items)


class FactoriApiAdapter(SupplierCatalogAdapter):
    """Replaceable supplier adapter boundary for future official Factori API integration."""

    adapter_id = 'factori_api'
    display_name = 'Factori Supplier API (Future Adapter)'

    def __init__(self, *, api_base_url: str = '', api_token: str = '', fetcher: Any = None):
        self.api_base_url = (api_base_url or '').strip().rstrip('/')
        self.api_token = (api_token or '').strip()
        self.fetcher = fetcher

    def fetch_pages(self) -> Iterator[CatalogPage]:
        if self.fetcher is None and (not self.api_base_url or not self.api_token):
            raise AgentConfigurationError(
                'Factori API access is not required and is not configured. Use Factori Bridge extension or CSV import.'
            )
        page = 1
        seen_signatures: set[str] = set()
        while True:
            payload = self.fetcher(page)
            raw_items = payload.get('items') or payload.get('data') or []
            sig = repr([(x.get('sku'), x.get('variant'), x.get('name')) for x in raw_items if isinstance(x, dict)])
            if sig in seen_signatures and raw_items:
                break
            seen_signatures.add(sig)
            total_pages = payload.get('total_pages')
            has_next = bool(payload.get('has_next'))
            items = [normalize_record(x, page_number=page) for x in raw_items if isinstance(x, dict)]
            yield CatalogPage(page_number=page, total_pages=total_pages, has_next=has_next, items=items)
            if not has_next or (total_pages is not None and page >= int(total_pages)) or not raw_items:
                break
            page += 1


def build_supplier_adapter(
    source: str,
    *,
    mode: str = 'store',
    payload: Optional[dict[str, Any]] = None,
    staged_batch: Optional[dict[str, Any]] = None,
) -> SupplierCatalogAdapter:
    """Instantiate the appropriate SupplierCatalogAdapter for the requested source."""
    data = payload or {}
    kind = (source or '').strip().lower()
    if kind == 'extension':
        cards = data.get('items') or data.get('cards')
        pages = data.get('pages')
        if cards is None and pages is None and staged_batch:
            cards = staged_batch.get('cards')
            pages = staged_batch.get('pages')
        return ExtensionBatchAdapter(cards=cards, pages=pages)
    if kind in ('csv', 'excel', 'xlsx'):
        csv_path = Path(data['csv_path']) if data.get('csv_path') else None
        xlsx_path = Path(data['xlsx_path']) if data.get('xlsx_path') else None
        return CsvSupplierAdapter(
            csv_path=csv_path,
            csv_content=data.get('csv_content'),
            xlsx_path=xlsx_path,
            xlsx_base64=data.get('xlsx_base64'),
        )
    if kind == 'factori_api':
        return FactoriApiAdapter(
            api_base_url=str(data.get('api_base_url') or ''),
            api_token=str(data.get('api_token') or ''),
        )
    return LocalStoreCatalogAdapter(mode=mode)
