(function () {
  let profitChart = null;
  let lastAnalytics = null;

  function make(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }

  function inr(n) {
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(n || 0));
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(Error('Could not read file'));
      reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
      reader.readAsDataURL(file);
    });
  }

  function renderProfitChart(data) {
    const canvas = document.getElementById('an-profit-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (profitChart) {
      profitChart.destroy();
      profitChart = null;
    }
    const s = data.summary || {};
    profitChart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: ['Gross Sales', 'Refunds', 'Net Sales', 'Product Cost', 'Order Fees', 'Ad Spend', 'Other Expenses', 'Operating Profit'],
        datasets: [{
          label: 'INR (₹)',
          data: [s.gross_sales || 0, s.refunds || 0, s.net_sales || 0, s.product_cost || 0, s.order_fees || 0, s.ad_spend || 0, s.other_expenses || 0, s.operating_profit || 0],
          backgroundColor: ['#123E3B', '#D97706', '#145F53', '#6B7280', '#9CA3AF', '#2563EB', '#A855F7', (s.operating_profit || 0) >= 0 ? '#10B981' : '#DC2626'],
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: {
            ticks: { callback: v => '₹' + Number(v).toLocaleString('en-IN') },
            grid: { color: 'rgba(128,128,128,0.12)' },
          },
          x: { grid: { display: false } },
        },
      },
    });
  }

  function renderRecommendations(data) {
    const host = document.getElementById('an-recommendations');
    if (!host) return;
    host.replaceChildren();
    const rec = data.recommendations || {};

    const promote = rec.promote_next || [];
    const returns = rec.high_return_skus || [];
    const poor = rec.poor_campaigns || [];

    host.append(make('strong', 'Promote Next (Profitable & Eligible)'));
    if (!promote.length) {
      host.append(make('p', 'No eligible ready products found.', 'hint'));
    } else {
      const ul = make('ul', '', 'checklist-list');
      for (const p of promote.slice(0, 4)) {
        const li = document.createElement('li');
        li.textContent = `${p.name} (${p.sku}) · Margin ${inr(p.margin)} · Stock ${p.stock} — `;
        const btn = make('button', 'Create Campaign');
        btn.type = 'button';
        btn.style.padding = '2px 8px';
        btn.style.fontSize = '11px';
        btn.onclick = () => {
          if (typeof promoteProductSkus === 'function') promoteProductSkus([p.sku]);
          else window.StudioUI?.navigate('campaigns');
        };
        li.append(btn);
        ul.append(li);
      }
      host.append(ul);
    }

    host.append(make('strong', 'Return-Risk Watchlist', 'hint'));
    if (!returns.length) {
      host.append(make('p', 'No SKUs above 20% return rate under active filters.', 'hint'));
    } else {
      const ul2 = make('ul', '', 'checklist-list');
      for (const h of returns) ul2.append(make('li', `${h.name} (${h.sku}): ${h.return_rate_pct}% returns (${h.returned_units}/${h.delivered_units})`));
      host.append(ul2);
    }

    if (poor.length) {
      host.append(make('strong', 'Traffic Without Orders'));
      const ul3 = make('ul', '', 'checklist-list');
      for (const c of poor) ul3.append(make('li', `${c.campaign}: ${c.sessions} sessions, 0 net units`));
      host.append(ul3);
    }
  }

  function renderSkuTable(data) {
    const host = document.getElementById('an-sku-table');
    if (!host) return;
    const rows = data.sku_performance || [];
    if (!rows.length) {
      host.replaceChildren(make('p', 'No order-line SKU data found for current filters.', 'hint'));
      return;
    }
    const t = document.createElement('table');
    t.className = 'data-table';
    const th = t.createTHead().insertRow();
    ['SKU / Product', 'Orders', 'Net Units', 'Returns', 'Net Sales', 'Cost + Fees', 'Contribution', 'Max CPA / BE-ROAS', 'Signal'].forEach(c => th.append(make('th', c)));
    const tb = t.createTBody();
    for (const r of rows) {
      const tr = tb.insertRow();
      const beTxt = r.break_even_roas !== null && r.break_even_roas !== undefined
        ? `${inr(r.max_cpa)} / ${r.break_even_roas}x`
        : 'N/A';
      tr.append(
        make('td', `${r.name} (${r.sku})`),
        make('td', String(r.orders)),
        make('td', String(r.net_units)),
        make('td', `${r.returned_units}/${r.delivered_units} (${r.return_rate_pct}%)`),
        make('td', inr(r.net_sales)),
        make('td', inr(r.product_cost + r.fees)),
        make('td', inr(r.contribution), r.contribution > 0 ? 'margin-positive' : 'margin-negative'),
        make('td', beTxt)
      );
      const tdFlag = tr.insertCell();
      const b = make('span', r.flag, 'badge ' + (r.flag === 'Promote Next' ? 'success' : r.flag === 'High Returns' ? 'warning' : r.contribution <= 0 ? 'error' : ''));
      tdFlag.append(b);
    }
    const scroller = make('div', '', 'table-scroll');
    scroller.append(t);
    host.replaceChildren(scroller);
  }

  function renderExpensesView(data) {
    const sumHost = document.getElementById('exp-summary-cards');
    const expHost = document.getElementById('exp-ledger-table');
    const settleHost = document.getElementById('settle-table');
    const isStore = (typeof mode === 'function' ? mode() : 'store') === 'store';
    const expBox = document.getElementById('exp-add-box');
    const setBox = document.getElementById('settle-add-box');
    if (expBox) expBox.hidden = !isStore;
    if (setBox) setBox.hidden = !isStore;

    const s = data.summary || {};
    if (sumHost) {
      sumHost.replaceChildren();
      for (const [k, v] of [
        ['Net Sales (After Refunds)', inr(s.net_sales)],
        ['Product Cost + Order Fees', inr((s.product_cost || 0) + (s.order_fees || 0))],
        ['Advertising Spend', inr(s.ad_spend)],
        ['Other Expenses', inr(s.other_expenses)],
        ['Operating Profit', inr(s.operating_profit)],
        ['Bank Settlements (Cash)', inr(s.settlement_total)],
      ]) {
        const box = make('div', '', 'catalog-stat');
        box.append(make('small', k), make('b', v));
        sumHost.append(box);
      }
    }

    if (expHost) {
      const list = data.expenses || [];
      if (!list.length) {
        expHost.replaceChildren(make('p', 'No entries in expenses.csv yet.', 'hint'));
      } else {
        const t = document.createElement('table');
        t.className = 'data-table';
        const th = t.createTHead().insertRow();
        ['Reference', 'Type', 'Amount'].forEach(c => th.append(make('th', c)));
        const tb = t.createTBody();
        for (const e of list) {
          const tr = tb.insertRow();
          tr.append(make('td', e.reference), make('td', e.type), make('td', inr(e.amount)));
        }
        expHost.replaceChildren(t);
      }
    }

    if (settleHost) {
      const list = data.settlements || [];
      if (!list.length) {
        settleHost.replaceChildren(make('p', 'No bank settlements recorded yet.', 'hint'));
      } else {
        const t = document.createElement('table');
        t.className = 'data-table';
        const th = t.createTHead().insertRow();
        ['Reference', 'Settled At', 'Amount', 'Notes'].forEach(c => th.append(make('th', c)));
        const tb = t.createTBody();
        for (const st of list) {
          const tr = tb.insertRow();
          tr.append(make('td', st.reference), make('td', (st.settled_at || '').slice(0, 16)), make('td', inr(st.amount)), make('td', st.notes || '—'));
        }
        settleHost.replaceChildren(t);
      }
    }
  }

  async function refreshSalesAnalytics(route) {
    const m = typeof mode === 'function' ? mode() : 'store';
    const xlsxLink = document.getElementById('an-export-xlsx');
    const csvLink = document.getElementById('an-export-csv');
    const ordLink = document.getElementById('an-export-orders-csv');
    const expLink = document.getElementById('an-export-expenses-csv');
    if (xlsxLink) xlsxLink.href = '/api/analytics/export.xlsx?' + new URLSearchParams({ mode: m });
    if (csvLink) csvLink.href = '/api/analytics/export.csv?' + new URLSearchParams({ mode: m, kind: 'sku' });
    if (ordLink) ordLink.href = '/api/analytics/export.csv?' + new URLSearchParams({ mode: m, kind: 'orders' });
    if (expLink) expLink.href = '/api/analytics/export.csv?' + new URLSearchParams({ mode: m, kind: 'expenses' });

    const params = new URLSearchParams({ mode: m });
    const sku = document.getElementById('an-filter-sku')?.value.trim() || '';
    const cat = document.getElementById('an-filter-cat')?.value || '';
    const camp = document.getElementById('an-filter-camp')?.value || '';
    const retOnly = document.getElementById('an-filter-returns')?.checked ? '1' : '';
    if (sku) params.set('sku', sku);
    if (cat) params.set('category', cat);
    if (camp) params.set('campaign', camp);
    if (retOnly) params.set('return_only', '1');

    const res = await fetch('/api/analytics?' + params);
    const data = await res.json();
    if (!res.ok) throw Error(data.error || 'Could not load sales analytics.');
    lastAnalytics = data;

    // Populate filter dropdowns from full catalog/campaigns.
    const catSel = document.getElementById('an-filter-cat');
    if (catSel && typeof state !== 'undefined' && state?.products) {
      const prev = catSel.value;
      const cats = [...new Set(state.products.map(p => p.category).filter(Boolean))].sort();
      catSel.replaceChildren(new Option('All categories', ''));
      cats.forEach(c => catSel.add(new Option(c, c)));
      if (cats.includes(prev)) catSel.value = prev;
    }
    const campSel = document.getElementById('an-filter-camp');
    if (campSel) {
      const prevC = campSel.value;
      const camps = (data.campaign_performance || []).map(c => c.campaign).filter(Boolean);
      campSel.replaceChildren(new Option('All campaigns', ''));
      camps.forEach(c => campSel.add(new Option(c, c)));
      if (camps.includes(prevC)) campSel.value = prevC;
    }

    const recBadge = document.getElementById('an-reconcile-badge');
    if (recBadge) {
      if (data.reconciliation?.is_unfiltered) {
        recBadge.className = 'badge ' + (data.reconciliation.matches_engine ? 'success' : 'error');
        recBadge.textContent = data.reconciliation.matches_engine ? 'Reconciled with Engine Totals' : 'Reconciliation Mismatch';
      } else {
        recBadge.className = 'badge info';
        recBadge.textContent = 'Filtered View Active';
      }
    }

    const extraKpis = document.getElementById('an-kpis-extra');
    if (extraKpis) {
      const s = data.summary || {};
      extraKpis.replaceChildren();
      for (const [k, v] of [
        ['Gross Sales', inr(s.gross_sales)],
        ['Refunds', inr(s.refunds)],
        ['Contribution', inr(s.contribution)],
        ['Return Rate', `${s.return_rate_pct ?? 0}% (${s.returned_units}/${s.delivered_units})`],
        ['Profit Margin', `${s.profit_margin_pct ?? 0}%`],
        ['ROAS (Verified Only)', s.roas !== null ? `${s.roas}x` : 'Insufficient attribution'],
      ]) {
        const box = make('div', '', 'catalog-stat');
        box.append(make('small', k), make('b', v));
        extraKpis.append(box);
      }
    }

    renderProfitChart(data);
    renderRecommendations(data);
    renderSkuTable(data);
    renderExpensesView(data);
  }

  document.addEventListener('DOMContentLoaded', () => {
    ['#an-filter-sku', '#an-filter-cat', '#an-filter-camp', '#an-filter-returns'].forEach(sel => {
      const el = document.querySelector(sel);
      if (!el) return;
      el.addEventListener(sel === '#an-filter-sku' ? 'input' : 'change', () => refreshSalesAnalytics().catch(() => {}));
    });
    document.getElementById('an-filter-reset')?.addEventListener('click', () => {
      if (document.getElementById('an-filter-sku')) document.getElementById('an-filter-sku').value = '';
      if (document.getElementById('an-filter-cat')) document.getElementById('an-filter-cat').value = '';
      if (document.getElementById('an-filter-camp')) document.getElementById('an-filter-camp').value = '';
      if (document.getElementById('an-filter-returns')) document.getElementById('an-filter-returns').checked = false;
      refreshSalesAnalytics().catch(() => {});
    });

    document.getElementById('an-import-file')?.addEventListener('change', async e => {
      const f = e.target.files?.[0];
      e.target.value = '';
      if (!f) return;
      try {
        const b64 = await fileToBase64(f);
        const res = await fetch('/api/analytics/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: typeof mode === 'function' ? mode() : 'store', filename: f.name, base64: b64 }),
        });
        const j = await res.json();
        if (!res.ok) throw Error(j.error || 'Import failed.');
        if (typeof notice === 'function') notice(j.message);
        if (typeof refresh === 'function') await refresh();
        await refreshSalesAnalytics();
      } catch (err) {
        if (typeof notice === 'function') notice(err.message, true);
      }
    });

    document.getElementById('exp-add-btn')?.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/expenses', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: typeof mode === 'function' ? mode() : 'store',
            reference: document.getElementById('exp-ref')?.value.trim() || '',
            type: document.getElementById('exp-type')?.value || 'Other',
            amount: document.getElementById('exp-amount')?.value || '',
          }),
        });
        const j = await res.json();
        if (!res.ok) throw Error(j.error || 'Could not record expense.');
        if (document.getElementById('exp-ref')) document.getElementById('exp-ref').value = '';
        if (document.getElementById('exp-amount')) document.getElementById('exp-amount').value = '';
        if (typeof notice === 'function') notice(j.message);
        if (typeof refresh === 'function') await refresh();
        await refreshSalesAnalytics();
      } catch (err) {
        if (typeof notice === 'function') notice(err.message, true);
      }
    });

    document.getElementById('settle-add-btn')?.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/settlements', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: typeof mode === 'function' ? mode() : 'store',
            reference: document.getElementById('settle-ref')?.value.trim() || '',
            amount: Number(document.getElementById('settle-amount')?.value || 0),
            notes: document.getElementById('settle-notes')?.value.trim() || '',
          }),
        });
        const j = await res.json();
        if (!res.ok) throw Error(j.error || 'Could not record settlement.');
        if (document.getElementById('settle-ref')) document.getElementById('settle-ref').value = '';
        if (document.getElementById('settle-amount')) document.getElementById('settle-amount').value = '';
        if (document.getElementById('settle-notes')) document.getElementById('settle-notes').value = '';
        if (typeof notice === 'function') notice(j.message);
        await refreshSalesAnalytics();
      } catch (err) {
        if (typeof notice === 'function') notice(err.message, true);
      }
    });
  });

  window.refreshSalesAnalytics = refreshSalesAnalytics;
})();
