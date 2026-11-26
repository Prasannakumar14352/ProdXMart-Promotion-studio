/* Shared UI Shell Core: Sidebar, Theme, Toasts, Confirm Dialog, Global Search, Notifications & Routing */
(function () {
  const ROUTE_META = {
    overview: { title: 'Overview', group: 'Workspace', section: 'overview' },
    catalogue: { title: 'Product Catalogue', group: 'Workspace', section: 'catalogue' },
    agents: { title: 'AI Agent Center', group: 'Workspace', section: 'agents', focus: '#agent-cards' },
    campaigns: { title: 'Campaigns', group: 'Marketing', section: 'agents', focus: '#marketing-panel' },
    video: { title: 'Video Studio', group: 'Marketing', section: 'agents', focus: '#video-qa-panel' },
    approvals: { title: 'Approval Center', group: 'Marketing', section: 'agents', focus: '#social-approval-panel' },
    calendar: { title: 'Publishing Calendar', group: 'Marketing', section: 'queue' },
    analytics: { title: 'Sales Analytics', group: 'Business', section: 'report' },
    expenses: { title: 'Expenses & Profit', group: 'Business', section: 'report' },
    integrations: { title: 'Integrations', group: 'System', section: 'agents', focus: '#agent-config-anchor' },
    history: { title: 'Execution History', group: 'System', section: 'agents', focus: '.agent-history-panel' },
    settings: { title: 'Settings', group: 'System', section: 'agents', focus: '#agent-config-anchor' },
  };

  let currentRoute = localStorage.getItem('prodx_route') || 'overview';
  if (!ROUTE_META[currentRoute]) currentRoute = 'overview';

  function hydrateIcons(root) {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      try {
        window.lucide.createIcons({ root: root || document });
      } catch {
        window.lucide.createIcons();
      }
    }
  }

  function applyTheme(theme) {
    const next = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('prodx_theme', next);
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.setAttribute('aria-label', next === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
      btn.title = next === 'dark' ? 'Light theme' : 'Dark theme';
      btn.innerHTML = `<i data-lucide="${next === 'dark' ? 'sun' : 'moon'}" width="17" height="17"></i>`;
      hydrateIcons(btn);
    }
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  }

  function applySidebarState(collapsed) {
    const shell = document.getElementById('app-shell');
    if (!shell) return;
    shell.classList.toggle('sidebar-collapsed', Boolean(collapsed));
    localStorage.setItem('prodx_sidebar_collapsed', collapsed ? '1' : '0');
  }

  function showToast(message, isError) {
    const stack = document.getElementById('toast-stack');
    if (!stack || !message) return;
    const item = document.createElement('div');
    item.className = 'toast' + (isError ? ' error' : '');
    const txt = document.createElement('span');
    txt.textContent = message;
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-ghost';
    closeBtn.style.height = '24px';
    closeBtn.style.padding = '0 6px';
    closeBtn.textContent = '×';
    closeBtn.onclick = () => item.remove();
    item.append(txt, closeBtn);
    stack.append(item);
    setTimeout(() => { if (item.parentNode) item.remove(); }, 4500);
  }

  function confirmAction(title, message, confirmText) {
    return new Promise(resolve => {
      const dlg = document.getElementById('confirm-dialog');
      if (!dlg) { resolve(window.confirm(message)); return; }
      document.getElementById('confirm-title').textContent = title || 'Confirm action';
      document.getElementById('confirm-message').textContent = message || '';
      const okBtn = document.getElementById('confirm-ok');
      const cancelBtn = document.getElementById('confirm-cancel');
      okBtn.textContent = confirmText || 'Confirm';
      function finish(val) {
        okBtn.onclick = null;
        cancelBtn.onclick = null;
        dlg.close();
        resolve(val);
      }
      okBtn.onclick = () => finish(true);
      cancelBtn.onclick = () => finish(false);
      dlg.showModal();
    });
  }

  function promptReason(title, subtitle, submitLabel) {
    return new Promise(resolve => {
      const dlg = document.getElementById('reason-dialog');
      if (!dlg) { resolve(window.prompt(subtitle || title)); return; }
      document.getElementById('reason-title').textContent = title || 'Operator Note';
      document.getElementById('reason-subtitle').textContent = subtitle || '';
      const input = document.getElementById('reason-input');
      const err = document.getElementById('reason-error');
      const okBtn = document.getElementById('reason-submit');
      const cancelBtn = document.getElementById('reason-cancel');
      input.value = '';
      err.textContent = '';
      okBtn.textContent = submitLabel || 'Submit';
      function finish(val) {
        okBtn.onclick = null;
        cancelBtn.onclick = null;
        dlg.close();
        resolve(val);
      }
      okBtn.onclick = () => {
        const cleaned = input.value.trim();
        if (!cleaned) {
          err.textContent = 'Please enter a short reason or note before submitting.';
          input.focus();
          return;
        }
        finish(cleaned);
      };
      cancelBtn.onclick = () => finish(null);
      dlg.showModal();
      input.focus();
    });
  }

  function navigate(route, opts) {
    const targetRoute = ROUTE_META[route] ? route : 'overview';
    currentRoute = targetRoute;
    localStorage.setItem('prodx_route', targetRoute);
    if (!opts?.skipUrlSync) {
      try {
        const u = new URL(location.href);
        u.searchParams.set('route', targetRoute);
        history.replaceState(null, '', u.pathname + u.search + u.hash);
      } catch {}
    }
    const meta = ROUTE_META[targetRoute];

    document.querySelectorAll('.nav-item[data-route]').forEach(btn => {
      const active = btn.dataset.route === targetRoute;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-current', active ? 'page' : 'false');
    });

    const bc = document.getElementById('breadcrumb-current');
    if (bc) bc.textContent = meta.title;
    const bcGroup = document.getElementById('breadcrumb-group');
    if (bcGroup) bcGroup.textContent = meta.group;

    // Show target section and hide siblings.
    ['overview', 'catalogue', 'queue', 'report', 'agents'].forEach(secId => {
      const el = document.getElementById(secId);
      if (el) el.hidden = secId !== meta.section;
    });

    // Toggle dedicated sub-views inside #agents and #report so each navigation item is a focused screen.
    if (meta.section === 'agents') {
      document.querySelectorAll('#agents [data-subview]').forEach(el => {
        el.hidden = el.dataset.subview !== targetRoute;
      });
    }
    if (meta.section === 'report') {
      document.querySelectorAll('#report [data-reportview]').forEach(el => {
        el.hidden = el.dataset.reportview !== targetRoute;
      });
    }

    const pageHead = document.getElementById('legacy-page-head');
    if (pageHead) pageHead.hidden = meta.section === 'overview';

    if (typeof window.syncLegacyHeading === 'function') {
      window.syncLegacyHeading(meta.section, meta.title, targetRoute);
    }

    const shell = document.getElementById('app-shell');
    if (shell) shell.classList.remove('mobile-nav-open');

    if (meta.section === 'overview' && typeof window.refreshOverview === 'function') {
      window.refreshOverview().catch(() => {});
    }
    if (meta.section === 'queue' && typeof window.refreshPublishingCalendar === 'function') {
      if (typeof window.refreshAgents === 'function') window.refreshAgents().catch(() => {});
      window.refreshPublishingCalendar();
    }
    if (meta.section === 'report' && typeof window.refreshSalesAnalytics === 'function') {
      window.refreshSalesAnalytics(targetRoute).catch(() => {});
    }
    if (meta.section === 'agents') {
      if (typeof window.refreshAgents === 'function') window.refreshAgents().catch(() => {});
      if (typeof window.refreshAgentViews === 'function') window.refreshAgentViews(targetRoute).catch(() => {});
      if (typeof window.refreshPhase4Studio === 'function') window.refreshPhase4Studio(targetRoute);
      if (typeof window.refreshApprovalSummary === 'function') window.refreshApprovalSummary();
    }
  }

  function updateNotifications(overviewData) {
    const dot = document.getElementById('notif-dot');
    const list = document.getElementById('notif-list');
    const navPill = document.getElementById('nav-approval-count');
    if (!overviewData) return;

    const k = overviewData.kpis || {};
    const pending = k.pending_approvals || 0;
    if (navPill) {
      navPill.textContent = String(pending);
      navPill.hidden = pending <= 0;
    }

    const alerts = [];
    if (pending > 0) alerts.push({ text: `${pending} item(s) waiting for human approval`, route: 'approvals' });
    if ((k.attention_products || 0) > 0) alerts.push({ text: `${k.attention_products} product(s) require attention`, route: 'catalogue' });
    if (overviewData.integration_health?.global_publishing?.status === 'paused') {
      alerts.push({ text: 'Global publishing stop is currently active', route: 'approvals' });
    }
    const failedRuns = (overviewData.agent_activity || []).filter(r => r.status === 'failed').length;
    if (failedRuns > 0) alerts.push({ text: `${failedRuns} recent agent task(s) failed`, route: 'history' });

    if (dot) dot.hidden = alerts.length === 0;
    if (list) {
      list.replaceChildren();
      if (!alerts.length) {
        const empty = document.createElement('p');
        empty.className = 'card-sub';
        empty.textContent = 'No pending alerts in this workspace.';
        list.append(empty);
      } else {
        for (const al of alerts) {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'list-row';
          b.style.width = '100%';
          b.style.cursor = 'pointer';
          b.textContent = al.text;
          b.onclick = () => {
            document.getElementById('notif-popover').hidden = true;
            navigate(al.route);
          };
          list.append(b);
        }
      }
    }
  }

  function initShell() {
    applyTheme(localStorage.getItem('prodx_theme') || 'light');
    applySidebarState(localStorage.getItem('prodx_sidebar_collapsed') === '1');

    document.querySelectorAll('.nav-item[data-route]').forEach(btn => {
      btn.onclick = () => navigate(btn.dataset.route);
    });

    const toggle = document.getElementById('sidebar-toggle');
    if (toggle) {
      toggle.onclick = () => {
        const shell = document.getElementById('app-shell');
        if (window.innerWidth <= 820) {
          shell.classList.toggle('mobile-nav-open');
        } else {
          applySidebarState(!shell.classList.contains('sidebar-collapsed'));
        }
      };
    }

    const mobileBtn = document.getElementById('mobile-menu-btn');
    if (mobileBtn) {
      mobileBtn.onclick = () => document.getElementById('app-shell').classList.toggle('mobile-nav-open');
    }

    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.onclick = toggleTheme;

    const settingsBtn = document.getElementById('header-settings-btn');
    if (settingsBtn) settingsBtn.onclick = () => navigate('settings');

    const notifBtn = document.getElementById('notif-btn');
    const notifPop = document.getElementById('notif-popover');
    if (notifBtn && notifPop) {
      notifBtn.onclick = e => {
        e.stopPropagation();
        notifPop.hidden = !notifPop.hidden;
      };
      document.addEventListener('click', e => {
        if (!notifPop.hidden && !notifPop.contains(e.target) && e.target !== notifBtn) {
          notifPop.hidden = true;
        }
      });
    }

    const gSearch = document.getElementById('global-search-input');
    if (gSearch) {
      document.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          gSearch.focus();
          gSearch.select();
        }
      });
      gSearch.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          const q = gSearch.value.trim();
          const catSearch = document.getElementById('search');
          if (catSearch) {
            catSearch.value = q;
            catSearch.dispatchEvent(new Event('input'));
          }
          navigate('catalogue');
        }
      });
    }

    const modeBadge = document.getElementById('workspace-demo-tag');
    const modeSel = document.getElementById('mode');
    if (modeSel && modeBadge) {
      const syncTag = () => { modeBadge.hidden = modeSel.value !== 'demo'; };
      modeSel.addEventListener('change', syncTag);
      syncTag();
    }

    const params = new URLSearchParams(location.search);
    const urlMode = params.get('mode');
    if (modeSel && (urlMode === 'demo' || urlMode === 'store')) {
      modeSel.value = urlMode;
      const syncTag = () => { if (modeBadge) modeBadge.hidden = modeSel.value !== 'demo'; };
      syncTag();
    }
    const urlRoute = params.get('route');
    if (urlRoute && ROUTE_META[urlRoute]) currentRoute = urlRoute;

    // Honor legacy deep-link hashes used by Factori Chrome extension.
    if (location.hash.startsWith('#factori=')) currentRoute = 'catalogue';
    if (location.hash.startsWith('#catalog-sync=')) currentRoute = 'agents';

    navigate(currentRoute, { skipScroll: true, skipUrlSync: true });
    hydrateIcons(document);
  }

  const PLATFORMS = [
    { id: 'instagram_reels', name: 'Instagram Reels', short: 'Instagram', color: '#E1306C' },
    { id: 'facebook_reels', name: 'Facebook Reels', short: 'Facebook', color: '#1877F2' },
    { id: 'youtube_shorts', name: 'YouTube Shorts', short: 'YouTube', color: '#FF0000' },
  ];

  function createSvgEl(tag, attrs, children) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    for (const ch of (children || [])) el.append(ch);
    return el;
  }

  function createPlatformIcon(platformId, size) {
    const s = size || 16;
    if (platformId === 'instagram_reels') {
      return createSvgEl('svg', { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': 'true', class: 'plat-svg plat-ig' }, [
        createSvgEl('rect', { x: 2.5, y: 2.5, width: 19, height: 19, rx: 5.5, stroke: '#E1306C', 'stroke-width': 2.2 }),
        createSvgEl('circle', { cx: 12, cy: 12, r: 4.2, stroke: '#E1306C', 'stroke-width': 2.1 }),
        createSvgEl('circle', { cx: 17.3, cy: 6.7, r: 1.3, fill: '#E1306C' }),
      ]);
    }
    if (platformId === 'facebook_reels') {
      return createSvgEl('svg', { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': 'true', class: 'plat-svg plat-fb' }, [
        createSvgEl('circle', { cx: 12, cy: 12, r: 10, fill: '#1877F2' }),
        createSvgEl('path', { d: 'M13.5 21.8v-7.6h2.5l.4-3h-2.9V9.3c0-.9.3-1.5 1.5-1.5h1.5V5.1c-.3 0-1.2-.1-2.3-.1-2.2 0-3.8 1.4-3.8 3.9v2.3H8v3h2.4v7.6h3.1z', fill: '#FFFFFF' }),
      ]);
    }
    return createSvgEl('svg', { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': 'true', class: 'plat-svg plat-yt' }, [
      createSvgEl('rect', { x: 2, y: 4.5, width: 20, height: 15, rx: 4.2, fill: '#FF0000' }),
      createSvgEl('polygon', { points: '10,8.5 16.2,12 10,15.5', fill: '#FFFFFF' }),
    ]);
  }

  function createPlatformMultiSelect(container, options) {
    const host = typeof container === 'string' ? document.getElementById(container) : container;
    if (!host) return null;
    if (host.__pmPicker) return host.__pmPicker;

    const opts = options || {};
    const validIds = PLATFORMS.map(p => p.id);
    let committed = (Array.isArray(opts.initialSelected) && opts.initialSelected.length)
      ? opts.initialSelected.filter(v => validIds.includes(v))
      : (opts.initialSelected && Array.isArray(opts.initialSelected) ? [] : [...validIds]);
    let draft = [...committed];

    const root = document.createElement('div');
    root.className = 'pm-picker';
    if (opts.id) root.id = opts.id;

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'pm-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', opts.ariaLabel || 'Select target social media platforms');

    const iconGroup = document.createElement('span');
    iconGroup.className = 'pm-icons';
    const textSpan = document.createElement('span');
    textSpan.className = 'pm-label';
    const countBadge = document.createElement('span');
    countBadge.className = 'pm-count';
    const caret = document.createElement('span');
    caret.className = 'pm-caret';
    caret.textContent = '▾';
    trigger.append(iconGroup, textSpan, countBadge, caret);

    const dropdown = document.createElement('div');
    dropdown.className = 'pm-dropdown';
    dropdown.hidden = true;
    dropdown.setAttribute('role', 'region');
    dropdown.setAttribute('aria-label', opts.ariaLabel || 'Platform choices');

    const topBar = document.createElement('div');
    topBar.className = 'pm-actions-top';
    const selAllBtn = document.createElement('button');
    selAllBtn.type = 'button';
    selAllBtn.className = 'btn-ghost btn-sm';
    selAllBtn.textContent = 'Select All';
    const clrAllBtn = document.createElement('button');
    clrAllBtn.type = 'button';
    clrAllBtn.className = 'btn-ghost btn-sm';
    clrAllBtn.textContent = 'Clear All';
    topBar.append(selAllBtn, clrAllBtn);

    const listBox = document.createElement('div');
    listBox.className = 'pm-options';
    listBox.setAttribute('role', 'listbox');
    listBox.setAttribute('aria-multiselectable', 'true');

    const checkInputs = new Map();
    const optionButtons = [];
    for (const plat of PLATFORMS) {
      const row = document.createElement('label');
      row.className = 'pm-option';
      row.setAttribute('role', 'option');
      row.tabIndex = 0;

      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.value = plat.id;
      chk.tabIndex = -1;
      checkInputs.set(plat.id, chk);

      const iconWrap = document.createElement('span');
      iconWrap.className = 'pm-opt-icon';
      iconWrap.append(createPlatformIcon(plat.id, 16));

      const lbl = document.createElement('span');
      lbl.className = 'pm-opt-name';
      lbl.textContent = plat.name;

      row.append(chk, iconWrap, lbl);
      listBox.append(row);
      optionButtons.push(row);

      chk.addEventListener('change', () => {
        if (chk.checked) {
          if (!draft.includes(plat.id)) draft.push(plat.id);
        } else {
          draft = draft.filter(v => v !== plat.id);
        }
        syncDraftUi();
      });
      row.addEventListener('keydown', e => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          chk.checked = !chk.checked;
          chk.dispatchEvent(new Event('change'));
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          const idx = optionButtons.indexOf(row);
          if (idx < optionButtons.length - 1) optionButtons[idx + 1].focus();
          else applyBtn.focus();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const idx = optionButtons.indexOf(row);
          if (idx > 0) optionButtons[idx - 1].focus();
          else selAllBtn.focus();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          closeDropdown(true);
        }
      });
    }

    const footer = document.createElement('div');
    footer.className = 'pm-footer';
    const draftLabel = document.createElement('small');
    draftLabel.className = 'hint';
    const applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'btn-sm primary pm-apply';
    applyBtn.textContent = 'Apply';
    footer.append(draftLabel, applyBtn);

    dropdown.append(topBar, listBox, footer);

    const hiddenMirror = document.createElement('div');
    hiddenMirror.hidden = true;
    hiddenMirror.setAttribute('aria-hidden', 'true');
    if (opts.syncHiddenName) {
      for (const plat of PLATFORMS) {
        const h = document.createElement('input');
        h.type = 'checkbox';
        h.name = opts.syncHiddenName;
        h.value = plat.id;
        hiddenMirror.append(h);
      }
    }

    root.append(trigger, dropdown, hiddenMirror);
    host.replaceChildren(root);

    function syncTriggerUi() {
      iconGroup.replaceChildren();
      for (const id of committed) iconGroup.append(createPlatformIcon(id, 14));
      if (committed.length === PLATFORMS.length) {
        textSpan.textContent = 'All Platforms Selected';
      } else if (committed.length === 0) {
        textSpan.textContent = '0 platforms selected';
      } else {
        textSpan.textContent = `${committed.length} platform${committed.length === 1 ? '' : 's'} selected`;
      }
      countBadge.textContent = String(committed.length);
      trigger.title = committed.length
        ? committed.map(id => PLATFORMS.find(p => p.id === id)?.name || id).join(', ')
        : 'No platforms selected';
      if (opts.syncHiddenName) {
        hiddenMirror.querySelectorAll('input[type="checkbox"]').forEach(el => {
          el.checked = committed.includes(el.value);
        });
      }
    }

    function syncDraftUi() {
      for (const plat of PLATFORMS) {
        const chk = checkInputs.get(plat.id);
        const checked = draft.includes(plat.id);
        if (chk) chk.checked = checked;
        chk?.closest('.pm-option')?.setAttribute('aria-selected', checked ? 'true' : 'false');
      }
      draftLabel.textContent = `${draft.length} of ${PLATFORMS.length} selected`;
    }

    function positionDropdown() {
      dropdown.classList.remove('align-right', 'drop-up');
      const rect = root.getBoundingClientRect();
      const ddRect = dropdown.getBoundingClientRect();
      if (rect.left + Math.max(250, ddRect.width) > window.innerWidth - 12) {
        dropdown.classList.add('align-right');
      }
      if (rect.bottom + ddRect.height > window.innerHeight - 12 && rect.top > ddRect.height + 16) {
        dropdown.classList.add('drop-up');
      }
    }

    function openDropdown() {
      draft = [...committed];
      syncDraftUi();
      dropdown.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      positionDropdown();
    }

    function closeDropdown(restoreFocus) {
      if (dropdown.hidden) return;
      dropdown.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    }

    function applySelection() {
      if (opts.minSelected && draft.length < opts.minSelected) {
        showToast(`Select at least ${opts.minSelected} platform.`, true);
        return;
      }
      committed = PLATFORMS.map(p => p.id).filter(id => draft.includes(id));
      syncTriggerUi();
      closeDropdown(true);
      if (typeof opts.onApply === 'function') opts.onApply([...committed]);
    }

    trigger.addEventListener('click', e => {
      e.stopPropagation();
      if (dropdown.hidden) openDropdown();
      else closeDropdown(false);
    });
    trigger.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (dropdown.hidden) openDropdown();
        optionButtons[0]?.focus();
      } else if (e.key === 'Escape') {
        closeDropdown(false);
      }
    });

    selAllBtn.addEventListener('click', () => {
      draft = [...validIds];
      syncDraftUi();
    });
    clrAllBtn.addEventListener('click', () => {
      draft = [];
      syncDraftUi();
    });
    applyBtn.addEventListener('click', applySelection);
    dropdown.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeDropdown(true);
      }
    });
    dropdown.addEventListener('click', e => e.stopPropagation());

    document.addEventListener('click', e => {
      if (!dropdown.hidden && !root.contains(e.target)) {
        closeDropdown(false);
      }
    });

    syncTriggerUi();
    syncDraftUi();

    const api = {
      getSelected: () => [...committed],
      setSelected: (nextIds, setOpts) => {
        const list = Array.isArray(nextIds) ? nextIds.filter(id => validIds.includes(id)) : [...validIds];
        committed = PLATFORMS.map(p => p.id).filter(id => list.includes(id));
        draft = [...committed];
        syncTriggerUi();
        syncDraftUi();
        if (setOpts?.emit && typeof opts.onApply === 'function') opts.onApply([...committed]);
      },
    };
    host.__pmPicker = api;
    return api;
  }

  let activeMenuEl = null;
  function closeActiveMenu() {
    if (activeMenuEl) {
      activeMenuEl.remove();
      activeMenuEl = null;
    }
  }

  function openActionMenu(anchorEl, items) {
    closeActiveMenu();
    if (!anchorEl || !items?.length) return;
    const menu = document.createElement('div');
    menu.className = 'ctx-menu';
    menu.setAttribute('role', 'menu');
    for (const item of items) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ctx-menu-item' + (item.danger ? ' is-danger' : '');
      btn.setAttribute('role', 'menuitem');
      btn.disabled = Boolean(item.disabled);
      btn.textContent = item.label;
      btn.onclick = async () => {
        closeActiveMenu();
        if (typeof item.onClick === 'function') await item.onClick();
      };
      menu.append(btn);
    }
    document.body.append(menu);
    activeMenuEl = menu;

    const r = anchorEl.getBoundingClientRect();
    const mr = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - mr.width - 8, r.right - mr.width));
    const top = (r.bottom + 4 + mr.height > window.innerHeight - 8)
      ? Math.max(8, r.top - mr.height - 4)
      : r.bottom + 4;
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.querySelector('button:not(:disabled)')?.focus();

    const onOutside = e => {
      if (activeMenuEl && !activeMenuEl.contains(e.target) && e.target !== anchorEl) {
        closeActiveMenu();
        document.removeEventListener('click', onOutside);
        document.removeEventListener('keydown', onKey);
      }
    };
    const onKey = e => {
      if (e.key === 'Escape') {
        closeActiveMenu();
        anchorEl.focus();
        document.removeEventListener('click', onOutside);
        document.removeEventListener('keydown', onKey);
      }
    };
    setTimeout(() => {
      document.addEventListener('click', onOutside);
      document.addEventListener('keydown', onKey);
    }, 0);
  }

  function confirmPublishedModal(post) {
    return new Promise(resolve => {
      const dlg = document.getElementById('confirm-published-dialog');
      if (!dlg) {
        const url = window.prompt(`Enter the actual HTTPS URL for ${post.platform} post (${post.sku}):`);
        resolve(url ? { published_url: url.trim(), external_post_id: '' } : null);
        return;
      }
      const platMeta = PLATFORMS.find(p => p.id === post.platform);
      document.getElementById('confpub-meta').textContent = `${platMeta?.name || post.platform} · ${post.sku} (${post.id})`;
      const urlIn = document.getElementById('confpub-url');
      const extIn = document.getElementById('confpub-ext-id');
      const errEl = document.getElementById('confpub-error');
      const cancelBtn = document.getElementById('confpub-cancel');
      const okBtn = document.getElementById('confpub-submit');
      urlIn.value = post.published_url || '';
      extIn.value = post.external_post_id || '';
      errEl.textContent = '';

      function finish(val) {
        okBtn.onclick = null;
        cancelBtn.onclick = null;
        dlg.close();
        resolve(val);
      }
      okBtn.onclick = () => {
        const rawUrl = urlIn.value.trim();
        if (!rawUrl.startsWith('https://')) {
          errEl.textContent = 'Enter a valid HTTPS post URL returned after publishing.';
          urlIn.focus();
          return;
        }
        finish({ published_url: rawUrl, external_post_id: extIn.value.trim() });
      };
      cancelBtn.onclick = () => finish(null);
      dlg.showModal();
      urlIn.focus();
    });
  }

  function openContentExpandModal(post, initial) {
    return new Promise(resolve => {
      const dlg = document.getElementById('content-expand-dialog');
      if (!dlg) { resolve(null); return; }
      const platMeta = PLATFORMS.find(p => p.id === post.platform);
      document.getElementById('expand-ed-meta').textContent = `${platMeta?.name || post.platform} · ${post.sku} (${post.id})`;
      const warnEl = document.getElementById('expand-ed-warn');
      warnEl.hidden = !['Approved', 'Scheduled'].includes(post.status);

      const titleIn = document.getElementById('expand-ed-title');
      const capIn = document.getElementById('expand-ed-caption');
      const descIn = document.getElementById('expand-ed-desc');
      const tagsIn = document.getElementById('expand-ed-tags');
      const linkIn = document.getElementById('expand-ed-link');
      const errEl = document.getElementById('expand-ed-error');
      const cancelBtn = document.getElementById('expand-ed-cancel');
      const saveBtn = document.getElementById('expand-ed-save');

      titleIn.value = initial.title || '';
      capIn.value = initial.caption || '';
      descIn.value = initial.description || '';
      tagsIn.value = initial.hashtags || '';
      linkIn.value = initial.product_link || '';
      errEl.textContent = '';

      function finish(val) {
        saveBtn.onclick = null;
        cancelBtn.onclick = null;
        dlg.close();
        resolve(val);
      }
      saveBtn.onclick = () => {
        if (!capIn.value.trim()) {
          errEl.textContent = 'Caption cannot be empty.';
          capIn.focus();
          return;
        }
        if (!linkIn.value.trim().startsWith('https://')) {
          errEl.textContent = 'Product link must be a valid HTTPS URL.';
          linkIn.focus();
          return;
        }
        finish({
          title: titleIn.value,
          caption: capIn.value,
          description: descIn.value,
          hashtags: tagsIn.value,
          product_link: linkIn.value,
        });
      };
      cancelBtn.onclick = () => finish(null);
      dlg.showModal();
      capIn.focus();
    });
  }

  window.StudioUI = {
    initShell,
    navigate,
    hydrateIcons,
    showToast,
    confirmAction,
    promptReason,
    updateNotifications,
    PLATFORMS,
    createPlatformIcon,
    createPlatformMultiSelect,
    openActionMenu,
    closeActiveMenu,
    confirmPublishedModal,
    openContentExpandModal,
    getRoute: () => currentRoute,
  };
})();
