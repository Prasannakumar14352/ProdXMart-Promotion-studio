(function () {
  let agentState = null;
  let agentPollTimer = null;
  let configDirty = false;
  let selectedRunId = null;
  let lastSucceededRunIds = new Set();
  let stagedCsvText = '';
  let stagedCsvName = '';
  let selectedCampaignId = null;

  const STATUS_LABELS = {
    idle: 'Idle',
    disabled: 'Disabled',
    queued: 'Queued',
    running: 'Running',
    succeeded: 'Succeeded',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };

  function formatTime(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST';
    } catch {
      return iso;
    }
  }

  async function agentPost(path, payload) {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, mode: mode() }),
    });
    const body = await res.json();
    if (!res.ok) throw Error(body.error || 'Agent request failed.');
    return body;
  }

  function fillConfigForm(cfg) {
    if (!cfg || configDirty) return;
    $('#agent-ollama-url').value = cfg.ollama_base_url || 'http://localhost:11434';
    $('#agent-default-model').value = cfg.default_model || 'qwen2.5:3b';
    $('#agent-exec-mode').value = cfg.execution_mode || 'manual';
    $('#agent-max-retries').value = String(cfg.max_retries ?? 2);
    $('#agent-timeout').value = String(cfg.request_timeout_seconds ?? 120);
  }

  function renderCards(state) {
    const container = $('#agent-cards');
    container.replaceChildren();
    const activeRunsByAgent = new Map();
    for (const run of state.runs || []) {
      if ((run.status === 'queued' || run.status === 'running') && !activeRunsByAgent.has(run.agent_id)) {
        activeRunsByAgent.set(run.agent_id, run);
      }
    }

    for (const agent of state.agents || []) {
      const card = element('article', '');
      card.className = 'agent-card' + (agent.role === 'orchestrator' ? ' orchestrator' : '') + (agent.enabled ? '' : ' is-disabled');

      const top = element('div', '');
      top.className = 'agent-card-top';
      const roleBadge = element('span', agent.role === 'orchestrator' ? 'Master Orchestrator' : 'Specialist Agent');
      roleBadge.className = 'agent-role' + (agent.role === 'orchestrator' ? ' is-master' : '');
      const statusPill = element('span', STATUS_LABELS[agent.status] || agent.status);
      statusPill.className = 'agent-status status-' + agent.status;
      top.append(roleBadge, statusPill);

      const title = element('h3', agent.name);
      const modelLine = element('p', 'Model: ' + agent.effective_model + ' · Safety: Manual only, read-only/advisory');
      modelLine.className = 'agent-meta';
      const desc = element('p', agent.description);
      desc.className = 'agent-desc';

      card.append(top, title, modelLine, desc);

      const currentRun = activeRunsByAgent.get(agent.agent_id) || agent.latest_run;
      const isActive = Boolean(currentRun && (currentRun.status === 'queued' || currentRun.status === 'running'));
      const canRetry = Boolean(agent.enabled && currentRun && (currentRun.status === 'failed' || currentRun.status === 'cancelled'));

      if (currentRun) {
        const progWrap = element('div', '');
        progWrap.className = 'agent-progress-wrap';
        const bar = element('div', '');
        bar.className = 'agent-progress-bar';
        const fill = element('div', '');
        fill.className = 'agent-progress-fill status-' + currentRun.status;
        fill.style.width = Math.max(0, Math.min(100, currentRun.progress || 0)) + '%';
        bar.append(fill);
        const progText = element(
          'small',
          `${currentRun.progress || 0}% · ${currentRun.progress_message || currentRun.status} (Attempt ${currentRun.attempt}/${1 + (currentRun.max_retries || 0)})`
        );
        progText.className = 'agent-progress-text';
        progWrap.append(bar, progText);
        card.append(progWrap);

        if (currentRun.status === 'succeeded' && currentRun.output) {
          const summary = element('p', currentRun.output.summary || 'Completed.');
          summary.className = 'agent-output-summary';
          card.append(summary);
          const stats = currentRun.output.data?.stats;
          if (agent.agent_id === 'catalog_agent' && stats) {
            const pills = element('div', '');
            pills.className = 'catalog-card-pills';
            for (const [label, val] of [
              ['Total', stats.total_products],
              ['Imported', stats.imported_products],
              ['Updated', stats.updated_products],
              ['Failed', stats.failed_products],
              ['Missing imgs', stats.missing_images],
            ]) {
              const b = element('span', `${label}: ${val ?? 0}`);
              b.className = 'catalog-pill';
              pills.append(b);
            }
            card.append(pills);
          }
          const details = element('details', '');
          details.className = 'agent-output-details';
          details.append(element('summary', 'View latest result'));
          const pre = element('pre', JSON.stringify(currentRun.output.data, null, 2));
          details.append(pre);
          card.append(details);
        } else if ((currentRun.status === 'failed' || currentRun.status === 'cancelled') && currentRun.error_message) {
          const err = element('p', currentRun.error_message);
          err.className = 'agent-error-text';
          card.append(err);
        }
      } else {
        const never = element('p', 'No runs recorded yet in this workspace.');
        never.className = 'agent-idle-note';
        card.append(never);
      }

      const actions = element('div', '');
      actions.className = 'cardactions';

      const toggleBtn = element('button', agent.enabled ? 'Disable' : 'Enable');
      toggleBtn.type = 'button';
      toggleBtn.onclick = async () => {
        toggleBtn.disabled = true;
        try {
          const res = await agentPost('/agents/toggle', { agent_id: agent.agent_id, enabled: !agent.enabled });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        } finally {
          toggleBtn.disabled = false;
        }
      };

      const runBtn = element('button', 'Run');
      runBtn.type = 'button';
      runBtn.className = 'primary';
      runBtn.disabled = !agent.enabled || isActive;
      runBtn.onclick = async () => {
        runBtn.disabled = true;
        try {
          const res = await agentPost('/agents/run', { agent_id: agent.agent_id, payload: {} });
          selectedRunId = res.run?.id || null;
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
          runBtn.disabled = !agent.enabled || isActive;
        }
      };

      const stopBtn = element('button', 'Stop');
      stopBtn.type = 'button';
      stopBtn.disabled = !isActive;
      stopBtn.onclick = async () => {
        if (!currentRun) return;
        stopBtn.disabled = true;
        try {
          const res = await agentPost('/agents/stop', { run_id: currentRun.id });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const retryBtn = element('button', 'Retry');
      retryBtn.type = 'button';
      retryBtn.disabled = !canRetry;
      retryBtn.onclick = async () => {
        if (!currentRun) return;
        retryBtn.disabled = true;
        try {
          const res = await agentPost('/agents/retry', { run_id: currentRun.id });
          selectedRunId = res.run?.id || null;
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const historyBtn = element('button', 'History');
      historyBtn.type = 'button';
      historyBtn.onclick = () => {
        $('#agent-history-filter').value = agent.agent_id;
        renderHistory(agentState);
        if (currentRun) {
          selectedRunId = currentRun.id;
          renderRunDetail(agentState);
        }
        $('.agent-history-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
      };

      actions.append(toggleBtn, runBtn, stopBtn, retryBtn, historyBtn);
      card.append(actions);
      container.append(card);
    }
  }

  function syncHistoryFilterOptions(agents) {
    const select = $('#agent-history-filter');
    const prev = select.value;
    select.replaceChildren(new Option('All agents', ''));
    for (const a of agents || []) {
      select.add(new Option(a.name, a.agent_id));
    }
    if ([...select.options].some(o => o.value === prev)) select.value = prev;
  }

  function renderRunDetail(state) {
    const box = $('#agent-run-detail');
    if (!selectedRunId || !state) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const run = (state.runs || []).find(r => r.id === selectedRunId);
    if (!run) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const logs = (state.logs || []).filter(l => l.run_id === run.id).reverse();
    box.hidden = false;
    box.replaceChildren();

    const header = element('div', '');
    header.className = 'agent-detail-head';
    header.append(element('strong', `Run ${run.id} · ${run.agent_id} (${run.status})`));
    const closeBtn = element('button', 'Close');
    closeBtn.type = 'button';
    closeBtn.onclick = () => {
      selectedRunId = null;
      renderRunDetail(agentState);
    };
    header.append(closeBtn);
    box.append(header);

    const meta = element(
      'p',
      `Model: ${run.model} · Trigger: ${run.trigger} · Attempt: ${run.attempt}/${1 + (run.max_retries || 0)} · Created: ${formatTime(run.created_at)} · Finished: ${formatTime(run.finished_at)}`
    );
    meta.className = 'hint';
    box.append(meta);

    if (run.error_message) {
      const err = element('p', 'Error: ' + run.error_message);
      err.className = 'agent-error-text';
      box.append(err);
    }
    if (run.output) {
      box.append(element('h3', 'Output'));
      box.append(element('pre', JSON.stringify(run.output, null, 2)));
    }
    box.append(element('h3', 'Execution & Error Logs'));
    if (!logs.length) {
      box.append(element('p', 'No log entries recorded for this run.'));
    } else {
      const list = element('ul', '');
      list.className = 'agent-log-list';
      for (const entry of logs) {
        const li = element('li', `[${entry.level}] ${formatTime(entry.created_at)} — ${entry.message}`);
        li.className = 'log-' + entry.level.toLowerCase();
        if (entry.details) {
          const det = element('pre', entry.details);
          li.append(det);
        }
        list.append(li);
      }
      box.append(list);
    }
  }

  function renderHistory(state) {
    const filterId = $('#agent-history-filter').value;
    const runs = (state.runs || []).filter(r => !filterId || r.agent_id === filterId);
    const target = $('#agent-runs');
    if (!runs.length) {
      const empty = element('div', '');
      empty.className = 'empty';
      empty.append(
        element('h3', 'No execution history yet'),
        element('p', 'Click Run on the Master Orchestrator or any enabled agent card to execute a local task.')
      );
      target.replaceChildren(empty);
      renderRunDetail(state);
      return;
    }

    const t = document.createElement('table');
    const head = t.createTHead().insertRow();
    ['Run', 'Agent', 'Status', 'Progress', 'Trigger', 'Started', 'Summary / Error', 'Actions'].forEach(col =>
      head.append(element('th', col))
    );
    const body = t.createTBody();
    for (const run of runs) {
      const tr = body.insertRow();
      tr.append(
        element('td', run.id),
        element('td', run.agent_id),
        element('td', STATUS_LABELS[run.status] || run.status),
        element('td', `${run.progress || 0}% (${run.attempt}/${1 + (run.max_retries || 0)})`),
        element('td', run.trigger),
        element('td', formatTime(run.started_at || run.created_at)),
        element(
          'td',
          run.status === 'succeeded'
            ? run.output?.summary || 'Completed'
            : run.error_message || run.progress_message || '—'
        )
      );
      const actCell = tr.insertCell();
      actCell.className = 'agent-table-actions';
      const inspectBtn = element('button', 'Inspect');
      inspectBtn.type = 'button';
      inspectBtn.onclick = () => {
        selectedRunId = run.id;
        renderRunDetail(agentState);
      };
      actCell.append(inspectBtn);

      if (run.status === 'queued' || run.status === 'running') {
        const stopBtn = element('button', 'Stop');
        stopBtn.type = 'button';
        stopBtn.onclick = async () => {
          stopBtn.disabled = true;
          try {
            const res = await agentPost('/agents/stop', { run_id: run.id });
            notice(res.message);
            await refreshAgents();
          } catch (err) {
            notice(err.message, true);
            stopBtn.disabled = false;
          }
        };
        actCell.append(stopBtn);
      } else if (run.status === 'failed' || run.status === 'cancelled') {
        const retryBtn = element('button', 'Retry');
        retryBtn.type = 'button';
        retryBtn.onclick = async () => {
          retryBtn.disabled = true;
          try {
            const res = await agentPost('/agents/retry', { run_id: run.id });
            selectedRunId = res.run?.id || null;
            notice(res.message);
            await refreshAgents();
          } catch (err) {
            notice(err.message, true);
            retryBtn.disabled = false;
          }
        };
        actCell.append(retryBtn);
      }
    }
    target.replaceChildren(t);
    renderRunDetail(state);
  }

  function renderCatalogPanel(state) {
    const stagedEl = $('#catalog-staged-status');
    const statsEl = $('#catalog-stats');
    const diagEl = $('#catalog-diagnostics');
    if (!stagedEl || !statsEl || !diagEl) return;

    const staged = state.staged_catalog;
    const freshness = state.catalog_freshness || {};
    const parts = [];
    if (staged) {
      parts.push(`Staged ${staged.source} batch: ${staged.item_count} product(s) across ${staged.pages_count} page(s) (${formatTime(staged.staged_at)}).`);
    }
    if (stagedCsvName) {
      parts.push(`Selected CSV: ${stagedCsvName}`);
    }
    if (!parts.length) {
      parts.push('No extension batch staged. Choose a source and click Run Catalog Sync manually.');
    }
    stagedEl.textContent = parts.join(' · ');

    const catalogCard = (state.agents || []).find(a => a.agent_id === 'catalog_agent');
    const latestRun = catalogCard?.latest_run;
    const stats = latestRun?.output?.data?.stats;
    const diagnostics = latestRun?.output?.data?.diagnostics;

    statsEl.replaceChildren();
    const statItems = [
      ['Total products', stats ? stats.total_products : (freshness.total_tracked ?? 0)],
      ['Imported (new)', stats ? stats.imported_products : 0],
      ['Updated', stats ? stats.updated_products : 0],
      ['Failed', stats ? stats.failed_products : 0],
      ['Missing images', stats ? stats.missing_images : 0],
      ['Stock freshness', `${freshness.fresh ?? 0} fresh · ${freshness.stale ?? 0} stale · ${freshness.unknown ?? 0} unknown`],
      ['Last synced', formatTime(stats?.last_synced_at || freshness.last_synced_at)],
    ];
    for (const [k, v] of statItems) {
      const box = element('div', '');
      box.className = 'catalog-stat';
      box.append(element('small', k), element('b', String(v)));
      statsEl.append(box);
    }

    diagEl.replaceChildren();
    if (!diagnostics) return;
    const buckets = [
      ['Missing SKU', diagnostics.missing_sku || [], row => `${row.name} (${row.product_url || 'no URL'}) — ${row.reason}`],
      ['Unknown stock', diagnostics.unknown_stock || [], row => `${row.sku}${row.variant ? ' [' + row.variant + ']' : ''} · ${row.name} — ${row.reason}`],
      ['Missing prices', diagnostics.missing_prices || [], row => `${row.sku}${row.variant ? ' [' + row.variant + ']' : ''} · ${row.name} — ${row.reason}`],
      ['Image failures', diagnostics.image_failures || [], row => `${row.sku} · ${row.url} (${row.attempts} attempt(s)) — ${row.error}`],
    ];
    for (const [title, rows, formatFn] of buckets) {
      const det = element('details', '');
      det.className = 'catalog-diag-bucket' + (rows.length ? ' has-issues' : '');
      det.append(element('summary', `${title} (${rows.length})`));
      if (!rows.length) {
        det.append(element('p', 'None reported in the latest run.'));
      } else {
        const ul = element('ul', '');
        for (const row of rows) ul.append(element('li', formatFn(row)));
        det.append(ul);
      }
      diagEl.append(det);
    }
  }

  function schedulePoll(activeCount) {
    if (agentPollTimer) {
      clearTimeout(agentPollTimer);
      agentPollTimer = null;
    }
    if (activeCount > 0) {
      agentPollTimer = setTimeout(() => {
        refreshAgents().catch(() => {});
      }, 1500);
    }
  }

  async function refreshAgents() {
    const res = await fetch('/agents/state?' + new URLSearchParams({ mode: mode() }));
    const data = await res.json();
    if (!res.ok) throw Error(data.error || 'Could not load AI Agent Center state.');

    // If a catalog sync or orchestrator run newly succeeded in store mode, refresh product table too.
    const newlyDone = (data.runs || []).some(
      r => r.status === 'succeeded' && ['catalog_agent', 'marketing_agent', 'video_agent', 'qa_agent', 'social_agent', 'master_orchestrator'].includes(r.agent_id) && !lastSucceededRunIds.has(r.id)
    );
    const hadPrevious = lastSucceededRunIds.size > 0 || agentState !== null;
    for (const r of data.runs || []) {
      if (r.status === 'succeeded') lastSucceededRunIds.add(r.id);
    }

    agentState = data;
    fillConfigForm(data.config);
    syncHistoryFilterOptions(data.agents);
    syncMarketingSkuOptions();
    syncVideoSkuOptions();
    syncSocialProductionOptions(data);
    $('#agent-queue-summary').textContent =
      data.active_count > 0
        ? `${data.active_count} active task(s) in queue · polling for updates`
        : `Queue idle · manual execution (${data.config.execution_mode})`;
    renderCatalogPanel(data);
    renderMarketingPreview(data);
    renderVideoQaPreview(data);
    renderSocialApprovalCenter(data);
    renderCards(data);
    renderHistory(data);
    schedulePoll(data.active_count || 0);
    if (hadPrevious && newlyDone && typeof refresh === 'function') {
      refresh().catch(() => {});
    }
  }

  function syncMarketingSkuOptions() {
    const sel = $('#mkt-skus');
    if (!sel || typeof state === 'undefined' || !state?.products) return;
    const prev = new Set([...sel.selectedOptions].map(o => o.value));
    sel.replaceChildren();
    for (const p of state.products) {
      const opt = new Option(`${p.name} · ${p.sku}${p.issues.length ? ' (Hold)' : ''}`, p.sku);
      opt.selected = prev.has(p.sku);
      sel.add(opt);
    }
  }

  function renderMarketingPreview(data) {
    const wrap = $('#marketing-preview');
    if (!wrap) return;
    wrap.replaceChildren();
    const campaigns = data.marketing_campaigns || [];
    if (!campaigns.length) {
      const empty = element('p', 'No marketing campaigns generated yet in this workspace. Choose campaign options above and click Generate Marketing Campaign.');
      empty.className = 'hint';
      wrap.append(empty);
      return;
    }

    if (!selectedCampaignId || !campaigns.some(c => c.id === selectedCampaignId)) {
      selectedCampaignId = campaigns[0].id;
    }
    const current = campaigns.find(c => c.id === selectedCampaignId) || campaigns[0];
    const doc = current.campaign || {};

    const bar = element('div', '');
    bar.className = 'mkt-toolbar';
    const picker = document.createElement('select');
    for (const c of campaigns) {
      picker.add(new Option(`${c.campaign_type} (${c.language}) · ${c.selected_skus.join(', ')} · ${formatTime(c.updated_at)}`, c.id));
    }
    picker.value = current.id;
    picker.onchange = () => {
      selectedCampaignId = picker.value;
      renderMarketingPreview(agentState);
    };

    const actions = element('div', '');
    actions.className = 'agent-config-actions';
    const regenBtn = element('button', 'Regenerate Campaign');
    regenBtn.type = 'button';
    regenBtn.onclick = async () => {
      regenBtn.disabled = true;
      try {
        const res = await agentPost('/agents/marketing/regenerate', {
          campaign_id: current.id,
          campaign_type: $('#mkt-campaign-type').value,
          language: $('#mkt-language').value,
          category: $('#mkt-category').value.trim(),
          season_theme: $('#mkt-season').value.trim(),
        });
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      } finally {
        regenBtn.disabled = false;
      }
    };

    const draftsBtn = element('button', 'Create Reel Drafts');
    draftsBtn.type = 'button';
    draftsBtn.className = 'primary';
    draftsBtn.onclick = async () => {
      draftsBtn.disabled = true;
      try {
        const res = await agentPost('/agents/marketing/create-drafts', { campaign_id: current.id });
        notice(res.message);
        await refreshAgents();
        if (typeof refresh === 'function') await refresh();
      } catch (err) {
        notice(err.message, true);
      } finally {
        draftsBtn.disabled = false;
      }
    };
    actions.append(regenBtn, draftsBtn);
    bar.append(picker, actions);
    wrap.append(bar);

    const badges = element('div', '');
    badges.className = 'catalog-card-pills';
    for (const txt of [
      `Type: ${current.campaign_type}`,
      `Language: ${current.language}`,
      `Mode: ${doc.generation_mode || 'ollama_validated'}`,
      `Selection: ${doc.selection_source || 'catalogue'}`,
      `Video Agent Handoff: ${current.status}`,
      `Linked Reel Drafts: ${(current.linked_job_ids || []).length}`,
    ]) {
      const pill = element('span', txt);
      pill.className = 'catalog-pill';
      badges.append(pill);
    }
    wrap.append(badges);

    if (doc.campaign_summary) {
      const sum = element('p', doc.campaign_summary);
      sum.className = 'agent-output-summary';
      wrap.append(sum);
    }
    if (doc.validation_warnings?.length) {
      const warn = element('p', 'Validation notes: ' + doc.validation_warnings.join(' | '));
      warn.className = 'agent-error-text';
      wrap.append(warn);
    }

    for (const item of doc.products || []) {
      const card = element('div', '');
      card.className = 'mkt-product-card';

      const head = element('div', '');
      head.className = 'agent-card-top';
      head.append(element('strong', `${item.product_name} (${item.sku}) · ₹${item.price_inr}`));
      const regenOne = element('button', 'Regenerate Product');
      regenOne.type = 'button';
      regenOne.onclick = async () => {
        regenOne.disabled = true;
        try {
          const res = await agentPost('/agents/marketing/regenerate', {
            campaign_id: current.id,
            sku: item.sku,
            language: $('#mkt-language').value,
            season_theme: $('#mkt-season').value.trim(),
          });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        } finally {
          regenOne.disabled = false;
        }
      };
      head.append(regenOne);
      card.append(head);

      const grid = element('div', '');
      grid.className = 'mkt-fields';
      for (const [label, val] of [
        ['Target Audience', item.target_audience],
        ['Marketing Objective', item.marketing_objective],
        ['Product Benefits', (item.product_benefits || []).join(' · ')],
        ['Opening Hook', item.opening_hook],
        ['Voiceover Script', item.voiceover_script],
        ['Promotional Video Script', item.video_script],
        ['On-screen Captions', (item.on_screen_captions || []).join(' | ')],
        ['Instagram Caption', item.instagram_caption],
        ['Facebook Caption', item.facebook_caption],
        ['YouTube Shorts', `${item.youtube_shorts?.title || ''} — ${item.youtube_shorts?.description || ''}`],
        ['Hashtags', (item.hashtags || []).join(' ')],
        ['Call to Action', item.call_to_action],
      ]) {
        const row = element('div', '');
        row.className = 'mkt-field';
        row.append(element('small', label), element('p', val || '—'));
        grid.append(row);
      }
      card.append(grid);

      if (item.storyboard?.length) {
        const sbTable = document.createElement('table');
        sbTable.className = 'mkt-storyboard';
        const sh = sbTable.createTHead().insertRow();
        ['Scene', 'Seconds', 'Visual', 'On-screen Text', 'Voiceover'].forEach(h => sh.append(element('th', h)));
        const sbBody = sbTable.createTBody();
        for (const sc of item.storyboard) {
          const tr = sbBody.insertRow();
          tr.append(
            element('td', String(sc.scene_number)),
            element('td', String(sc.duration_seconds) + 's'),
            element('td', sc.visual),
            element('td', sc.on_screen_text),
            element('td', sc.voiceover)
          );
        }
        card.append(sbTable);
      }

      wrap.append(card);
    }

    if (current.storyboard_handoff) {
      const handoffDet = element('details', '');
      handoffDet.className = 'agent-output-details';
      handoffDet.append(element('summary', 'View Video Agent Storyboard Handoff Payload'));
      handoffDet.append(element('pre', JSON.stringify(current.storyboard_handoff, null, 2)));
      wrap.append(handoffDet);
    }
  }

  function updateMarketingFormVisibility() {
    const type = $('#mkt-campaign-type')?.value;
    if ($('#mkt-category-wrap')) $('#mkt-category-wrap').hidden = type !== 'category';
    if ($('#mkt-season-wrap')) $('#mkt-season-wrap').hidden = type !== 'seasonal';
  }
  if ($('#mkt-campaign-type')) {
    $('#mkt-campaign-type').onchange = updateMarketingFormVisibility;
    updateMarketingFormVisibility();
  }
  if ($('#mkt-generate')) {
    $('#mkt-generate').onclick = async () => {
      const btn = $('#mkt-generate');
      btn.disabled = true;
      try {
        const skus = [...($('#mkt-skus')?.selectedOptions || [])].map(o => o.value).filter(Boolean);
        const campaign_type = $('#mkt-campaign-type').value;
        const payload = {
          campaign_type,
          language: $('#mkt-language').value,
          skus: skus.length ? skus : undefined,
          category: $('#mkt-category').value.trim(),
          season_theme: $('#mkt-season').value.trim(),
          max_products: campaign_type === 'single_product' ? 1 : Math.max(3, skus.length || 3),
        };
        const res = await agentPost('/agents/run', { agent_id: 'marketing_agent', payload });
        selectedRunId = res.run?.id || null;
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      } finally {
        btn.disabled = false;
      }
    };
  }

  function syncVideoSkuOptions() {
    const sel = $('#vid-skus');
    if (!sel || typeof state === 'undefined' || !state?.products) return;
    const prev = new Set([...sel.selectedOptions].map(o => o.value));
    sel.replaceChildren();
    for (const p of state.products) {
      const opt = new Option(`${p.name} · ${p.sku}${p.issues.length ? ' (Hold)' : ''}`, p.sku);
      opt.selected = prev.has(p.sku);
      sel.add(opt);
    }
  }

  function renderVideoQaPreview(data) {
    const wrap = $('#video-qa-preview');
    if (!wrap) return;
    wrap.replaceChildren();

    const productions = data.video_productions || [];
    const reportsByProd = new Map();
    for (const rep of data.qa_reports || []) {
      if (!reportsByProd.has(rep.production_id)) reportsByProd.set(rep.production_id, rep);
    }

    const qaCard = (data.agents || []).find(a => a.agent_id === 'qa_agent');
    const latestAudits = qaCard?.latest_run?.output?.data?.job_audits;
    if (latestAudits?.length) {
      const auditBox = element('details', '');
      auditBox.className = 'catalog-diag-bucket' + (latestAudits.some(a => !a.automated_passed) ? ' has-issues' : '');
      auditBox.open = true;
      auditBox.append(element('summary', `Existing Reel Queue FFprobe Audit (${latestAudits.filter(a => !a.automated_passed).length} failed / ${latestAudits.length} checked)`));
      const ul = element('ul', '');
      for (const item of latestAudits) {
        ul.append(element('li', `${item.job_id} (${item.sku} · ${item.status}): ${item.automated_passed ? 'PASSED' : 'BLOCKED — ' + item.failures.join('; ')}`));
      }
      auditBox.append(ul);
      wrap.append(auditBox);
    }

    if (!productions.length) {
      const empty = element('p', 'No Video Production Agent videos rendered yet in this workspace. Choose duration and click Generate Video.');
      empty.className = 'hint';
      wrap.append(empty);
      return;
    }

    for (const prod of productions) {
      const meta = prod.metadata || {};
      const qaEntry = reportsByProd.get(prod.id);
      const rep = qaEntry?.report;

      const card = element('div', '');
      card.className = 'vid-qa-card';

      const videoCol = element('div', '');
      videoCol.className = 'vid-player-col';
      const video = document.createElement('video');
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.src = (meta.video_url || `/video/${prod.id}.mp4`) + '?t=' + encodeURIComponent(prod.updated_at || '');
      videoCol.append(video);

      const infoCol = element('div', '');
      infoCol.className = 'vid-info-col';
      const header = element('div', '');
      header.className = 'agent-card-top';
      header.append(element('strong', `Video ${prod.id} · ${prod.duration_seconds}s · ${prod.language.toUpperCase()} (${(prod.skus || []).join(', ')})`));
      const statusBadge = element('span', `QA: ${prod.qa_status} · Human: ${prod.human_review_status}`);
      statusBadge.className = 'agent-status ' + (prod.qa_status === 'failed' ? 'status-failed' : prod.human_review_status === 'approved' ? 'status-succeeded' : 'status-cancelled');
      header.append(statusBadge);
      infoCol.append(header);

      const pills = element('div', '');
      pills.className = 'catalog-card-pills';
      for (const label of [
        `Resolution: ${meta.width || 1080}×${meta.height || 1920}`,
        `Layouts: ${(meta.category_layouts || []).join(', ') || 'default'}`,
        `Voiceover: ${meta.voiceover_status || 'unknown'}`,
        `Ducking: ${meta.ducking_applied ? 'Yes' : 'Music only'}`,
        `Publish Allowed: ${qaEntry?.publish_allowed ? 'Yes (manual only)' : 'Blocked until QA + Human Review'}`,
      ]) {
        const p = element('span', label);
        p.className = 'catalog-pill';
        pills.append(p);
      }
      infoCol.append(pills);

      if (rep) {
        const checks = element('div', '');
        checks.className = 'qa-checks-grid';
        for (const [ck, passed] of Object.entries(rep.checks || {})) {
          const item = element('span', `${passed ? '✓' : '✗'} ${ck.replace(/_/g, ' ')}`);
          item.className = 'qa-check-pill ' + (passed ? 'pass' : 'fail');
          checks.append(item);
        }
        infoCol.append(checks);

        if (rep.probe?.audio_volume) {
          const vol = rep.probe.audio_volume;
          const volLine = element('p', `FFprobe: ${rep.probe.duration_seconds}s · Audio mean ${vol.mean_volume_db ?? '—'} dB / max ${vol.max_volume_db ?? '—'} dB`);
          volLine.className = 'hint';
          infoCol.append(volLine);
        }
        if (rep.failures?.length) {
          const err = element('p', 'QA Failures: ' + rep.failures.join(' | '));
          err.className = 'agent-error-text';
          infoCol.append(err);
        }
        if (rep.warnings?.length) {
          const warn = element('p', 'Warnings: ' + rep.warnings.join(' | '));
          warn.className = 'hint';
          infoCol.append(warn);
        }
      }

      // Human review checklist & regeneration controls.
      const reviewBox = element('div', '');
      reviewBox.className = 'qa-human-review';
      reviewBox.append(element('strong', 'Human Review Gate (Required before publishing)'));
      const checksRow = element('div', '');
      checksRow.className = 'qa-review-checks';
      const visChk = document.createElement('input'); visChk.type = 'checkbox';
      const proChk = document.createElement('input'); proChk.type = 'checkbox';
      const accChk = document.createElement('input'); accChk.type = 'checkbox';
      const existingChecklist = rep?.human_review?.checklist || {};
      visChk.checked = existingChecklist.visual_quality === true;
      proChk.checked = existingChecklist.pronunciation === true;
      accChk.checked = existingChecklist.product_accuracy === true;
      for (const [box, lbl] of [[visChk, 'Visual quality'], [proChk, 'Pronunciation'], [accChk, 'Product accuracy']]) {
        const l = document.createElement('label');
        l.className = 'qa-chk-label';
        l.append(box, document.createTextNode(' ' + lbl));
        checksRow.append(l);
      }
      const noteInput = document.createElement('input');
      noteInput.type = 'text';
      noteInput.placeholder = 'Optional reviewer note…';
      noteInput.value = prod.reviewer_note || '';

      const btnRow = element('div', '');
      btnRow.className = 'cardactions';
      const runQaBtn = element('button', 'Run QA');
      runQaBtn.type = 'button';
      runQaBtn.onclick = async () => {
        await agentPost('/agents/run', { agent_id: 'qa_agent', payload: { production_id: prod.id } });
        await refreshAgents();
      };
      const regenBtn = element('button', 'Regenerate Video');
      regenBtn.type = 'button';
      regenBtn.onclick = async () => {
        const res = await agentPost('/agents/video/regenerate', {
          production_id: prod.id,
          duration_seconds: Number($('#vid-duration').value),
          language: $('#vid-language').value,
          voiceover_mode: $('#vid-vo-mode').value,
          recorded_audio_path: $('#vid-recorded-path').value.trim(),
          duck_music: Boolean($('#vid-duck').checked),
        });
        notice(res.message);
        await refreshAgents();
      };
      const approveBtn = element('button', 'Approve Review');
      approveBtn.type = 'button';
      approveBtn.className = 'primary';
      approveBtn.disabled = prod.qa_status !== 'passed';
      approveBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/qa/review', {
            production_id: prod.id,
            decision: 'approved',
            checklist: { visual_quality: visChk.checked, pronunciation: proChk.checked, product_accuracy: accChk.checked },
            note: noteInput.value.trim(),
          });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };
      const rejectBtn = element('button', 'Reject');
      rejectBtn.type = 'button';
      rejectBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/qa/review', {
            production_id: prod.id,
            decision: 'rejected',
            checklist: { visual_quality: visChk.checked, pronunciation: proChk.checked, product_accuracy: accChk.checked },
            note: noteInput.value.trim(),
          });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };
      btnRow.append(runQaBtn, regenBtn, approveBtn, rejectBtn);
      reviewBox.append(checksRow, noteInput, btnRow);
      infoCol.append(reviewBox);

      card.append(videoCol, infoCol);
      wrap.append(card);
    }
  }

  if ($('#vid-vo-mode')) {
    $('#vid-vo-mode').onchange = () => {
      if ($('#vid-recorded-wrap')) $('#vid-recorded-wrap').hidden = $('#vid-vo-mode').value !== 'recorded';
    };
  }
  if ($('#vid-generate')) {
    $('#vid-generate').onclick = async () => {
      const btn = $('#vid-generate');
      btn.disabled = true;
      try {
        const skus = [...($('#vid-skus')?.selectedOptions || [])].map(o => o.value).filter(Boolean);
        const res = await agentPost('/agents/run', {
          agent_id: 'video_agent',
          payload: {
            duration_seconds: Number($('#vid-duration').value),
            language: $('#vid-language').value,
            voiceover_mode: $('#vid-vo-mode').value,
            recorded_audio_path: $('#vid-recorded-path').value.trim(),
            duck_music: Boolean($('#vid-duck').checked),
            skus: skus.length ? skus : undefined,
          },
        });
        selectedRunId = res.run?.id || null;
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      } finally {
        btn.disabled = false;
      }
    };
  }
  if ($('#vid-run-qa')) {
    $('#vid-run-qa').onclick = async () => {
      const res = await agentPost('/agents/run', { agent_id: 'qa_agent', payload: {} });
      notice(res.message);
      await refreshAgents();
    };
  }
  if ($('#vid-audit-reels')) {
    $('#vid-audit-reels').onclick = async () => {
      const res = await agentPost('/agents/run', { agent_id: 'qa_agent', payload: { audit_existing_jobs: true } });
      notice(res.message);
      await refreshAgents();
    };
  }

  function syncSocialProductionOptions(data) {
    const sel = $('#social-production-select');
    if (!sel) return;
    const prev = sel.value;
    sel.replaceChildren(new Option('Latest QA-passed video', ''));
    for (const p of data.video_productions || []) {
      sel.add(new Option(`${p.id} · ${(p.skus || []).join(',')} · ${p.duration_seconds}s (${p.qa_status}/${p.human_review_status})`, p.id));
    }
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  }

  function renderSocialApprovalCenter(data) {
    const settings = data.social_settings || {};
    const stopped = Boolean(settings.global_publishing_stopped);
    const stopBtn = $('#social-global-stop');
    const stopBanner = $('#social-stop-banner');
    if (stopBtn) {
      stopBtn.textContent = stopped ? 'Resume Publishing' : 'Stop All Publishing';
      stopBtn.className = stopped ? 'primary' : 'btn-danger';
    }
    if (stopBanner) {
      stopBanner.hidden = !stopped;
      stopBanner.className = 'agent-banner is-stopped';
      stopBanner.textContent = stopped
        ? 'GLOBAL PUBLISHING STOP ACTIVE: All package downloads, manual publish exports, and scheduled publishing are halted until resumed.'
        : '';
    }

    const creds = data.social_credentials || {};
    const credsOut = $('#social-creds-status');
    if (credsOut) {
      const configured = Object.values(creds).filter(c => c.configured);
      credsOut.textContent = configured.length
        ? 'Configured (masked): ' + configured.map(c => `${c.cred_key}=${c.masked_preview}`).join(' · ')
        : 'No external API credentials stored yet. Manual publishing packages require no API keys.';
    }

    const posts = data.social_posts || [];
    const calEl = $('#social-calendar');
    if (calEl) {
      calEl.replaceChildren();
      const calItems = posts.filter(p => p.scheduled_at || p.published_at || p.status === 'Approved');
      if (!calItems.length) {
        const p = element('p', 'No approved, scheduled, or published posts on the calendar yet.');
        p.className = 'hint';
        calEl.append(p);
      } else {
        const groups = new Map();
        for (const item of calItems) {
          const ts = item.scheduled_at || item.published_at || item.approved_at;
          const dayKey = ts ? new Date(ts).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) : 'Unscheduled Approved';
          if (!groups.has(dayKey)) groups.set(dayKey, []);
          groups.get(dayKey).push(item);
        }
        for (const [day, items] of groups.entries()) {
          const dayCard = element('div', '');
          dayCard.className = 'cal-day';
          dayCard.append(element('strong', day));
          const list = element('ul', '');
          for (const it of items) {
            const li = element('li', `${it.platform} · ${it.sku} · ${it.status}${it.scheduled_at ? ' @ ' + formatTime(it.scheduled_at) : ''}`);
            list.append(li);
          }
          dayCard.append(list);
          calEl.append(dayCard);
        }
      }
    }

    const postsEl = $('#social-posts');
    if (!postsEl) return;
    postsEl.replaceChildren();
    const filterStatus = $('#social-status-filter')?.value || '';
    const visible = posts.filter(p => !filterStatus || p.status === filterStatus);
    if (!visible.length) {
      const empty = element('p', 'No social posts match the current filter. Click Prepare Social Posts after generating and reviewing a video.');
      empty.className = 'hint';
      postsEl.append(empty);
      return;
    }

    for (const post of visible) {
      const card = element('div', '');
      card.className = 'social-post-card';

      const head = element('div', '');
      head.className = 'agent-card-top';
      head.append(element('strong', `${post.platform.replace('_', ' ').toUpperCase()} · ${post.sku} (${post.id})`));
      const st = element('span', post.status);
      st.className = 'agent-status ' + (
        post.status === 'Published' || post.status === 'Approved' ? 'status-succeeded' :
        post.status === 'Failed' ? 'status-failed' :
        post.status === 'Scheduled' ? 'status-running' : 'status-cancelled'
      );
      head.append(st);
      card.append(head);

      if (post.invalidation_reason) {
        const inv = element('p', post.invalidation_reason);
        inv.className = 'agent-error-text';
        card.append(inv);
      }
      if (post.error_message && post.error_message !== post.invalidation_reason) {
        const err = element('p', post.error_message);
        err.className = 'agent-error-text';
        card.append(err);
      }
      if (post.published_url) {
        const pubP = element('p', 'Published URL: ');
        const a = element('a', post.published_url);
        a.href = post.published_url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        pubP.append(a);
        card.append(pubP);
      }

      const fields = element('div', '');
      fields.className = 'social-edit-grid';
      const titleIn = document.createElement('input'); titleIn.type = 'text'; titleIn.value = post.title || '';
      const capIn = document.createElement('textarea'); capIn.rows = 3; capIn.value = post.caption || '';
      const descIn = document.createElement('textarea'); descIn.rows = 2; descIn.value = post.description || '';
      const tagsIn = document.createElement('input'); tagsIn.type = 'text'; tagsIn.value = (post.hashtags || []).join(' ');
      const linkIn = document.createElement('input'); linkIn.type = 'url'; linkIn.value = post.product_link || '';
      const ro = post.status === 'Published';
      [titleIn, capIn, descIn, tagsIn, linkIn].forEach(el => { el.disabled = ro; });

      for (const [lbl, el] of [['Title', titleIn], ['Caption', capIn], ['Description', descIn], ['Hashtags', tagsIn], ['Product Link', linkIn]]) {
        const wrapLbl = document.createElement('label');
        wrapLbl.append(element('small', lbl), el);
        fields.append(wrapLbl);
      }
      card.append(fields);

      const actions = element('div', '');
      actions.className = 'cardactions';

      const saveEditBtn = element('button', 'Save Edit');
      saveEditBtn.type = 'button';
      saveEditBtn.disabled = ro;
      saveEditBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/social/edit', {
            post_id: post.id,
            title: titleIn.value,
            caption: capIn.value,
            description: descIn.value,
            hashtags: tagsIn.value,
            product_link: linkIn.value,
          });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const regenBtn = element('button', 'Regenerate');
      regenBtn.type = 'button';
      regenBtn.disabled = ro;
      regenBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/social/regenerate', { post_id: post.id });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const approveBtn = element('button', 'Approve');
      approveBtn.type = 'button';
      approveBtn.className = 'primary';
      approveBtn.disabled = !['Draft', 'Awaiting Approval'].includes(post.status);
      approveBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/social/approve', { post_id: post.id });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const rejectBtn = element('button', 'Reject');
      rejectBtn.type = 'button';
      rejectBtn.disabled = ro;
      rejectBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/social/reject', { post_id: post.id, reason: 'Rejected in Approval Center' });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      const schedInput = document.createElement('input');
      schedInput.type = 'datetime-local';
      schedInput.value = post.scheduled_at ? post.scheduled_at.slice(0, 16) : ($('#start')?.value || '');
      schedInput.disabled = !['Approved', 'Scheduled'].includes(post.status);

      const schedBtn = element('button', 'Schedule');
      schedBtn.type = 'button';
      schedBtn.disabled = !['Approved', 'Scheduled'].includes(post.status);
      schedBtn.onclick = async () => {
        try {
          const res = await agentPost('/agents/social/schedule', { post_id: post.id, scheduled_at: schedInput.value });
          notice(res.message);
          await refreshAgents();
        } catch (err) {
          notice(err.message, true);
        }
      };

      actions.append(saveEditBtn, regenBtn, approveBtn, rejectBtn, schedInput, schedBtn);

      if (['Approved', 'Scheduled'].includes(post.status) && !stopped) {
        const dl = element('a', 'Download Manual Package (.zip)');
        dl.href = `/agents/social/package/${post.id}.zip?mode=${mode()}`;
        dl.download = `${post.id}_${post.platform}.zip`;
        actions.append(dl);

        const urlInput = document.createElement('input');
        urlInput.type = 'url';
        urlInput.placeholder = 'https://... actual post URL';
        const confirmBtn = element('button', 'Confirm Published');
        confirmBtn.type = 'button';
        confirmBtn.onclick = async () => {
          try {
            const res = await agentPost('/agents/social/confirm-published', { post_id: post.id, published_url: urlInput.value.trim() });
            notice(res.message);
            await refreshAgents();
          } catch (err) {
            notice(err.message, true);
          }
        };
        actions.append(urlInput, confirmBtn);
      }

      card.append(actions);
      postsEl.append(card);
    }
  }

  if ($('#social-status-filter')) {
    $('#social-status-filter').onchange = () => {
      if (agentState) renderSocialApprovalCenter(agentState);
    };
  }
  if ($('#social-global-stop')) {
    $('#social-global-stop').onclick = async () => {
      const currentlyStopped = Boolean(agentState?.social_settings?.global_publishing_stopped);
      const res = await agentPost('/agents/social/global-stop', {
        stopped: !currentlyStopped,
        reason: !currentlyStopped ? 'Paused by user from Approval Center' : '',
      });
      notice(res.message);
      await refreshAgents();
    };
  }
  if ($('#social-prepare')) {
    $('#social-prepare').onclick = async () => {
      const btn = $('#social-prepare');
      btn.disabled = true;
      try {
        const platforms = [...document.querySelectorAll('.social-plat:checked')].map(el => el.value);
        const res = await agentPost('/agents/run', {
          agent_id: 'social_agent',
          payload: {
            platforms,
            production_id: $('#social-production-select')?.value || '',
          },
        });
        selectedRunId = res.run?.id || null;
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      } finally {
        btn.disabled = false;
      }
    };
  }
  if ($('#social-save-creds')) {
    $('#social-save-creds').onclick = async () => {
      const keys = ['meta_graph_version', 'meta_ig_id', 'meta_ig_token', 'meta_fb_page_id', 'meta_fb_page_token', 'youtube_channel_id', 'youtube_oauth_token'];
      const credentials = {};
      for (const k of keys) {
        const v = $('#cred-' + k)?.value.trim();
        if (v) credentials[k] = v;
      }
      try {
        const res = await agentPost('/agents/social/credentials', { credentials });
        keys.forEach(k => { if ($('#cred-' + k)) $('#cred-' + k).value = ''; });
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      }
    };
  }

  async function pullFromFactoriExtension() {
    if (typeof readerRequest !== 'function') {
      throw Error('Factori Bridge extension reader is unavailable in this window.');
    }
    const res = await readerRequest('get-factori-catalog', '');
    if (!res || !Array.isArray(res.cards) || !res.cards.length) {
      throw Error('No scanned multi-page batch found in Factori Bridge. Open the extension → Browse all pages → Sync all to Studio Catalog Agent.');
    }
    const staged = await agentPost('/agents/catalog/stage', {
      source: 'extension',
      cards: res.cards,
      pages_count: res.pages_count || 1,
      total_pages: res.total_pages || null,
    });
    $('#catalog-source').value = 'extension';
    $('#catalog-csv-wrap').hidden = true;
    notice(staged.message + ' Click Run Catalog Sync to import.');
    await refreshAgents();
  }

  if ($('#catalog-source')) {
    $('#catalog-source').onchange = () => {
      $('#catalog-csv-wrap').hidden = $('#catalog-source').value !== 'csv';
    };
  }
  if ($('#catalog-csv-file')) {
    $('#catalog-csv-file').onchange = async e => {
      const file = e.target.files?.[0];
      if (!file) {
        stagedCsvText = '';
        stagedCsvName = '';
        return;
      }
      stagedCsvText = await file.text();
      stagedCsvName = file.name;
      if (agentState) renderCatalogPanel(agentState);
    };
  }
  if ($('#catalog-pull-extension')) {
    $('#catalog-pull-extension').onclick = async () => {
      const btn = $('#catalog-pull-extension');
      btn.disabled = true;
      try {
        await pullFromFactoriExtension();
      } catch (err) {
        notice(err.message, true);
      } finally {
        btn.disabled = false;
      }
    };
  }
  if ($('#catalog-run-sync')) {
    $('#catalog-run-sync').onclick = async () => {
      const btn = $('#catalog-run-sync');
      btn.disabled = true;
      try {
        const source = $('#catalog-source').value;
        const payload = {
          source,
          fetch_store_gallery: Boolean($('#catalog-fetch-photos').checked),
        };
        if (source === 'csv') {
          if (!stagedCsvText) throw Error('Choose a CSV file before running CSV catalog sync.');
          payload.csv_content = stagedCsvText;
        }
        const res = await agentPost('/agents/run', { agent_id: 'catalog_agent', payload });
        selectedRunId = res.run?.id || null;
        notice(res.message);
        await refreshAgents();
      } catch (err) {
        notice(err.message, true);
      } finally {
        btn.disabled = false;
      }
    };
  }

  if (location.hash.startsWith('#catalog-sync=')) {
    history.replaceState(null, '', location.pathname + location.search);
    setTimeout(() => {
      if (typeof openTab === 'function') openTab('agents');
      pullFromFactoriExtension().catch(err => notice(err.message, true));
    }, 350);
  }

  ['#agent-ollama-url', '#agent-default-model', '#agent-exec-mode', '#agent-max-retries', '#agent-timeout'].forEach(sel => {
    const el = $(sel);
    if (el) el.addEventListener('input', () => { configDirty = true; });
    if (el) el.addEventListener('change', () => { configDirty = true; });
  });

  $('#agent-save-config').onclick = async () => {
    const btn = $('#agent-save-config');
    btn.disabled = true;
    try {
      const res = await agentPost('/agents/config', {
        ollama_base_url: $('#agent-ollama-url').value.trim(),
        default_model: $('#agent-default-model').value.trim(),
        execution_mode: $('#agent-exec-mode').value,
        max_retries: Number($('#agent-max-retries').value),
        request_timeout_seconds: Number($('#agent-timeout').value),
      });
      configDirty = false;
      notice(res.message);
      await refreshAgents();
    } catch (err) {
      notice(err.message, true);
    } finally {
      btn.disabled = false;
    }
  };

  $('#agent-test-ollama').onclick = async () => {
    const btn = $('#agent-test-ollama');
    const out = $('#ollama-status');
    btn.disabled = true;
    out.className = 'ollama-status';
    out.textContent = 'Testing connection to local Ollama…';
    try {
      const res = await agentPost('/agents/ollama/test', {
        ollama_base_url: $('#agent-ollama-url').value.trim(),
        default_model: $('#agent-default-model').value.trim(),
      });
      if (res.ok && res.model_available) {
        out.className = 'ollama-status ok';
        out.textContent = `Connected to ${res.base_url} (${res.latency_ms} ms). Model "${res.configured_model}" is available. Installed: ${res.installed_models.join(', ') || 'none'}.`;
      } else if (res.ok) {
        out.className = 'ollama-status warn';
        out.textContent = `${res.error} Installed models: ${res.installed_models.join(', ') || 'none'}.`;
      } else {
        out.className = 'ollama-status fail';
        out.textContent = res.error || 'Could not reach Ollama.';
      }
    } catch (err) {
      out.className = 'ollama-status fail';
      out.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  };

  $('#agent-history-filter').onchange = () => {
    if (agentState) renderHistory(agentState);
  };
  $('#agent-history-refresh').onclick = () => {
    refreshAgents().catch(e => notice(e.message, true));
  };

  window.refreshAgents = refreshAgents;
})();
