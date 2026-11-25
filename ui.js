const $ = s => document.querySelector(s);
let state, busy = false, sequence = 0;
let activeTab = 'overview';
const mode = () => $('#mode').value;
const money = n => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(n);
function element(tag, text) { const e = document.createElement(tag); e.textContent = text; return e; }
function notice(msg, bad = false) {
  if ($('#feedback')) {
    $('#feedback').textContent = msg;
    $('#feedback').className = bad ? 'message error' : 'message';
  }
  if (msg && window.StudioUI) window.StudioUI.showToast(msg, bad);
}
function table(target, columns, rows) {
  const t = document.createElement('table'), header = t.createTHead().insertRow(), body = t.createTBody();
  t.className = 'data-table';
  columns.forEach(k => header.append(element('th', k)));
  rows.forEach(row => { const tr = body.insertRow(); row.forEach(v => tr.append(element('td', v))); });
  $(target).replaceChildren(t);
}
async function refresh() {
  const seq = ++sequence;
  const res = await fetch('/state?mode=' + mode()), s = await res.json();
  if (seq !== sequence) return;
  if (!res.ok) {
    state = null;
    ['#metrics', '#cards', '#products', '#campaigns'].forEach(k => $(k).replaceChildren());
    $('#approve').disabled = true;
    throw Error(s.error);
  }
  state = s;
  const demo = mode() === 'demo';
  $('#add').hidden = demo;
  $('#factori-help').hidden = demo;
  $('#factori-review').hidden = demo || !importedCard;
  updateHeading();
  $('#conn').textContent = demo
    ? 'Demo workspace · local simulation'
    : (s.enabled ? 'Instagram publishing configured · account verification occurs at publish time' : 'Instagram disconnected · draft and scheduling tools available');
  $('#start').value ||= s.start;
  $('#metrics').replaceChildren();
  for (const [k, v] of [['Delivered orders', s.earnings.orders], ['Units after returns', s.earnings.units], ['Sales after refunds', money(s.earnings.sales)], ['Estimated profit', money(s.earnings.profit)]]) {
    const d = element('div', ''); d.className = 'metric'; d.append(element('small', k), element('b', v)); $('#metrics').append(d);
  }
  renderProducts();
  const known = new Set(s.jobs.map(j => j.id)), groups = {};
  for (const [id, n] of Object.entries(s.earnings.campaigns)) { const k = known.has(id) ? id : 'Unattributed'; groups[k] = (groups[k] || 0) + n; }
  const campaignIDs = new Set([...Object.keys(groups), ...Object.keys(s.traffic)]);
  table('#campaigns', ['Campaign', 'Tracked sessions', 'Units after returns'], [...campaignIDs].map(k => [k, s.traffic[k] ?? '—', groups[k] ?? 0]));
  $('#bio').hidden = !s.jobs.some(j => ['Scheduled', 'Published', 'Simulated'].includes(j.status));
  $('#bio').href = '/shop-links?mode=' + mode();
  $('#cards').replaceChildren();
  for (const j of s.jobs) {
    const a = $('#card').content.cloneNode(true), find = k => a.querySelector(k);
    find('video').src = '/video/' + j.id + '.mp4#t=0.1';
    for (const [key, value] of [['.sku', j.sku], ['.status', j.status], ['h3', j.name], ['.caption', j.caption], ['.time', j.due ? 'Scheduled ' + new Date(j.due).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST' : ''], ['.note', j.note]]) find(key).textContent = value;
    const buttons = find('.cardactions');
    if (j.status === 'Draft' || j.status === 'Scheduled') {
      for (const [label, action] of [['Draft' === j.status ? 'Approve' : 'Reschedule', 'approve'], ['Skip', 'skip']]) {
        const b = element('button', label); b.onclick = () => change(action, [j.id]); buttons.append(b);
      }
    }
    if (j.status === 'Draft') {
      const remake = element('button', 'Remake with music');
      remake.onclick = () => post('/remake', { id: j.id }); buttons.append(remake);
    }
    const file = element('a', 'Save video'); file.href = '/video/' + j.id + '.mp4'; file.download = j.id + '.mp4'; buttons.append(file);
    const link = element('a', 'Product link'); link.href = j.link; link.target = '_blank'; link.rel = 'noopener'; buttons.append(link);
    $('#cards').append(a);
  }
  if (!s.jobs.length) {
    const h = element('div', ''); h.className = 'empty';
    h.append(element('strong', 'Your reel queue is empty'), element('p', demo ? 'Generate a batch to preview three Telugu Reels.' : 'Add a product with its photos in Products, then generate a weekly batch.'));
    $('#cards').append(h);
  }
  $('#approve').disabled = !s.jobs.some(j => j.status === 'Draft');
  if (typeof window.refreshOverview === 'function') window.refreshOverview().catch(() => {});
  if (typeof window.refreshAgents === 'function') window.refreshAgents().catch(() => {});
  if (typeof window.refreshPublishingCalendar === 'function') window.refreshPublishingCalendar();
  if (typeof window.refreshSalesAnalytics === 'function') window.refreshSalesAnalytics().catch(() => {});
}
async function post(path, data) {
  if (busy) return; busy = true; document.body.setAttribute('aria-busy', 'true'); $('#mode').disabled = true; $('#generate').disabled = true;
  notice(path === '/remake' ? 'Remaking your Reel with music…' : path === '/generate' ? 'Preparing videos…' : 'Updating queue…');
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...data, mode: mode() }) }), j = await r.json();
    if (!r.ok) throw Error(j.error);
    await refresh();
    if (path === '/generate') openTab('queue');
    notice(j.message);
  } catch (e) { notice(e.message, true); }
  finally { busy = false; $('#mode').disabled = false; $('#generate').disabled = false; document.body.removeAttribute('aria-busy'); }
}
function change(action, ids) { post('/decide', { action, ids, start: $('#start').value }); }
$('#approve').onclick = () => change('approve', state.jobs.filter(j => j.status === 'Draft').reverse().map(j => j.id));
$('#generate').onclick = () => post('/generate', {});
$('#mode').onchange = () => { state = null; $('#products').replaceChildren(); $('#count').textContent = ''; notice(''); refresh().catch(e => notice(e.message, true)); };
$('#refresh').onclick = () => { notice(''); refresh().catch(e => notice(e.message, true)); };

function syncLegacyHeading(section, routeTitle) {
  activeTab = section;
  const demo = mode() === 'demo';
  const info = {
    overview: ['Business Overview', ''],
    catalogue: ['Product catalogue', demo ? 'Sample products for trying the Reel workflow.' : 'Manage your store links, photos, prices and stock.'],
    queue: [routeTitle || 'Reel queue', 'Review and schedule your Telugu product Reels.'],
    report: [routeTitle || 'Sales report', 'Delivered orders, returns and operating profit for this workspace.'],
    agents: [routeTitle || 'AI Agent Center', 'Manually orchestrate local Ollama agents, video rendering, QA and social approval packages.'],
  };
  const pair = info[section] || info.catalogue;
  if ($('#page-title')) $('#page-title').textContent = pair[0];
  if ($('#sub')) $('#sub').textContent = pair[1];
}
window.syncLegacyHeading = syncLegacyHeading;

function updateHeading() {
  syncLegacyHeading(activeTab);
}
function openTab(tab) {
  const map = { catalogue: 'catalogue', queue: 'calendar', report: 'analytics', agents: 'agents', overview: 'overview' };
  if (window.StudioUI) window.StudioUI.navigate(map[tab] || tab);
}

if (window.StudioUI) window.StudioUI.initShell();
