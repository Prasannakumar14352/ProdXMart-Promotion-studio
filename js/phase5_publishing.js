(function () {
  let calMode = 'month';
  let anchorDate = new Date();
  let selectedCalItem = null;

  function make(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }

  function istYmd(dateObj) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(dateObj);
    const get = t => parts.find(p => p.type === t)?.value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  }

  function istTime(iso) {
    if (!iso) return 'Unscheduled';
    try {
      return new Date(iso).toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
      }) + ' IST';
    } catch {
      return iso;
    }
  }

  function collectCalendarItems() {
    const platFilter = document.getElementById('cal-plat-filter')?.value || '';
    const statusFilter = document.getElementById('cal-status-filter')?.value || '';
    const items = [];

    const socialPosts = window.__latestAgentState?.social_posts || [];
    for (const p of socialPosts) {
      const ts = p.scheduled_at || p.published_at || p.approved_at || p.created_at;
      const day = ts ? istYmd(new Date(ts)) : istYmd(new Date());
      items.push({
        id: p.id,
        source: 'social',
        platform: p.platform,
        platformLabel: p.platform.replace('_', ' '),
        sku: p.sku,
        title: `${p.platform.replace('_', ' ')} · ${p.sku}`,
        status: p.status,
        ts,
        day,
        raw: p,
      });
    }

    const jobs = (typeof state !== 'undefined' && state?.jobs) ? state.jobs : [];
    for (const j of jobs) {
      const ts = j.due || j.created;
      const day = ts ? istYmd(new Date(ts)) : istYmd(new Date());
      items.push({
        id: j.id,
        source: 'reel_queue',
        platform: 'reel_queue',
        platformLabel: 'Reel Queue',
        sku: j.sku,
        title: `Reel · ${j.name} (${j.sku})`,
        status: j.status,
        ts,
        day,
        raw: j,
      });
    }

    return items.filter(it => {
      if (platFilter && it.platform !== platFilter) return false;
      if (statusFilter && it.status !== statusFilter) return false;
      return true;
    });
  }

  function renderCalendarDetail(item) {
    const box = document.getElementById('pub-cal-detail');
    if (!box) return;
    if (!item) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    box.hidden = false;
    box.replaceChildren();
    const head = make('div', '', 'agent-detail-head');
    head.append(make('strong', `${item.title} (${item.status})`));
    const close = make('button', 'Close');
    close.type = 'button';
    close.onclick = () => { selectedCalItem = null; renderCalendarDetail(null); };
    head.append(close);

    const meta = make('p', `Source: ${item.source} · Date (IST): ${item.day} · Time: ${istTime(item.ts)} · SKU: ${item.sku}`, 'hint');
    const act = make('div', '', 'agent-table-actions');
    act.style.marginTop = '8px';
    if (item.source === 'social') {
      const goBtn = make('button', 'Open in Approval Center', 'primary');
      goBtn.type = 'button';
      goBtn.onclick = () => window.StudioUI?.navigate('approvals');
      act.append(goBtn);
      if (['Approved', 'Scheduled'].includes(item.raw.status) && !window.__latestAgentState?.social_settings?.global_publishing_stopped) {
        const dl = make('a', 'Download Package (.zip)');
        dl.href = `/agents/social/package/${item.id}.zip?mode=${typeof mode === 'function' ? mode() : 'store'}`;
        dl.download = `${item.id}_${item.platform}.zip`;
        act.append(dl);
      }
    } else {
      const dl = make('a', 'Download Reel MP4');
      dl.href = `/video/${item.id}.mp4`;
      dl.download = `${item.id}.mp4`;
      act.append(dl);
    }
    box.append(head, meta, act);
  }

  function renderPublishingCalendar() {
    const host = document.getElementById('pub-cal-grid');
    const periodEl = document.getElementById('cal-period-label');
    const stopBanner = document.getElementById('cal-stop-banner');
    const stopBtn = document.getElementById('cal-global-stop-mirror');
    if (!host) return;

    const stopped = Boolean(window.__latestAgentState?.social_settings?.global_publishing_stopped);
    if (stopBanner) {
      stopBanner.hidden = !stopped;
      stopBanner.textContent = stopped
        ? `Global Publishing Stop ACTIVE (${window.__latestAgentState?.social_settings?.global_stop_reason || 'Paused by operator'}). Manual & automated publishing locked.`
        : '';
    }
    if (stopBtn) {
      stopBtn.textContent = stopped ? 'Resume Publishing' : 'Stop All Publishing';
      stopBtn.onclick = () => document.getElementById('social-global-stop')?.click();
    }

    document.querySelectorAll('.cal-mode-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.calmode === calMode);
    });

    const items = collectCalendarItems();
    const byDay = new Map();
    for (const it of items) {
      if (!byDay.has(it.day)) byDay.set(it.day, []);
      byDay.get(it.day).push(it);
    }

    const todayKey = istYmd(new Date());
    host.replaceChildren();

    if (calMode === 'month') {
      const y = anchorDate.getFullYear();
      const m = anchorDate.getMonth();
      if (periodEl) periodEl.textContent = new Date(y, m, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' }) + ' (IST)';
      const firstDay = new Date(y, m, 1);
      const startOffset = firstDay.getDay();
      const grid = make('div', '', 'pub-cal-month');
      ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].forEach(d => grid.append(make('div', d, 'pub-cal-dow')));
      for (let i = 0; i < 42; i++) {
        const cellDate = new Date(y, m, 1 - startOffset + i);
        const key = istYmd(cellDate);
        const cell = make('div', '', 'pub-cal-cell' + (cellDate.getMonth() !== m ? ' is-muted' : '') + (key === todayKey ? ' is-today' : ''));
        cell.append(make('span', String(cellDate.getDate()), 'pub-cal-daynum'));
        for (const it of (byDay.get(key) || []).slice(0, 4)) {
          const chip = make('button', `${istTime(it.ts).replace(' IST', '')} ${it.platformLabel}: ${it.sku}`, `pub-cal-chip status-${it.status}`);
          chip.type = 'button';
          chip.title = `${it.title} (${it.status})`;
          chip.onclick = () => { selectedCalItem = it; renderCalendarDetail(it); };
          cell.append(chip);
        }
        const extra = (byDay.get(key) || []).length - 4;
        if (extra > 0) cell.append(make('small', `+${extra} more`, 'hint'));
        grid.append(cell);
      }
      host.append(grid);
    } else if (calMode === 'week') {
      const start = new Date(anchorDate);
      start.setDate(start.getDate() - start.getDay());
      const end = new Date(start);
      end.setDate(start.getDate() + 6);
      if (periodEl) periodEl.textContent = `${istYmd(start)} to ${istYmd(end)} (IST)`;
      const grid = make('div', '', 'pub-cal-week');
      for (let i = 0; i < 7; i++) {
        const d = new Date(start);
        d.setDate(start.getDate() + i);
        const key = istYmd(d);
        const cell = make('div', '', 'pub-cal-cell' + (key === todayKey ? ' is-today' : ''));
        cell.append(make('strong', d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' }), 'pub-cal-daynum'));
        for (const it of byDay.get(key) || []) {
          const chip = make('button', `${it.platformLabel} · ${it.sku} (${it.status})`, `pub-cal-chip status-${it.status}`);
          chip.type = 'button';
          chip.onclick = () => { selectedCalItem = it; renderCalendarDetail(it); };
          cell.append(chip);
        }
        grid.append(cell);
      }
      host.append(grid);
    } else {
      const key = istYmd(anchorDate);
      if (periodEl) periodEl.textContent = `${key} (IST Daily Schedule)`;
      const list = make('div', '', 'pub-cal-daylist');
      const dayItems = byDay.get(key) || [];
      if (!dayItems.length) {
        list.append(make('p', `No scheduled or draft promotions on ${key}.`, 'hint'));
      } else {
        for (const it of dayItems) {
          const row = make('div', '', 'list-row');
          row.append(make('strong', `${istTime(it.ts)} — ${it.title}`), make('span', it.status, 'badge'));
          row.style.cursor = 'pointer';
          row.onclick = () => { selectedCalItem = it; renderCalendarDetail(it); };
          list.append(row);
        }
      }
      host.append(list);
    }
    renderCalendarDetail(selectedCalItem);
  }

  function refreshApprovalSummary() {
    const strip = document.getElementById('approval-summary-strip');
    if (!strip || !window.__latestAgentState) return;
    const posts = window.__latestAgentState.social_posts || [];
    const videos = window.__latestAgentState.video_productions || [];
    const stopped = Boolean(window.__latestAgentState.social_settings?.global_publishing_stopped);

    const counts = {
      awaiting: posts.filter(p => ['Draft', 'Awaiting Approval'].includes(p.status)).length,
      approved: posts.filter(p => p.status === 'Approved').length,
      scheduled: posts.filter(p => p.status === 'Scheduled').length,
      published: posts.filter(p => p.status === 'Published').length,
      qaPending: videos.filter(v => v.qa_status === 'passed' && v.human_review_status === 'pending').length,
    };
    strip.replaceChildren();
    for (const [lbl, val] of [
      ['Pending Social Approval', counts.awaiting],
      ['Videos Waiting QA Sign-off', counts.qaPending],
      ['Approved Packages', counts.approved],
      ['Scheduled (IST)', counts.scheduled],
      ['Published Posts', counts.published],
      ['Publishing Gate', stopped ? 'PAUSED' : 'Manual Approval'],
    ]) {
      const box = make('div', '', 'catalog-stat');
      box.append(make('small', lbl), make('b', String(val)));
      strip.append(box);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.cal-mode-btn').forEach(btn => {
      btn.onclick = () => { calMode = btn.dataset.calmode || 'month'; renderPublishingCalendar(); };
    });
    document.getElementById('cal-prev')?.addEventListener('click', () => {
      if (calMode === 'month') anchorDate.setMonth(anchorDate.getMonth() - 1);
      else if (calMode === 'week') anchorDate.setDate(anchorDate.getDate() - 7);
      else anchorDate.setDate(anchorDate.getDate() - 1);
      renderPublishingCalendar();
    });
    document.getElementById('cal-today')?.addEventListener('click', () => {
      anchorDate = new Date();
      renderPublishingCalendar();
    });
    document.getElementById('cal-next')?.addEventListener('click', () => {
      if (calMode === 'month') anchorDate.setMonth(anchorDate.getMonth() + 1);
      else if (calMode === 'week') anchorDate.setDate(anchorDate.getDate() + 7);
      else anchorDate.setDate(anchorDate.getDate() + 1);
      renderPublishingCalendar();
    });
    document.getElementById('cal-plat-filter')?.addEventListener('change', renderPublishingCalendar);
    document.getElementById('cal-status-filter')?.addEventListener('change', renderPublishingCalendar);
  });

  window.refreshPublishingCalendar = renderPublishingCalendar;
  window.refreshApprovalSummary = refreshApprovalSummary;
})();
