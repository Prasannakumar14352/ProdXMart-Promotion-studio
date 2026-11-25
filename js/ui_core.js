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

  window.StudioUI = {
    initShell,
    navigate,
    hydrateIcons,
    showToast,
    confirmAction,
    promptReason,
    updateNotifications,
    getRoute: () => currentRoute,
  };
})();
