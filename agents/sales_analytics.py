"""Grounded Sales Analytics, Expenses & Profit Ledger, and Excel/CSV Reconciliation."""

from __future__ import annotations

import base64
from contextlib import closing
import csv
from decimal import Decimal
from io import StringIO
import os
import tempfile
from typing import Any, Optional

import engine
from agents.excel_io import read_xlsx_sheets, write_xlsx_sheets
from agents.storage import AgentStorage

ORDER_HEADERS = ['order_id', 'line_id', 'sku', 'delivered_units', 'returned_units', 'sales', 'refunds', 'net_product_cost', 'fees', 'campaign']
EXPENSE_HEADERS = ['reference', 'type', 'amount']


def _write_csv_atomic(path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, encoding='utf-8-sig', newline='', delete=False) as f:
            tmp_name = f.name
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def build_sales_analytics(
    mode: str,
    *,
    category: str = '',
    sku: str = '',
    campaign: str = '',
    return_only: bool = False,
    storage: Optional[AgentStorage] = None,
) -> dict[str, Any]:
    """Build financial & SKU performance analytics reconciled strictly with engine.earnings(mode)."""
    base = engine.directory(mode)
    products = engine.catalogue(mode)
    storage = storage or AgentStorage()

    raw_orders = engine.records(base / 'orders.csv', ORDER_HEADERS)
    raw_expenses = engine.records(base / 'expenses.csv', EXPENSE_HEADERS)
    raw_traffic = engine.records(base / 'traffic.csv', ['campaign', 'sessions'])
    settlements = storage.list_bank_settlements(mode=mode, limit=200)
    social_posts = storage.list_social_posts(mode=mode, limit=200)

    seen_order_keys: set[tuple[str, str]] = set()
    parsed_orders: list[dict[str, Any]] = []
    for r in raw_orders:
        key = (r['order_id'], r['line_id'])
        if not all(key) or key in seen_order_keys or not r['sku']:
            raise ValueError('Each order line needs a unique order ID + line ID and SKU.')
        seen_order_keys.add(key)
        delivered = int(r['delivered_units'])
        returned = int(r['returned_units'])
        sales = engine.amount(r['sales'])
        refunds = engine.amount(r['refunds'])
        cost = engine.amount(r['net_product_cost'])
        fees = engine.amount(r['fees'])
        if not 0 <= returned <= delivered or refunds > sales:
            raise ValueError('Check returned units or refunds in orders.csv.')

        prod = products.get(r['sku'])
        prod_cat = (prod.get('category') if prod else '') or 'Uncategorized'
        prod_name = (prod.get('name') if prod else '') or r['sku']
        camp_name = (r['campaign'] or '').strip()

        parsed_orders.append({
            'order_id': r['order_id'],
            'line_id': r['line_id'],
            'sku': r['sku'],
            'name': prod_name,
            'category': prod_cat,
            'delivered_units': delivered,
            'returned_units': returned,
            'net_units': delivered - returned,
            'sales': sales,
            'refunds': refunds,
            'net_sales': sales - refunds,
            'net_product_cost': cost,
            'fees': fees,
            'contribution': (sales - refunds) - cost - fees,
            'campaign': camp_name or 'Unattributed',
            'attributed': bool(camp_name),
        })

    # Apply dashboard filters cleanly.
    cat_q = category.strip().lower()
    sku_q = sku.strip().lower()
    camp_q = campaign.strip().lower()
    filtered_orders = []
    for row in parsed_orders:
        if cat_q and row['category'].lower() != cat_q:
            continue
        if sku_q and sku_q not in row['sku'].lower() and sku_q not in row['name'].lower():
            continue
        if camp_q and row['campaign'].lower() != camp_q:
            continue
        if return_only and row['returned_units'] <= 0:
            continue
        filtered_orders.append(row)

    order_ids = {r['order_id'] for r in filtered_orders}
    delivered_units = sum(r['delivered_units'] for r in filtered_orders)
    returned_units = sum(r['returned_units'] for r in filtered_orders)
    net_units = sum(r['net_units'] for r in filtered_orders)
    gross_sales = sum((r['sales'] for r in filtered_orders), Decimal(0))
    refunds = sum((r['refunds'] for r in filtered_orders), Decimal(0))
    net_sales = sum((r['net_sales'] for r in filtered_orders), Decimal(0))
    product_cost = sum((r['net_product_cost'] for r in filtered_orders), Decimal(0))
    order_fees = sum((r['fees'] for r in filtered_orders), Decimal(0))
    contribution = net_sales - product_cost - order_fees
    attributed_net_sales = sum((r['net_sales'] for r in filtered_orders if r['attributed']), Decimal(0))

    seen_refs: set[str] = set()
    expenses_list: list[dict[str, Any]] = []
    ad_spend = Decimal(0)
    other_expenses = Decimal(0)
    expense_by_type: dict[str, Decimal] = {}
    for row in raw_expenses:
        ref = row['reference']
        if not ref or ref in seen_refs:
            raise ValueError('Duplicate or empty expense reference.')
        seen_refs.add(ref)
        amt = engine.amount(row['amount'])
        etype = (row['type'] or 'Other').strip()
        expense_by_type[etype] = expense_by_type.get(etype, Decimal(0)) + amt
        if 'advert' in etype.lower() or etype.lower() == 'ad':
            ad_spend += amt
        else:
            other_expenses += amt
        expenses_list.append({'reference': ref, 'type': etype, 'amount': float(amt)})

    non_order_spend = ad_spend + other_expenses
    total_expenses = product_cost + order_fees + non_order_spend
    operating_profit = net_sales - total_expenses
    margin_pct = round(float(operating_profit) / float(net_sales) * 100.0, 2) if net_sales > 0 else 0.0
    return_rate_pct = round((returned_units / delivered_units * 100.0), 2) if delivered_units > 0 else 0.0

    # Strict ROAS gate: only compute when BOTH reliable attributed sales & positive ad spend exist.
    roas_value = None
    roas_status = 'Insufficient attribution or ad spend data'
    if attributed_net_sales > 0 and ad_spend > 0:
        roas_value = round(float(attributed_net_sales) / float(ad_spend), 2)
        roas_status = f'{roas_value}x (Attributed net sales ₹{float(attributed_net_sales):,.2f} / Ad spend ₹{float(ad_spend):,.2f})'

    # Reconcile unfiltered numbers directly against engine.earnings(mode).
    engine_summary = engine.earnings(mode)
    is_unfiltered = not (cat_q or sku_q or camp_q or return_only)
    matches_engine = (
        abs(float(operating_profit) - float(engine_summary['profit'])) < 0.01
        and abs(float(net_sales) - float(engine_summary['sales'])) < 0.01
    ) if is_unfiltered else None

    # SKU profitability aggregation.
    sku_map: dict[str, dict[str, Any]] = {}
    for r in filtered_orders:
        s = r['sku']
        entry = sku_map.setdefault(s, {
            'sku': s,
            'name': r['name'],
            'category': r['category'],
            'orders': set(),
            'delivered_units': 0,
            'returned_units': 0,
            'net_units': 0,
            'gross_sales': Decimal(0),
            'refunds': Decimal(0),
            'net_sales': Decimal(0),
            'product_cost': Decimal(0),
            'fees': Decimal(0),
            'contribution': Decimal(0),
        })
        entry['orders'].add(r['order_id'])
        entry['delivered_units'] += r['delivered_units']
        entry['returned_units'] += r['returned_units']
        entry['net_units'] += r['net_units']
        entry['gross_sales'] += r['sales']
        entry['refunds'] += r['refunds']
        entry['net_sales'] += r['net_sales']
        entry['product_cost'] += r['net_product_cost']
        entry['fees'] += r['fees']
        entry['contribution'] += r['contribution']

    sku_performance = []
    for s, e in sku_map.items():
        ret_rate = round((e['returned_units'] / e['delivered_units'] * 100.0), 1) if e['delivered_units'] > 0 else 0.0
        contrib = float(e['contribution'])
        cat_prod = products.get(s)
        unit_price = float(cat_prod['price']) if cat_prod else (float(e['net_sales']) / e['net_units'] if e['net_units'] > 0 else 0.0)
        unit_margin = float(cat_prod['margin']) if cat_prod else (contrib / e['net_units'] if e['net_units'] > 0 else 0.0)
        break_even_roas = round(unit_price / unit_margin, 2) if unit_price > 0 and unit_margin > 0 else None
        max_cpa = round(max(0.0, unit_margin), 2)
        flag = 'Healthy'
        if ret_rate >= 25.0 or (e['returned_units'] > 0 and e['net_units'] == 0):
            flag = 'High Returns'
        elif contrib <= 0:
            flag = 'Low / Negative Contribution'
        elif s in products and not products[s]['issues'] and contrib > 0:
            flag = 'Promote Next'

        sku_performance.append({
            'sku': s,
            'name': e['name'],
            'category': e['category'],
            'orders': len(e['orders']),
            'delivered_units': e['delivered_units'],
            'returned_units': e['returned_units'],
            'net_units': e['net_units'],
            'return_rate_pct': ret_rate,
            'gross_sales': float(e['gross_sales']),
            'refunds': float(e['refunds']),
            'net_sales': float(e['net_sales']),
            'product_cost': float(e['product_cost']),
            'fees': float(e['fees']),
            'contribution': contrib,
            'unit_margin': round(unit_margin, 2),
            'max_cpa': max_cpa,
            'break_even_roas': break_even_roas,
            'flag': flag,
        })
    sku_performance.sort(key=lambda x: (-x['contribution'], -x['net_sales'], x['sku']))

    # Campaign & Social channel attribution (strictly factual).
    traffic_map = {row['campaign']: int(row['sessions']) for row in raw_traffic if row.get('campaign')}
    camp_agg: dict[str, dict[str, Any]] = {}
    for r in filtered_orders:
        c = r['campaign']
        entry = camp_agg.setdefault(c, {
            'campaign': c,
            'attributed': r['attributed'],
            'orders': set(),
            'net_units': 0,
            'net_sales': Decimal(0),
            'contribution': Decimal(0),
            'sessions': traffic_map.get(c),
        })
        entry['orders'].add(r['order_id'])
        entry['net_units'] += r['net_units']
        entry['net_sales'] += r['net_sales']
        entry['contribution'] += r['contribution']

    for c_name, sess in traffic_map.items():
        if c_name not in camp_agg:
            camp_agg[c_name] = {
                'campaign': c_name,
                'attributed': True,
                'orders': set(),
                'net_units': 0,
                'net_sales': Decimal(0),
                'contribution': Decimal(0),
                'sessions': sess,
            }

    campaign_performance = [
        {
            'campaign': c,
            'attributed': v['attributed'],
            'orders': len(v['orders']),
            'net_units': v['net_units'],
            'net_sales': float(v['net_sales']),
            'contribution': float(v['contribution']),
            'sessions': v['sessions'],
        }
        for c, v in camp_agg.items()
    ]
    campaign_performance.sort(key=lambda x: (-x['net_sales'], -x['net_units'], x['campaign']))

    platform_posts = {}
    for post in social_posts:
        plat = post['platform']
        st = platform_posts.setdefault(plat, {'platform': plat, 'total_posts': 0, 'published': 0, 'scheduled': 0})
        st['total_posts'] += 1
        if post['status'] == 'Published':
            st['published'] += 1
        elif post['status'] == 'Scheduled':
            st['scheduled'] += 1

    # Bank settlements (tracked separately; NEVER counted as revenue).
    settlement_total = sum((Decimal(str(s['amount'])) for s in settlements), Decimal(0))
    settlement_variance = net_sales - settlement_total

    # Recommendations combining profitable catalog SKUs & sales ledger outcomes.
    high_return_skus = [x for x in sku_performance if x['returned_units'] > 0 and x['return_rate_pct'] >= 20.0]
    poor_campaigns = [c for c in campaign_performance if (c['sessions'] or 0) > 0 and c['net_units'] == 0]
    eligible_ready = [
        {
            'sku': p['sku'],
            'name': p['name'],
            'category': p['category'],
            'price': float(p['price']),
            'margin': float(p['margin']),
            'max_cpa': round(max(0.0, float(p['margin'])), 2),
            'break_even_roas': round(float(p['price']) / float(p['margin']), 2) if float(p['margin']) > 0 else None,
            'stock': p['stock'],
            'reason': 'Eligible catalog margin & stock' + (' + proven contribution' if p['sku'] in sku_map and float(sku_map[p['sku']]['contribution']) > 0 else ''),
        }
        for p in products.values()
        if not p['issues'] and p['sku'] not in {h['sku'] for h in high_return_skus}
    ]
    eligible_ready.sort(key=lambda x: (-(float(sku_map[x['sku']]['contribution']) if x['sku'] in sku_map else 0.0), -x['margin'], -x['stock']))

    return {
        'mode': mode,
        'filters': {'category': category, 'sku': sku, 'campaign': campaign, 'return_only': return_only},
        'summary': {
            'orders': len(order_ids),
            'delivered_units': delivered_units,
            'returned_units': returned_units,
            'net_units': net_units,
            'return_rate_pct': return_rate_pct,
            'gross_sales': float(gross_sales),
            'refunds': float(refunds),
            'net_sales': float(net_sales),
            'product_cost': float(product_cost),
            'order_fees': float(order_fees),
            'contribution': float(contribution),
            'ad_spend': float(ad_spend),
            'other_expenses': float(other_expenses),
            'total_expenses': float(total_expenses),
            'operating_profit': float(operating_profit),
            'profit_margin_pct': margin_pct,
            'attributed_net_sales': float(attributed_net_sales),
            'roas': roas_value,
            'roas_status': roas_status,
            'settlement_total': float(settlement_total),
            'settlement_variance': float(settlement_variance),
        },
        'reconciliation': {
            'is_unfiltered': is_unfiltered,
            'matches_engine': matches_engine,
            'engine_orders': engine_summary['orders'],
            'engine_units': engine_summary['units'],
            'engine_sales': engine_summary['sales'],
            'engine_profit': engine_summary['profit'],
            'bank_settlements_excluded_from_revenue': True,
        },
        'sku_performance': sku_performance,
        'campaign_performance': campaign_performance,
        'channel_posts': list(platform_posts.values()),
        'orders': [
            {
                **r,
                'sales': float(r['sales']),
                'refunds': float(r['refunds']),
                'net_sales': float(r['net_sales']),
                'net_product_cost': float(r['net_product_cost']),
                'fees': float(r['fees']),
                'contribution': float(r['contribution']),
            }
            for r in filtered_orders[:200]
        ],
        'expenses': expenses_list,
        'expense_by_type': {k: float(v) for k, v in expense_by_type.items()},
        'settlements': settlements,
        'recommendations': {
            'promote_next': eligible_ready[:6],
            'high_return_skus': high_return_skus[:6],
            'poor_campaigns': poor_campaigns[:6],
        },
    }


def export_analytics_workbook(mode: str) -> bytes:
    """Export reconciled Sales Analytics, Orders, Expenses, and Bank Settlements to multi-sheet .xlsx."""
    data = build_sales_analytics(mode)
    s = data['summary']
    rec = data['reconciliation']

    summary_rows = [
        ['Metric', 'Value', 'Notes'],
        ['Workspace Mode', mode, 'Demo or Store'],
        ['Delivered Orders', s['orders'], 'Unique order IDs'],
        ['Delivered Units', s['delivered_units'], 'Total units shipped'],
        ['Returned Units', s['returned_units'], 'Total units returned'],
        ['Net Units (After Returns)', s['net_units'], 'Delivered - Returned'],
        ['Gross Sales (INR)', s['gross_sales'], 'Before refunds'],
        ['Refunds (INR)', s['refunds'], 'Customer refunds'],
        ['Net Sales (INR)', s['net_sales'], 'Gross Sales - Refunds'],
        ['Product Cost (INR)', s['product_cost'], 'Order-line net_product_cost'],
        ['Order Fees (INR)', s['order_fees'], 'Order-line delivery/platform fees'],
        ['Contribution (INR)', s['contribution'], 'Net Sales - Product Cost - Order Fees'],
        ['Advertising Spend (INR)', s['ad_spend'], 'From expenses.csv (Advertising)'],
        ['Other Expenses (INR)', s['other_expenses'], 'Non-ad entries from expenses.csv'],
        ['Operating Profit (INR)', s['operating_profit'], 'Contribution - Advertising - Other Expenses'],
        ['Profit Margin (%)', s['profit_margin_pct'], 'Operating Profit / Net Sales'],
        ['ROAS', s['roas'] if s['roas'] is not None else 'N/A', s['roas_status']],
        ['Bank Settlements Reconciled (INR)', s['settlement_total'], 'Tracked separately; NEVER counted as additional revenue'],
        ['Engine Reconciliation Status', 'MATCHES' if rec['matches_engine'] else 'FILTERED', f"engine.earnings profit={rec['engine_profit']}"],
    ]

    sku_rows = [
        ['SKU', 'Product Name', 'Category', 'Orders', 'Delivered', 'Returned', 'Net Units', 'Return Rate %', 'Gross Sales', 'Refunds', 'Net Sales', 'Product Cost', 'Fees', 'Contribution', 'Flag']
    ]
    for item in data['sku_performance']:
        sku_rows.append([
            item['sku'], item['name'], item['category'], item['orders'], item['delivered_units'],
            item['returned_units'], item['net_units'], item['return_rate_pct'], item['gross_sales'],
            item['refunds'], item['net_sales'], item['product_cost'], item['fees'], item['contribution'], item['flag'],
        ])

    camp_rows = [['Campaign', 'Attributed', 'Tracked Sessions', 'Orders', 'Net Units', 'Net Sales', 'Contribution']]
    for c in data['campaign_performance']:
        camp_rows.append([c['campaign'], 'Yes' if c['attributed'] else 'No', c['sessions'] if c['sessions'] is not None else '', c['orders'], c['net_units'], c['net_sales'], c['contribution']])

    base = engine.directory(mode)
    orders_rows = [ORDER_HEADERS]
    for r in engine.records(base / 'orders.csv', ORDER_HEADERS):
        orders_rows.append([r[k] for k in ORDER_HEADERS])

    expenses_rows = [EXPENSE_HEADERS]
    for r in engine.records(base / 'expenses.csv', EXPENSE_HEADERS):
        expenses_rows.append([r[k] for k in EXPENSE_HEADERS])

    settle_rows = [['Reference', 'Settled At', 'Amount (INR)', 'Notes']]
    for st in data['settlements']:
        settle_rows.append([st['reference'], st['settled_at'], st['amount'], st['notes']])

    return write_xlsx_sheets([
        ('Summary & Reconciliation', summary_rows),
        ('SKU Performance', sku_rows),
        ('Campaign Attribution', camp_rows),
        ('Orders', orders_rows),
        ('Expenses', expenses_rows),
        ('Bank Settlements', settle_rows),
    ])


def export_analytics_csv(mode: str, kind: str = 'sku') -> tuple[str, str]:
    """Export SKU performance, orders, or expenses as UTF-8 CSV text."""
    buf = StringIO()
    if kind == 'orders':
        base = engine.directory(mode)
        rows = engine.records(base / 'orders.csv', ORDER_HEADERS)
        w = csv.DictWriter(buf, fieldnames=ORDER_HEADERS)
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue(), f'orders-{mode}.csv'
    if kind == 'expenses':
        base = engine.directory(mode)
        rows = engine.records(base / 'expenses.csv', EXPENSE_HEADERS)
        w = csv.DictWriter(buf, fieldnames=EXPENSE_HEADERS)
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue(), f'expenses-{mode}.csv'
    data = build_sales_analytics(mode)
    cols = ['sku', 'name', 'category', 'orders', 'delivered_units', 'returned_units', 'net_units', 'return_rate_pct', 'gross_sales', 'refunds', 'net_sales', 'product_cost', 'fees', 'contribution', 'flag']
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for item in data['sku_performance']:
        w.writerow({k: item[k] for k in cols})
    return buf.getvalue(), f'sku-profitability-{mode}.csv'


def add_expense_entry(mode: str, *, reference: str, expense_type: str, amount_val: Any) -> dict[str, Any]:
    """Append a non-duplicate expense record to expenses.csv."""
    if mode != 'store':
        raise ValueError('Switch to My store to add expenses.')
    ref = str(reference or '').strip()
    etype = str(expense_type or '').strip()
    amt = engine.amount(str(amount_val))
    if not ref or not etype or amt < 0:
        raise ValueError('Enter a valid expense reference, type, and nonnegative amount.')

    base = engine.directory(mode)
    path = base / 'expenses.csv'
    with engine.LOCK:
        existing = list(engine.records(path, EXPENSE_HEADERS))
        if any(r['reference'] == ref for r in existing):
            raise ValueError(f'Expense reference "{ref}" already exists.')
        existing.append({'reference': ref, 'type': etype, 'amount': str(amt)})
        _write_csv_atomic(path, EXPENSE_HEADERS, existing)
    return {'reference': ref, 'type': etype, 'amount': float(amt)}


def import_financial_upload(
    mode: str,
    *,
    filename: str = '',
    csv_text: str = '',
    base64_data: str = '',
    target: str = 'auto',
    storage: Optional[AgentStorage] = None,
) -> dict[str, Any]:
    """Import Orders, Expenses, or Bank Settlements from .xlsx or .csv with duplicate prevention."""
    if mode != 'store':
        raise ValueError('Switch to My store to import orders, expenses, or bank settlements.')
    storage = storage or AgentStorage()
    base = engine.directory(mode)

    sheets: dict[str, list[dict[str, str]]] = {}
    if base64_data:
        raw_bytes = base64.b64decode(base64_data)
        if filename.lower().endswith('.xlsx'):
            sheets = read_xlsx_sheets(raw_bytes)
        else:
            csv_text = raw_bytes.decode('utf-8-sig')

    if csv_text and not sheets:
        reader = csv.DictReader(StringIO(csv_text.lstrip('\ufeff')))
        if not reader.fieldnames:
            raise ValueError('CSV file has no headers.')
        rows = [dict(r) for r in reader if any(str(v or '').strip() for v in r.values())]
        sheets = {target if target != 'auto' else 'csv': rows}

    if not sheets:
        raise ValueError('Provide a .xlsx or .csv file to import.')

    imported_orders = 0
    skipped_dup_orders = 0
    imported_expenses = 0
    skipped_dup_expenses = 0
    imported_settlements = 0

    with engine.LOCK:
        orders_path = base / 'orders.csv'
        cur_orders = list(engine.records(orders_path, ORDER_HEADERS))
        existing_order_keys = {(r['order_id'], r['line_id']) for r in cur_orders}

        exp_path = base / 'expenses.csv'
        cur_expenses = list(engine.records(exp_path, EXPENSE_HEADERS))
        existing_exp_refs = {r['reference'] for r in cur_expenses}

        for sheet_name, rows in sheets.items():
            if not rows:
                continue
            sample_keys = {k.strip().lower() for k in rows[0].keys()}
            s_lower = sheet_name.lower()

            if {'order_id', 'sku'}.issubset(sample_keys) or s_lower == 'orders' or target == 'orders':
                for r in rows:
                    norm = {k.strip().lower(): str(v or '').strip() for k, v in r.items()}
                    oid = norm.get('order_id', '')
                    lid = norm.get('line_id', '1') or '1'
                    sku_val = norm.get('sku', '')
                    if not oid or not sku_val:
                        raise ValueError('Imported order row is missing order_id or sku.')
                    if (oid, lid) in existing_order_keys:
                        skipped_dup_orders += 1
                        continue
                    delivered = int(float(norm.get('delivered_units', '0') or '0'))
                    returned = int(float(norm.get('returned_units', '0') or '0'))
                    sales = engine.amount(norm.get('sales', '0') or '0')
                    refunds = engine.amount(norm.get('refunds', '0') or '0')
                    cost = engine.amount(norm.get('net_product_cost', '0') or '0')
                    fees = engine.amount(norm.get('fees', '0') or '0')
                    if not 0 <= returned <= delivered or refunds > sales:
                        raise ValueError(f'Invalid returned units or refunds for order {oid}-{lid}.')
                    existing_order_keys.add((oid, lid))
                    cur_orders.append({
                        'order_id': oid,
                        'line_id': lid,
                        'sku': sku_val,
                        'delivered_units': str(delivered),
                        'returned_units': str(returned),
                        'sales': str(sales),
                        'refunds': str(refunds),
                        'net_product_cost': str(cost),
                        'fees': str(fees),
                        'campaign': norm.get('campaign', ''),
                    })
                    imported_orders += 1

            elif {'reference', 'amount'}.issubset(sample_keys) and ('settlement' in s_lower or target == 'settlements'):
                for r in rows:
                    norm = {k.strip().lower(): str(v or '').strip() for k, v in r.items()}
                    ref = norm.get('reference', '')
                    amt = float(engine.amount(norm.get('amount', '0') or '0'))
                    storage.add_bank_settlement(
                        mode=mode,
                        reference=ref,
                        settled_at=norm.get('settled_at', '') or engine.now().isoformat(),
                        amount=amt,
                        notes=norm.get('notes', ''),
                    )
                    imported_settlements += 1

            elif {'reference', 'type', 'amount'}.issubset(sample_keys) or s_lower == 'expenses' or target == 'expenses':
                for r in rows:
                    norm = {k.strip().lower(): str(v or '').strip() for k, v in r.items()}
                    ref = norm.get('reference', '')
                    etype = norm.get('type', 'Other') or 'Other'
                    amt = engine.amount(norm.get('amount', '0') or '0')
                    if not ref:
                        raise ValueError('Expense row is missing reference.')
                    if ref in existing_exp_refs:
                        skipped_dup_expenses += 1
                        continue
                    existing_exp_refs.add(ref)
                    cur_expenses.append({'reference': ref, 'type': etype, 'amount': str(amt)})
                    imported_expenses += 1
            else:
                # Summary/SKU export sheets are informational and skipped safely on multi-sheet workbook re-import.
                if s_lower in ('summary & reconciliation', 'sku performance', 'campaign attribution'):
                    continue
                raise ValueError(f'Unrecognized sheet/CSV columns in "{sheet_name}".')

        if imported_orders:
            _write_csv_atomic(orders_path, ORDER_HEADERS, cur_orders)
        if imported_expenses:
            _write_csv_atomic(exp_path, EXPENSE_HEADERS, cur_expenses)

    return {
        'imported_orders': imported_orders,
        'skipped_duplicate_orders': skipped_dup_orders,
        'imported_expenses': imported_expenses,
        'skipped_duplicate_expenses': skipped_dup_expenses,
        'imported_settlements': imported_settlements,
    }
