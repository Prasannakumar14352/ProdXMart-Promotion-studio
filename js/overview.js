/* Screen 1 — Business Overview Dashboard */
(function () {
  let revChart = null;
  let chartMode = 'rev_vs_exp';

  const inr = n => new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(Number(n || 0));

  function greetingText() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
  }

  function formattedToday() {
    return new Date().toLocaleDateString('en-IN', {
      timeZone: 'Asia/Kolkata',
      weekday: 'long',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }

  function relTime(iso) {
    if (!iso) return '—';
    const diffSec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return new Date(iso).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short' });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderKpis(data) {
    const grid = document.getElementById('ov-kpis');
    if (!grid) return;
    grid.replaceChildren();
    const k = data.kpis;
    const isDemo = data.mode === 'demo';
    const cards = [
      {
        label: 'Total products',
        val: String(k.total_products),
        meta: isDemo ? 'Demo catalogue workspace' : 'Local store catalogue',
        cls: '',
        icon: 'package',
      },
      {
        label: 'Ready to promote',
        val: String(k.ready_products),
        meta: k.attention_products > 0
          ? `${k.ready_pct}% eligible · ${k.attention_products} need attention`
          : `${k.ready_pct}% of catalogue healthy`,
        cls: k.attention_products > 0 ? 'warn' : (k.ready_products > 0 ? 'good' : ''),
        icon: 'check-circle-2',
      },
      {
        label: 'Pending approvals',
        val: String(k.pending_approvals),
        meta: k.pending_approvals > 0 ? 'Requires human sign-off' : 'Approval queue clear',
        cls: k.pending_approvals > 0 ? 'warn' : '',
        icon: 'shield-check',
      },
      {
        label: 'Scheduled / Published',
        val: `${k.scheduled_promotions} / ${k.published_promotions}`,
        meta: `${k.scheduled_promotions} scheduled · ${k.published_promotions} published`,
        cls: '',
        icon: 'calendar-check',
      },
      {
        label: 'Net sales',
        val: inr(k.net_sales),
        meta: `${k.orders} order(s) · ${k.delivered_units - k.returned_units} net units`,
        cls: k.net_sales > 0 ? 'good' : '',
        icon: 'indian-rupee',
      },
      {
        label: 'Operating profit',
        val: inr(k.operating_profit),
        meta: k.net_sales > 0 ? `${k.profit_margin_pct}% operating margin` : 'No order revenue imported yet',
        cls: k.operating_profit > 0 ? 'good' : (k.operating_profit < 0 ? 'bad' : ''),
        icon: 'trending-up',
      },
    ];

    for (const c of cards) {
      const card = el('div', 'kpi-card');
      const top = el('div', 'kpi-top');
      top.append(el('span', '', c.label));
      const iconWrap = el('span', '');
      iconWrap.innerHTML = `<i data-lucide="${c.icon}" width="16" height="16"></i>`;
      top.append(iconWrap);
      card.append(top, el('div', 'kpi-value', c.val), el('div', 'kpi-meta ' + c.cls, c.meta));
      grid.append(card);
    }
  }

  function renderRevenueChart(data) {
    const canvas = document.getElementById('ov-rev-chart');
    const emptyEl = document.getElementById('ov-rev-empty');
    if (!canvas || !emptyEl) return;

    const fb = data.financial_breakdown;
    const hasNumbers = fb.gross_sales > 0 || fb.total_expenses > 0 || fb.product_cost > 0 || fb.ad_spend > 0 || fb.other_expenses > 0;
    if (!hasNumbers) {
      canvas.hidden = true;
      emptyEl.hidden = false;
      if (revChart) { revChart.destroy(); revChart = null; }
      return;
    }
    canvas.hidden = false;
    emptyEl.hidden = true;

    if (typeof window.Chart !== 'function') return;
    if (revChart) { revChart.destroy(); revChart = null; }

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const textCol = isDark ? '#94A3B8' : '#64748B';
    const gridCol = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(18,62,59,0.07)';

    if (chartMode === 'profit') {
      revChart = new window.Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
          labels: ['Gross Sales', 'Refunds', 'Net Sales', 'Product Cost', 'Fees & Ads', 'Operating Profit'],
          datasets: [{
            label: 'Amount (₹)',
            data: [fb.gross_sales, fb.refunds, fb.net_sales, fb.product_cost, fb.order_fees + fb.ad_spend + fb.other_expenses, fb.operating_profit],
            backgroundColor: ['#145F53', '#F59E0B', '#10B981', '#64748B', '#94A3B8', fb.operating_profit >= 0 ? '#16A34A' : '#DC2626'],
            borderRadius: 6,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { color: textCol } },
            y: { grid: { color: gridCol }, ticks: { color: textCol } },
          },
        },
      });
      return;
    }

    revChart = new window.Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: ['Net Sales', 'Product Cost', 'Order Fees', 'Ad Spend', 'Other Expenses', 'Operating Profit'],
        datasets: [
          {
            label: 'Revenue / Profit (₹)',
            data: [fb.net_sales, 0, 0, 0, 0, Math.max(0, fb.operating_profit)],
            backgroundColor: '#145F53',
            borderRadius: 6,
          },
          {
            label: 'Costs & Expenses (₹)',
            data: [0, fb.product_cost, fb.order_fees, fb.ad_spend, fb.other_expenses, fb.operating_profit < 0 ? Math.abs(fb.operating_profit) : 0],
            backgroundColor: '#9FB8AE',
            borderRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: textCol } } },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { color: textCol } },
          y: { stacked: true, grid: { color: gridCol }, ticks: { color: textCol } },
        },
      },
    });
  }

  function renderCampaignPerformance(data) {
    const box = document.getElementById('ov-campaigns');
    if (!box) return;
    box.replaceChildren();
    const list = data.campaigns || [];
    if (!list.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('strong', '', 'No attributed campaign sales yet'), el('p', '', 'Campaign performance bars populate automatically once orders carry utm_campaign IDs.'));
      box.append(empty);
      return;
    }
    const maxSales = Math.max(1, ...list.map(c => c.net_sales || 0));
    const stack = el('div', 'list-stack');
    for (const c of list.slice(0, 5)) {
      const row = el('div', 'prog-row');
      const head = el('div', 'kpi-top');
      head.append(el('span', '', `${c.campaign} (${c.units} units)`), el('strong', '', inr(c.net_sales)));
      const track = el('div', 'prog-track');
      const fill = el('div', 'prog-fill');
      fill.style.width = `${Math.max(4, Math.round((c.net_sales / maxSales) * 100))}%`;
      track.append(fill);
      row.append(head, track);
      stack.append(row);
    }
    box.append(stack);
  }

  function renderRecommendedProducts(data) {
    const box = document.getElementById('ov-recommended');
    if (!box) return;
    box.replaceChildren();
    const items = data.recommended_products || [];
    if (!items.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('strong', '', 'No eligible products ready to promote'), el('p', '', 'Add products with stock ≥ 3, positive margin, photos and media permission in Product Catalogue.'));
      box.append(empty);
      return;
    }
    const scroller = el('div', 'table-scroll');
    const table = el('table', 'data-table');
    const thead = table.createTHead().insertRow();
    ['Product', 'Price', 'Margin', ''].forEach(h => thead.append(el('th', '', h)));
    const tbody = table.createTBody();
    for (const p of items) {
      const tr = tbody.insertRow();
      const prodCell = tr.insertCell();
      const wrap = el('div', 'list-row-left');
      if (p.has_photo) {
        const img = new Image();
        img.src = '/photo?' + new URLSearchParams({ mode: data.mode, sku: p.sku });
        img.alt = '';
        img.width = 36;
        img.height = 36;
        img.style.borderRadius = '6px';
        img.style.objectFit = 'cover';
        wrap.append(img);
      }
      const meta = el('div', '');
      const titleDiv = el('div', 'list-row-title', p.name);
      titleDiv.title = p.name;
      meta.append(titleDiv, el('div', 'list-row-sub', `${p.category} · ${p.sku} · ${p.stock} in stock`));
      wrap.append(meta);
      prodCell.append(wrap);
      tr.insertCell().textContent = inr(p.price);
      tr.insertCell().textContent = inr(p.margin);
      const actCell = tr.insertCell();
      const btn = el('button', 'btn btn-sm btn-ghost', 'Promote');
      btn.type = 'button';
      btn.onclick = () => {
        if (typeof promoteProductSkus === 'function') promoteProductSkus([p.sku]);
        else {
          const mktSelect = document.getElementById('mkt-skus');
          if (mktSelect) Array.from(mktSelect.options).forEach(opt => { opt.selected = opt.value === p.sku; });
          window.StudioUI?.navigate('campaigns');
        }
      };
      actCell.append(btn);
    }
    scroller.append(table);
    box.append(scroller);
  }

  function renderAgentActivity(data) {
    const box = document.getElementById('ov-agent-activity');
    const livePill = document.getElementById('ov-agent-live');
    if (livePill) {
      const active = data.active_agent_count > 0;
      livePill.className = 'badge ' + (active ? 'success' : '');
      livePill.textContent = active ? `Live (${data.active_agent_count})` : 'Idle · Manual';
    }
    if (!box) return;
    box.replaceChildren();
    const runs = data.agent_activity || [];
    if (!runs.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('strong', '', 'No agent runs recorded yet'), el('p', '', 'Agents execute manually when you click Run in AI Agent Center.'));
      box.append(empty);
      return;
    }
    const stack = el('div', 'list-stack');
    for (const r of runs.slice(0, 5)) {
      const row = el('div', 'list-row');
      const left = el('div', 'list-row-left');
      const icon = el('span', '');
      icon.innerHTML = '<i data-lucide="bot" width="16" height="16"></i>';
      const txt = el('div', '');
      const summary = r.output?.summary || r.error_message || r.progress_message || r.status;
      txt.append(el('div', 'list-row-title', `${r.agent_id} · ${r.status}`), el('div', 'list-row-sub', summary));
      left.append(icon, txt);
      row.append(left, el('span', 'list-row-sub', relTime(r.finished_at || r.created_at)));
      stack.append(row);
    }
    box.append(stack);
  }

  function renderBottomPanels(data) {
    const attBox = document.getElementById('ov-attention');
    const attBadge = document.getElementById('ov-attention-count');
    if (attBadge) attBadge.textContent = String(data.kpis.attention_products || 0);
    if (attBox) {
      attBox.replaceChildren();
      const issues = data.attention_breakdown || [];
      if (!issues.length) {
        const empty = el('div', 'empty-state');
        empty.append(el('strong', '', 'No products need attention'), el('p', '', 'All products meet stock, margin and media requirements.'));
        attBox.append(empty);
      } else {
        const stack = el('div', 'list-stack');
        for (const item of issues) {
          const row = el('button', 'list-row');
          row.type = 'button';
          row.style.width = '100%';
          row.style.cursor = 'pointer';
          row.onclick = () => {
            const f = document.getElementById('filter');
            if (f) { f.value = 'hold'; f.dispatchEvent(new Event('change')); }
            window.StudioUI?.navigate('catalogue');
          };
          const left = el('div', 'list-row-left');
          left.innerHTML = `<i data-lucide="alert-circle" width="16" height="16"></i><div><div class="list-row-title">${item.count} product(s) · ${item.issue}</div><div class="list-row-sub">Click to review in Catalogue</div></div>`;
          row.append(left, el('span', '', '›'));
          stack.append(row);
        }
        attBox.append(stack);
      }
    }

    const upBox = document.getElementById('ov-upcoming');
    if (upBox) {
      upBox.replaceChildren();
      const upcoming = data.upcoming_promotions || [];
      if (!upcoming.length) {
        const empty = el('div', 'empty-state');
        empty.append(el('strong', '', 'No upcoming promotions'), el('p', '', 'Approve and schedule a campaign or Reel to see it here.'));
        upBox.append(empty);
      } else {
        const stack = el('div', 'list-stack');
        for (const u of upcoming.slice(0, 4)) {
          const row = el('div', 'list-row');
          const d = u.due ? new Date(u.due).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short' }) : 'Draft';
          const left = el('div', 'list-row-left');
          left.append(el('span', 'badge info', d), el('div', '', `${u.title} (${u.status})`));
          row.append(left);
          stack.append(row);
        }
        upBox.append(stack);
      }
    }

    const healthBox = document.getElementById('ov-health');
    if (healthBox) {
      healthBox.replaceChildren();
      const stack = el('div', 'list-stack');
      for (const info of Object.values(data.integration_health || {})) {
        const row = el('div', 'list-row');
        const cls = ['ready', 'connected', 'configured', 'manual'].includes(info.status)
          ? 'success'
          : (info.status === 'optional' ? 'info' : (info.status === 'paused' || info.status === 'missing' ? 'error' : 'warning'));
        const left = el('div', '');
        left.append(el('div', 'list-row-title', info.label), el('div', 'list-row-sub', info.detail));
        row.append(left, el('span', 'badge ' + cls, info.status === 'optional' ? 'Optional' : info.status));
        stack.append(row);
      }
      healthBox.append(stack);
    }
  }

  async function refreshOverview() {
    const mode = document.getElementById('mode')?.value || 'store';
    const range = document.getElementById('ov-range')?.value || 'month';
    const greetEl = document.getElementById('ov-greeting');
    const dateEl = document.getElementById('ov-date');
    if (greetEl) greetEl.textContent = `${greetingText()}, ProdXMart`;
    if (dateEl) dateEl.textContent = `${formattedToday()} · ${mode === 'demo' ? 'Demo catalogue workspace (sample data)' : 'My Store workspace'}`;

    const res = await fetch('/api/overview?' + new URLSearchParams({ mode, range }));
    const data = await res.json();
    if (!res.ok) throw Error(data.error || 'Could not load overview.');

    renderKpis(data);
    renderRevenueChart(data);
    renderCampaignPerformance(data);
    renderRecommendedProducts(data);
    renderAgentActivity(data);
    renderBottomPanels(data);

    window.StudioUI?.updateNotifications(data);
    window.StudioUI?.hydrateIcons(document.getElementById('overview'));
  }

  document.addEventListener('DOMContentLoaded', () => {
    const rangeSel = document.getElementById('ov-range');
    if (rangeSel) rangeSel.onchange = () => refreshOverview().catch(e => window.StudioUI?.showToast(e.message, true));

    document.querySelectorAll('[data-ov-chart]').forEach(btn => {
      btn.onclick = () => {
        chartMode = btn.dataset.ovChart;
        document.querySelectorAll('[data-ov-chart]').forEach(b => b.classList.toggle('btn-primary', b === btn));
        refreshOverview().catch(() => {});
      };
    });

    const btnImport = document.getElementById('ov-import-btn');
    if (btnImport) btnImport.onclick = () => window.StudioUI?.navigate('catalogue');

    const btnCreate = document.getElementById('ov-create-campaign-btn');
    if (btnCreate) btnCreate.onclick = () => window.StudioUI?.navigate('campaigns');

    const btnRunAgents = document.getElementById('ov-run-agents-btn');
    if (btnRunAgents) btnRunAgents.onclick = () => window.StudioUI?.navigate('agents');
  });

  window.refreshOverview = refreshOverview;
})();
