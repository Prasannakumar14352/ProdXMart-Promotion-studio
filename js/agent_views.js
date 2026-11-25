(function () {
  let latestIntegrations = null;
  let activeSettingsTab = 'ai';

  function esc(val) {
    return String(val ?? '');
  }

  function makeEl(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }

  function badgeClass(status) {
    const s = String(status || '').toLowerCase();
    if (['succeeded', 'ready', 'connected', 'configured'].includes(s)) return 'badge success';
    if (['running', 'queued', 'manual'].includes(s)) return 'badge info';
    if (['failed', 'missing', 'paused', 'error'].includes(s)) return 'badge error';
    if (['pending', 'not_configured', 'standby', 'cancelled'].includes(s)) return 'badge warning';
    return 'badge';
  }

  function renderPipeline(agentState) {
    const host = document.getElementById('agent-pipeline-track');
    if (!host) return;
    const byId = new Map((agentState?.agents || []).map(a => [a.agent_id, a]));
    const steps = [
      { id: 'catalog_agent', label: '1. Catalog Sync', route: 'agents' },
      { id: 'catalogue_auditor', label: '2. Catalogue Audit', route: 'agents' },
      { id: 'reel_copy_strategist', label: '3. Copy Strategy', route: 'agents' },
      { id: 'performance_analyst', label: '4. Performance Analyst', route: 'analytics' },
      { id: 'marketing_agent', label: '5. Marketing Campaign', route: 'campaigns' },
      { id: 'video_agent', label: '6. Video Production', route: 'video' },
      { id: 'qa_agent', label: '7. QA Gate', route: 'video' },
      { id: 'social_agent', label: '8. Approval & Package', route: 'approvals' },
      { id: 'sales_analytics', label: '9. Sales & Profit Ledger', route: 'analytics', ledger: true },
    ];

    host.replaceChildren();
    steps.forEach((step, idx) => {
      const a = byId.get(step.id);
      const status = step.ledger ? 'ready' : (!a ? 'not_configured' : (!a.enabled ? 'disabled' : (a.status || 'idle')));
      const box = document.createElement('button');
      box.type = 'button';
      box.className = 'pipeline-step';
      box.onclick = () => window.StudioUI?.navigate(step.route);

      const top = makeEl('div', '', 'meta');
      top.append(makeEl('strong', step.label));
      const pill = makeEl('span', step.ledger ? 'Active' : status, badgeClass(status));
      top.append(pill);

      const sub = makeEl(
        'small',
        step.ledger
          ? 'Reconciled orders, expenses, settlements & Excel export.'
          : (a?.latest_run?.output?.summary || a?.effective_model || 'Ready for manual run'),
        'hint'
      );
      box.append(top, sub);
      host.append(box);

      if (idx < steps.length - 1) {
        const arrow = makeEl('span', '→', 'pipeline-arrow');
        arrow.setAttribute('aria-hidden', 'true');
        host.append(arrow);
      }
    });
  }

  function renderIntegrations(payload) {
    latestIntegrations = payload;
    const cardsHost = document.getElementById('integrations-cards');
    const checklistHost = document.getElementById('factori-checklist-view');
    if (!cardsHost || !payload) return;

    const ints = payload.integrations || {};
    cardsHost.replaceChildren();

    const defs = [
      {
        key: 'ollama',
        title: ints.ollama?.name || 'Ollama Local AI',
        status: ints.ollama?.status || 'configured',
        details: [
          `Endpoint: ${ints.ollama?.base_url || 'http://localhost:11434'}`,
          `Default model: ${ints.ollama?.model || 'qwen2.5:3b'}`,
          `Mode: ${ints.ollama?.execution_mode || 'manual'} execution`,
        ],
        actionLabel: 'Test Ollama Connection',
        onAction: () => {
          window.StudioUI?.navigate('settings');
          switchSettingsTab('ai');
          document.getElementById('agent-test-ollama')?.click();
        },
      },
      {
        key: 'ffmpeg',
        title: ints.ffmpeg?.name || 'FFmpeg Engine',
        status: ints.ffmpeg?.status || 'missing',
        details: [
          'Target format: 1080×1920 H.264 + AAC',
          `FFmpeg: ${ints.ffmpeg?.ffmpeg_path || 'Not resolved'}`,
          `FFprobe QA: ${ints.ffmpeg?.ffprobe_path ? 'Available' : 'Not found'}`,
        ],
        actionLabel: 'Open Video Studio',
        onAction: () => window.StudioUI?.navigate('video'),
      },
      {
        key: 'factori_bridge',
        title: ints.factori_bridge?.name || 'Factori Bridge Extension',
        status: ints.factori_bridge?.status || 'standby',
        details: [
          `Extension version: ${ints.factori_bridge?.version || 'Unknown'}`,
          `Staged items: ${ints.factori_bridge?.staged_items ?? 0}`,
          `Scanned pages: ${ints.factori_bridge?.staged_pages ?? 0}`,
        ],
        actionLabel: 'Open Catalog Sync',
        onAction: () => window.StudioUI?.navigate('agents'),
      },
      {
        key: 'instagram_facebook',
        title: ints.instagram_facebook?.name || 'Meta Instagram & Facebook API',
        status: ints.instagram_facebook?.status || 'not_configured',
        details: [
          `Instagram Token: ${ints.instagram_facebook?.credentials?.meta_ig_token?.configured ? 'Encrypted in SQLite' : 'Not configured'}`,
          `Facebook Token: ${ints.instagram_facebook?.credentials?.meta_fb_page_token?.configured ? 'Encrypted in SQLite' : 'Not configured'}`,
          'Fallback: Manual publishing .zip packages active',
        ],
        actionLabel: 'Manage Credentials',
        onAction: () => {
          window.StudioUI?.navigate('approvals');
          const box = document.getElementById('social-creds-box');
          if (box) { box.open = true; box.scrollIntoView({ behavior: 'smooth' }); }
        },
      },
      {
        key: 'youtube',
        title: ints.youtube?.name || 'YouTube Data API v3',
        status: ints.youtube?.status || 'not_configured',
        details: [
          `Channel ID: ${ints.youtube?.credentials?.youtube_channel_id?.masked || 'Not configured'}`,
          `OAuth Token: ${ints.youtube?.credentials?.youtube_oauth_token?.configured ? 'Encrypted in SQLite' : 'Not configured'}`,
          'Fallback: Manual Shorts publishing packages active',
        ],
        actionLabel: 'Configure Token',
        onAction: () => {
          window.StudioUI?.navigate('approvals');
          const box = document.getElementById('social-creds-box');
          if (box) { box.open = true; box.scrollIntoView({ behavior: 'smooth' }); }
        },
      },
      {
        key: 'factori_official_api',
        title: ints.factori_official_api?.name || 'Factori Official API',
        status: ints.factori_official_api?.status || 'pending',
        details: [
          'Official API credentials not yet provided.',
          'Replaceable SupplierAdapter ready in agents/catalog_agent.py.',
          'Zero blocker: Extension multi-page scan + CSV imports work now.',
        ],
      },
    ];

    for (const d of defs) {
      const card = makeEl('article', '', 'integration-card');
      const head = makeEl('div', '', 'meta');
      head.append(makeEl('strong', d.title), makeEl('span', d.status.replace('_', ' '), badgeClass(d.status)));
      card.append(head);
      const ul = document.createElement('ul');
      ul.className = 'checklist-list';
      for (const line of d.details) ul.append(makeEl('li', line));
      card.append(ul);
      if (d.actionLabel) {
        const btn = makeEl('button', d.actionLabel);
        btn.type = 'button';
        btn.onclick = d.onAction;
        card.append(btn);
      }
      cardsHost.append(card);
    }

    if (checklistHost) {
      const items = ints.factori_official_api?.checklist || [];
      checklistHost.replaceChildren();
      if (!items.length) {
        checklistHost.append(makeEl('p', 'No checklist items found in FACTORI_API_CHECKLIST.md.', 'hint'));
      } else {
        const ul = document.createElement('ul');
        ul.className = 'checklist-list';
        for (const item of items) {
          const li = document.createElement('li');
          li.textContent = (item.checked ? '[Done] ' : '[Pending] ') + item.text;
          ul.append(li);
        }
        checklistHost.append(ul);
      }
    }
  }

  function renderKvGrid(hostId, pairs) {
    const host = document.getElementById(hostId);
    if (!host) return;
    host.replaceChildren();
    for (const [k, v] of pairs) {
      const item = makeEl('div', '', 'catalog-stat');
      item.append(makeEl('small', k), makeEl('b', v));
      host.append(item);
    }
  }

  function renderSettingsTabs(payload) {
    if (!payload) return;
    const cfg = payload.safe_config || {};
    const dbInfo = payload.database || {};

    renderKvGrid('settings-general-kv', [
      ['Active Workspace', payload.mode === 'demo' ? 'Demo Catalogue (Isolated)' : 'My Store (Live Workspace)'],
      ['Currency & Timezone', 'INR (₹) · Asia/Kolkata (IST)'],
      ['Weekly Promotion Slots', (cfg.weekly_slots || []).join(', ') || 'None configured'],
      ['Minimum Stock Threshold', String(cfg.minimum_stock ?? 0) + ' units'],
      ['Max Catalogue Freshness Age', String(cfg.maximum_catalogue_age_hours ?? 0) + ' hours'],
    ]);

    renderKvGrid('settings-marketing-kv', [
      ['Default Language Focus', 'Telugu (తెలుగు) & English'],
      ['Supported Campaign Types', 'Single, Multi-Product, Category, Seasonal'],
      ['Strict Truth Policy', 'Zero invented features, prices, reviews, or stock'],
      ['Prompt Injection Guard', 'External descriptions sandboxed as data only'],
    ]);

    renderKvGrid('settings-video-kv', [
      ['Target Resolution & Orientation', '1080×1920 Vertical (9:16)'],
      ['Codec Standard', 'H.264 Video + AAC Audio (MP4)'],
      ['Durations Supported', '15s / 30s / 60s Configurable'],
      ['FFmpeg Engine Status', payload.integrations?.ffmpeg?.status || 'Unknown'],
    ]);

    renderKvGrid('settings-automation-kv', [
      ['Default Execution Mode', cfg.execution_mode || 'manual'],
      ['Configured Retries', String(cfg.max_retries ?? 2)],
      ['Request Timeout', String(cfg.request_timeout_seconds ?? 120) + 's'],
      ['Ad Spend Policy', '₹0 Automatic Ad Spend Prohibited'],
    ]);

    const secBox = document.getElementById('settings-security-box');
    if (secBox) {
      secBox.replaceChildren();
      const stopActive = Boolean(cfg.global_publishing_stopped);
      const banner = makeEl(
        'div',
        stopActive
          ? `Global Publishing Stop ACTIVE: ${cfg.global_stop_reason || 'Paused by operator'}`
          : 'Human Approval Gate Active: No post can be published without explicit approval, passed QA, valid stock, and matching price.',
        'agent-banner' + (stopActive ? ' is-stopped' : '')
      );
      const actRow = makeEl('div', '', 'agent-table-actions');
      const toggleStop = makeEl('button', stopActive ? 'Resume Publishing Gate' : 'Activate Global Publishing Stop', stopActive ? '' : 'primary');
      toggleStop.type = 'button';
      toggleStop.onclick = () => document.getElementById('social-global-stop')?.click();

      const credsBtn = makeEl('button', 'Open Encrypted Social Credentials');
      credsBtn.type = 'button';
      credsBtn.onclick = () => {
        window.StudioUI?.navigate('approvals');
        const box = document.getElementById('social-creds-box');
        if (box) { box.open = true; box.scrollIntoView({ behavior: 'smooth' }); }
      };
      actRow.append(toggleStop, credsBtn);
      secBox.append(banner, actRow);
    }

    const dataBox = document.getElementById('settings-data-box');
    if (dataBox) {
      dataBox.replaceChildren();
      const counts = dbInfo.table_counts || {};
      const statsGrid = makeEl('div', '', 'kv-grid');
      for (const [lbl, val] of [
        ['SQLite DB Size', (Number(dbInfo.size_bytes || 0) / 1024).toFixed(1) + ' KB'],
        ['Reel Queue Jobs', String(counts.jobs ?? 0)],
        ['Agent Execution Runs', String(counts.agent_runs ?? 0)],
        ['Marketing Campaigns', String(counts.marketing_campaigns ?? 0)],
        ['Video Productions', String(counts.video_productions ?? 0)],
        ['Social Posts', String(counts.social_posts ?? 0)],
      ]) {
        const item = makeEl('div', '', 'catalog-stat');
        item.append(makeEl('small', lbl), makeEl('b', val));
        statsGrid.append(item);
      }

      const exportBtn = makeEl('button', 'Download Safe Config & Diagnostics (.json)');
      exportBtn.type = 'button';
      exportBtn.style.marginTop = '12px';
      exportBtn.onclick = () => {
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `prodxmart-diagnostics-${payload.mode}.json`;
        a.click();
        URL.revokeObjectURL(a.href);
      };
      dataBox.append(statsGrid, exportBtn);
    }
  }

  function switchSettingsTab(tabKey) {
    activeSettingsTab = tabKey || 'general';
    document.querySelectorAll('#settings-panel .settings-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.stab === activeSettingsTab);
    });
    document.querySelectorAll('#settings-panel .settings-body').forEach(panel => {
      panel.hidden = panel.dataset.spanel !== activeSettingsTab;
    });
  }

  function applyHistoryExtraFilters() {
    const query = (document.getElementById('history-search')?.value || '').toLowerCase().trim();
    const statusFilter = (document.getElementById('history-status-filter')?.value || '').toLowerCase().trim();
    const table = document.querySelector('#agent-runs table');
    if (!table) return;
    table.querySelectorAll('tbody tr').forEach(tr => {
      const cells = tr.querySelectorAll('td');
      const statusText = (cells[2]?.textContent || '').toLowerCase().trim();
      const fullText = (tr.textContent || '').toLowerCase();
      const matchQuery = !query || fullText.includes(query);
      const matchStatus = !statusFilter || statusText === statusFilter;
      tr.hidden = !(matchQuery && matchStatus);
    });
  }

  async function refreshAgentViews(route) {
    const modeVal = typeof mode === 'function' ? mode() : 'store';
    if (route === 'agents' && window.__latestAgentState) {
      renderPipeline(window.__latestAgentState);
    }
    if (route === 'integrations' || route === 'settings' || !latestIntegrations) {
      const res = await fetch('/api/system/integrations?' + new URLSearchParams({ mode: modeVal }));
      if (res.ok) {
        const data = await res.json();
        renderIntegrations(data);
        renderSettingsTabs(data);
      }
    } else if (latestIntegrations) {
      renderSettingsTabs(latestIntegrations);
    }
    if (route === 'history') {
      applyHistoryExtraFilters();
    }
  }

  // Bind controls once DOM is ready.
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#settings-panel .settings-tab').forEach(btn => {
      btn.onclick = () => switchSettingsTab(btn.dataset.stab);
    });
    switchSettingsTab('ai');

    document.getElementById('int-refresh-btn')?.addEventListener('click', () => {
      refreshAgentViews('integrations').catch(err => notice(err.message, true));
    });

    document.getElementById('pipeline-run-master')?.addEventListener('click', async () => {
      const btn = document.getElementById('pipeline-run-master');
      if (btn) btn.disabled = true;
      try {
        const res = await fetch('/agents/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: 'master_orchestrator', mode: typeof mode === 'function' ? mode() : 'store', payload: {} }),
        });
        const j = await res.json();
        if (!res.ok) throw Error(j.error || 'Workflow launch failed.');
        if (typeof notice === 'function') notice(j.message);
        if (typeof window.refreshAgents === 'function') await window.refreshAgents();
      } catch (err) {
        if (typeof notice === 'function') notice(err.message, true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    document.getElementById('history-search')?.addEventListener('input', applyHistoryExtraFilters);
    document.getElementById('history-status-filter')?.addEventListener('change', applyHistoryExtraFilters);
    document.getElementById('history-export-json')?.addEventListener('click', () => {
      const st = window.__latestAgentState || { runs: [], logs: [] };
      const blob = new Blob([JSON.stringify({ runs: st.runs || [], logs: st.logs || [] }, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `agent-history-${typeof mode === 'function' ? mode() : 'store'}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  });

  window.refreshAgentViews = refreshAgentViews;
  window.renderAgentPipeline = renderPipeline;
  window.applyHistoryExtraFilters = applyHistoryExtraFilters;
})();
