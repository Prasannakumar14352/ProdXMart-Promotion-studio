const form = $('#product-form');
const dialog = $('#editor');
let editing = null;
let selection = [];
let saving = false;
let formDirty = false;
let catView = localStorage.getItem('prodx_cat_view') === 'grid' ? 'grid' : 'table';
let catPage = 1;
let catPageSize = 12;
let catKpiPreset = '';
const catSelected = new Set();

function productCostValue(p) {
  return Number.isFinite(p.cost) ? p.cost : Math.max(0, (p.price || 0) - (p.margin || 0) - (p.fees || 0));
}

function stockBadgeMeta(stock) {
  const s = Number(stock || 0);
  if (s <= 0) return { cls: 'badge error', label: 'Out of stock (0)' };
  if (s <= 5) return { cls: 'badge warning', label: `Low (${s})` };
  return { cls: 'badge success', label: `${s} in stock` };
}

function renderCatKpis(allProducts) {
  const host = $('#cat-kpis');
  if (!host) return;
  const total = allProducts.length;
  const ready = allProducts.filter(p => p.issues.length === 0).length;
  const out = allProducts.filter(p => Number(p.stock || 0) <= 0).length;
  const missingPhotos = allProducts.filter(p => !p.has_photo || Number(p.photo_count || 0) === 0).length;
  const attention = allProducts.filter(p => p.issues.length > 0).length;
  const cards = [
    { key: '', label: 'Total products', value: total, sub: 'In active workspace' },
    { key: 'ready', label: 'Promotion-ready', value: ready, sub: total ? Math.round((ready / total) * 100) + '% eligible' : '0% eligible' },
    { key: 'out', label: 'Out of stock', value: out, sub: 'Zero available units' },
    { key: 'no_photo', label: 'Missing images', value: missingPhotos, sub: 'Needs gallery photos' },
    { key: 'attention', label: 'Needs attention', value: attention, sub: 'Blocked from reels' },
  ];
  host.replaceChildren();
  for (const c of cards) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'cat-kpi-btn' + (catKpiPreset === c.key ? ' active' : '');
    const sm = element('small', c.label);
    const val = element('b', String(c.value));
    const sub = element('span', c.sub);
    b.append(sm, val, sub);
    b.onclick = () => {
      catKpiPreset = catKpiPreset === c.key ? '' : c.key;
      if (catKpiPreset === 'ready') $('#filter').value = 'ready';
      else if (catKpiPreset === 'attention') $('#filter').value = 'hold';
      else $('#filter').value = '';
      if (catKpiPreset === 'out') $('#cat-stock').value = 'out';
      else if ($('#cat-stock')) $('#cat-stock').value = '';
      catPage = 1;
      renderProducts();
    };
    host.append(b);
  }
}

function syncCategoryFilter(allProducts) {
  const sel = $('#cat-category');
  if (!sel) return;
  const prev = sel.value;
  const cats = [...new Set(allProducts.map(p => p.category).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  sel.replaceChildren(new Option('All categories', ''));
  for (const c of cats) sel.add(new Option(c, c));
  if (cats.includes(prev)) sel.value = prev;
}

function updateBulkBar(visibleSkus) {
  const bar = $('#cat-bulk-bar');
  if (!bar) return;
  const validSkus = new Set((state?.products || []).map(p => p.sku));
  for (const s of [...catSelected]) if (!validSkus.has(s)) catSelected.delete(s);
  bar.hidden = catSelected.size === 0;
  if ($('#cat-bulk-count')) $('#cat-bulk-count').textContent = catSelected.size + ' selected';
  if ($('#cat-bulk-select-page')) {
    $('#cat-bulk-select-page').onclick = () => {
      visibleSkus.forEach(s => catSelected.add(s));
      renderProducts();
    };
  }
}

function promoteProductSkus(skus) {
  const clean = (skus || []).filter(Boolean);
  if (!clean.length) return;
  window.__preselectCampaignSkus = clean;
  const sel = $('#mkt-skus');
  if (sel) {
    Array.from(sel.options).forEach(opt => {
      opt.selected = clean.includes(opt.value);
    });
    if ($('#mkt-campaign-type')) {
      $('#mkt-campaign-type').value = clean.length > 1 ? 'multi_product' : 'single_product';
    }
  }
  if (window.StudioUI?.navigate) window.StudioUI.navigate('campaigns');
  else if (typeof openTab === 'function') openTab('agents');
  notice(clean.length + ' product(s) pre-selected for Campaign creation.');
}

function renderProducts() {
  if (!state) return;
  const allProducts = state.products || [];
  renderCatKpis(allProducts);
  syncCategoryFilter(allProducts);

  const query = ($('#search')?.value || '').toLowerCase().trim();
  const filter = $('#filter')?.value || '';
  const category = $('#cat-category')?.value || '';
  const stockFilter = $('#cat-stock')?.value || '';
  const marginFilter = $('#cat-margin')?.value || '';
  const sortBy = $('#cat-sort')?.value || 'name_asc';

  let products = allProducts.filter(p => {
    const ready = p.issues.length === 0;
    const matches = !query || [p.name, p.sku, p.category].some(v => (v || '').toLowerCase().includes(query));
    if (!matches) return false;
    if (filter && (filter === 'ready') !== ready) return false;
    if (category && p.category !== category) return false;
    const s = Number(p.stock || 0);
    if (stockFilter === 'in' && s <= 0) return false;
    if (stockFilter === 'low' && (s < 1 || s > 5)) return false;
    if (stockFilter === 'out' && s > 0) return false;
    if (marginFilter === 'positive' && Number(p.margin || 0) <= 0) return false;
    if (marginFilter === 'nonpositive' && Number(p.margin || 0) > 0) return false;
    if (catKpiPreset === 'no_photo' && (p.has_photo && Number(p.photo_count || 0) > 0)) return false;
    return true;
  });

  products = [...products].sort((a, b) => {
    switch (sortBy) {
      case 'margin_desc': return (b.margin || 0) - (a.margin || 0);
      case 'margin_asc': return (a.margin || 0) - (b.margin || 0);
      case 'price_desc': return (b.price || 0) - (a.price || 0);
      case 'price_asc': return (a.price || 0) - (b.price || 0);
      case 'stock_asc': return (a.stock || 0) - (b.stock || 0);
      default: return String(a.name || '').localeCompare(String(b.name || ''));
    }
  });

  const totalMatches = products.length;
  const totalPages = Math.max(1, Math.ceil(totalMatches / catPageSize));
  if (catPage > totalPages) catPage = totalPages;
  const startIdx = (catPage - 1) * catPageSize;
  const paged = products.slice(startIdx, startIdx + catPageSize);

  $('#count').textContent = totalMatches + (totalMatches === 1 ? ' product' : ' products');
  $('#cat-view-table')?.classList.toggle('active', catView === 'table');
  $('#cat-view-grid')?.classList.toggle('active', catView === 'grid');
  $('#cat-view-table')?.setAttribute('aria-pressed', catView === 'table' ? 'true' : 'false');
  $('#cat-view-grid')?.setAttribute('aria-pressed', catView === 'grid' ? 'true' : 'false');

  updateBulkBar(paged.map(p => p.sku));

  const pagEl = $('#cat-pagination');
  if (!products.length) {
    if (pagEl) pagEl.hidden = true;
    const empty = element('div', ''); empty.className = 'empty';
    const fresh = allProducts.length === 0;
    empty.append(
      element('h3', fresh ? 'Add your first product' : 'No matching products'),
      element('p', fresh ? 'Paste a ProdXMart link and add the details you want to use in a Reel.' : 'Try adjusting your search or resetting the catalogue filters.')
    );
    if (fresh && mode() === 'store') {
      const b = element('button', 'Add product'); b.className = 'primary'; b.onclick = () => openEditor(); empty.append(b);
    }
    $('#products').replaceChildren(empty);
    return;
  }

  if (catView === 'grid') {
    const grid = document.createElement('div');
    grid.className = 'cat-grid';
    for (const p of paged) {
      const card = document.createElement('article');
      card.className = 'cat-card';

      const media = document.createElement('div');
      media.className = 'cat-card-media';
      if (p.has_photo) {
        const img = new Image(); img.alt = p.name; img.loading = 'lazy';
        img.src = '/photo?' + new URLSearchParams({ mode: mode(), sku: p.sku });
        img.style.cursor = 'pointer';
        img.onclick = () => openLightbox(p.name, img.src);
        media.append(img);
      } else {
        const blank = element('span', p.name.slice(0, 1));
        blank.className = 'thumb-fallback';
        blank.style.width = '64px'; blank.style.height = '64px';
        media.append(blank);
      }
      const chkWrap = document.createElement('label');
      chkWrap.className = 'cat-card-check';
      const chk = document.createElement('input');
      chk.type = 'checkbox'; chk.checked = catSelected.has(p.sku);
      chk.setAttribute('aria-label', 'Select ' + p.name);
      chk.onchange = () => {
        if (chk.checked) catSelected.add(p.sku); else catSelected.delete(p.sku);
        updateBulkBar(paged.map(x => x.sku));
      };
      chkWrap.append(chk);
      media.append(chkWrap);

      const body = document.createElement('div');
      body.className = 'cat-card-body';
      const topRow = document.createElement('div');
      topRow.className = 'meta';
      const catBadge = element('span', p.category || 'General'); catBadge.className = 'badge';
      const readyTag = element('span', p.issues.length ? 'Needs attention' : 'Ready to promote');
      readyTag.className = 'tag' + (p.issues.length ? ' hold' : '');
      topRow.append(catBadge, readyTag);

      const title = element('strong', p.name);
      const sub = element('small', `${p.sku} · ${p.photo_count} photo${p.photo_count === 1 ? '' : 's'}`);
      sub.className = 'hint';

      const metrics = document.createElement('div');
      metrics.className = 'cat-card-metrics';
      const costVal = productCostValue(p);
      for (const [lbl, val, cls] of [
        ['Selling', money(p.price), ''],
        ['Cost', money(costVal), ''],
        ['Margin', money(p.margin), p.margin <= 0 ? 'margin-negative' : 'margin-positive'],
      ]) {
        const cell = document.createElement('div');
        cell.append(element('small', lbl));
        const b = element('b', val);
        if (cls) b.className = cls;
        cell.append(b);
        metrics.append(cell);
      }

      const st = stockBadgeMeta(p.stock);
      const stRow = document.createElement('div');
      stRow.className = 'meta';
      const stPill = element('span', st.label); stPill.className = st.cls;
      stRow.append(stPill);
      if (p.issues.length) {
        const iss = element('small', p.issues.join(' · '));
        iss.className = 'hint';
        stRow.append(iss);
      }

      const actions = document.createElement('div');
      actions.className = 'cardactions';
      if (mode() === 'store') {
        const eb = element('button', 'Edit'); eb.setAttribute('aria-label', 'Edit ' + p.name); eb.onclick = () => openEditor(p.sku);
        actions.append(eb);
      }
      if (!p.issues.length) {
        const pb = element('button', 'Promote'); pb.className = 'primary'; pb.onclick = () => promoteProductSkus([p.sku]);
        actions.append(pb);
      }
      if (p.product_url) {
        const lnk = element('a', 'View URL'); lnk.href = p.product_url; lnk.target = '_blank'; lnk.rel = 'noopener noreferrer';
        actions.append(lnk);
      }
      body.append(topRow, title, sub, metrics, stRow, actions);
      card.append(media, body);
      grid.append(card);
    }
    $('#products').replaceChildren(grid);
  } else {
    const t = document.createElement('table');
    t.className = 'data-table';
    const h = t.createTHead().insertRow(), body = t.createTBody();
    const selTh = document.createElement('th');
    selTh.style.width = '32px';
    const selAll = document.createElement('input');
    selAll.type = 'checkbox';
    selAll.setAttribute('aria-label', 'Select all on page');
    selAll.checked = paged.length > 0 && paged.every(p => catSelected.has(p.sku));
    selAll.onchange = () => {
      paged.forEach(p => { if (selAll.checked) catSelected.add(p.sku); else catSelected.delete(p.sku); });
      renderProducts();
    };
    selTh.append(selAll);
    h.append(selTh);
    ['Product', 'Selling price', 'Cost', 'Unit margin', 'Stock', 'Readiness', 'Actions'].forEach(n => h.append(element('th', n)));
    for (const p of paged) {
      const tr = body.insertRow();
      const cSel = tr.insertCell();
      const chk = document.createElement('input');
      chk.type = 'checkbox'; chk.checked = catSelected.has(p.sku);
      chk.setAttribute('aria-label', 'Select ' + p.name);
      chk.onchange = () => {
        if (chk.checked) catSelected.add(p.sku); else catSelected.delete(p.sku);
        updateBulkBar(paged.map(x => x.sku));
      };
      cSel.append(chk);

      const idCell = tr.insertCell(), id = element('div', ''); id.className = 'product-id';
      if (p.has_photo) {
        const image = new Image(); image.alt = ''; image.loading = 'lazy';
        image.src = '/photo?' + new URLSearchParams({ mode: mode(), sku: p.sku });
        image.style.cursor = 'pointer';
        image.onclick = () => openLightbox(p.name, image.src);
        id.append(image);
      } else {
        const blank = element('span', p.name.slice(0, 1)); blank.className = 'thumb-fallback'; id.append(blank);
      }
      const name = element('div', '');
      name.append(element('strong', p.name), element('small', p.sku + ' · ' + p.category + ' · ' + p.photo_count + ' photo' + (p.photo_count === 1 ? '' : 's')));
      id.append(name); idCell.append(id);

      const priceCell = tr.insertCell(); priceCell.textContent = money(p.price);
      const costCell = tr.insertCell(); costCell.textContent = money(productCostValue(p));
      const marginCell = tr.insertCell();
      const pct = p.price > 0 ? Math.round((p.margin / p.price) * 100) : 0;
      const mSpan = element('span', `${money(p.margin)} (${pct}%)`);
      mSpan.className = p.margin <= 0 ? 'margin-negative' : 'margin-positive';
      marginCell.append(mSpan);

      const stockCell = tr.insertCell();
      const st = stockBadgeMeta(p.stock);
      const stPill = element('span', st.label); stPill.className = st.cls;
      stockCell.append(stPill);

      const readiness = tr.insertCell(), tag = element('span', p.issues.length ? 'Needs attention' : 'Ready to promote');
      tag.className = 'tag' + (p.issues.length ? ' hold' : ''); readiness.append(tag);
      if (p.issues.length) { const note = element('small', p.issues.join(' · ')); note.className = 'why'; readiness.append(note); }

      const actions = tr.insertCell();
      const actWrap = document.createElement('div');
      actWrap.className = 'agent-table-actions';
      if (mode() === 'store') {
        const b = element('button', 'Edit'); b.setAttribute('aria-label', 'Edit ' + p.name); b.onclick = () => openEditor(p.sku);
        actWrap.append(b);
      }
      if (!p.issues.length) {
        const pb = element('button', 'Promote'); pb.onclick = () => promoteProductSkus([p.sku]);
        actWrap.append(pb);
      }
      if (p.product_url) {
        const lnk = element('a', 'Store'); lnk.className = 'btn btn-sm'; lnk.href = p.product_url; lnk.target = '_blank'; lnk.rel = 'noopener noreferrer';
        actWrap.append(lnk);
      }
      actions.append(actWrap);
    }
    const scroller = document.createElement('div');
    scroller.className = 'table-scroll';
    scroller.append(t);
    $('#products').replaceChildren(scroller);
  }

  if (pagEl) {
    pagEl.hidden = false;
    pagEl.replaceChildren();
    const info = element('span', `Showing ${startIdx + 1}–${Math.min(startIdx + paged.length, totalMatches)} of ${totalMatches} products`);
    info.className = 'hint';

    const ctrls = document.createElement('div');
    ctrls.className = 'agent-table-actions';
    const sizeSel = document.createElement('select');
    sizeSel.setAttribute('aria-label', 'Rows per page');
    for (const sz of [12, 24, 50]) sizeSel.add(new Option(sz + ' / page', String(sz)));
    sizeSel.value = String(catPageSize);
    sizeSel.onchange = () => { catPageSize = Number(sizeSel.value); catPage = 1; renderProducts(); };

    const prev = element('button', '← Prev'); prev.type = 'button'; prev.disabled = catPage <= 1;
    prev.onclick = () => { if (catPage > 1) { catPage--; renderProducts(); } };
    const pageLbl = element('span', `Page ${catPage} of ${totalPages}`); pageLbl.className = 'badge';
    const next = element('button', 'Next →'); next.type = 'button'; next.disabled = catPage >= totalPages;
    next.onclick = () => { if (catPage < totalPages) { catPage++; renderProducts(); } };
    ctrls.append(sizeSel, prev, pageLbl, next);
    pagEl.append(info, ctrls);
  }
}

['#search', '#filter', '#cat-category', '#cat-stock', '#cat-margin', '#cat-sort'].forEach(sel => {
  const el = $(sel);
  if (!el) return;
  const handler = () => { catPage = 1; renderProducts(); };
  if (sel === '#search') el.oninput = handler;
  else el.onchange = handler;
});
if ($('#cat-clear')) {
  $('#cat-clear').onclick = () => {
    catKpiPreset = '';
    catPage = 1;
    $('#search').value = '';
    $('#filter').value = '';
    if ($('#cat-category')) $('#cat-category').value = '';
    if ($('#cat-stock')) $('#cat-stock').value = '';
    if ($('#cat-margin')) $('#cat-margin').value = '';
    if ($('#cat-sort')) $('#cat-sort').value = 'name_asc';
    renderProducts();
  };
}
if ($('#cat-view-table')) {
  $('#cat-view-table').onclick = () => { catView = 'table'; localStorage.setItem('prodx_cat_view', 'table'); renderProducts(); };
}
if ($('#cat-view-grid')) {
  $('#cat-view-grid').onclick = () => { catView = 'grid'; localStorage.setItem('prodx_cat_view', 'grid'); renderProducts(); };
}
if ($('#cat-bulk-clear')) {
  $('#cat-bulk-clear').onclick = () => { catSelected.clear(); renderProducts(); };
}
if ($('#cat-bulk-promote')) {
  $('#cat-bulk-promote').onclick = () => promoteProductSkus([...catSelected]);
}

function openLightbox(title, src) {
  const lb = $('#photo-lightbox');
  if (!lb) return;
  $('#photo-lightbox-title').textContent = title || 'Photo preview';
  $('#photo-lightbox-img').src = src;
  lb.showModal();
}
if ($('#photo-lightbox-close')) $('#photo-lightbox-close').onclick = () => $('#photo-lightbox')?.close();

function margin() {
  const val = k => form.elements[k].value;
  const mEl = $('#margin');
  if (!val('price') || !val('cost') || !val('fees')) {
    mEl.textContent = '';
    mEl.className = 'margin';
    return;
  }
  const price = Number(val('price'));
  const uMargin = price - Number(val('cost')) - Number(val('fees'));
  const pct = price > 0 ? ((uMargin / price) * 100).toFixed(1) : '0.0';
  mEl.textContent = `Estimated unit margin: ${money(uMargin)} (${pct}% of selling price)`;
  mEl.className = 'margin' + (uMargin <= 0 ? ' is-negative' : '');
}
['price', 'cost', 'fees'].forEach(k => form.elements[k].oninput = margin);
function showPhotos() {
  const box = $('#photo-list'); box.replaceChildren();
  selection.forEach((item, i) => {
    const tile = element('div', ''); tile.className = 'photo' + (i === 0 ? ' is-cover' : '');
    if (i === 0) {
      const badge = element('span', 'Cover #1'); badge.className = 'photo-cover-badge';
      tile.append(badge);
    }
    const img = new Image(); img.src = item.preview; img.alt = 'Product photo ' + (i + 1);
    img.onclick = () => openLightbox('Photo #' + (i + 1), item.preview);

    const actions = document.createElement('div');
    actions.className = 'photo-actions';
    if (i > 0) {
      const cov = element('button', 'Cover'); cov.type = 'button'; cov.title = 'Set as cover photo';
      cov.onclick = () => { const [picked] = selection.splice(i, 1); selection.unshift(picked); showPhotos(); };
      actions.append(cov);

      const left = element('button', '←'); left.type = 'button'; left.title = 'Move left';
      left.onclick = () => { [selection[i - 1], selection[i]] = [selection[i], selection[i - 1]]; showPhotos(); };
      actions.append(left);
    }
    if (i < selection.length - 1) {
      const right = element('button', '→'); right.type = 'button'; right.title = 'Move right';
      right.onclick = () => { [selection[i + 1], selection[i]] = [selection[i], selection[i + 1]]; showPhotos(); };
      actions.append(right);
    }
    const b = element('button', 'Remove'); b.type = 'button'; b.setAttribute('aria-label', 'Remove photo ' + (i + 1));
    b.onclick = () => { if (item.file) URL.revokeObjectURL(item.preview); selection.splice(i, 1); showPhotos(); };
    actions.append(b);
    tile.append(img, actions); box.append(tile);
  });
}
async function openEditor(sku, imported){
  if(mode()!=='store'||saving)return;
  try{
    let p=null;
    if(sku){const r=await fetch('/product?'+new URLSearchParams({sku}));p=await r.json();if(!r.ok)throw Error(p.error);}
    if(mode()!=='store')return;
    editing=p;form.reset();$('#photo-status').textContent='';$('#form-error').textContent='';$('#form-title').textContent=p?'Edit product':'Add product';
    $('#categories').replaceChildren(...[...new Set((state?.products||[]).map(x=>x.category))].map(c=>element('option',c)));
    if(p)for(const k of ['product_url','sku','name','category','price','cost','fees','stock','feature_te'])form.elements[k].value=p[k];
    form.elements.sku.readOnly=!!p;form.elements.media_ok.checked=p?.media_ok?.toLowerCase()==='yes';
    selection=(p?.photos||[]).map((name,index)=>({name,preview:'/photo?'+new URLSearchParams({mode:'store',sku,index})}));
    if(imported){
      form.elements.price.value=imported.price;
      form.elements.cost.value=imported.cost;
      if(imported.name && (!p || imported.fullName || !/…|\.\.\./.test(imported.name)))form.elements.name.value=imported.name;
      if(!p)form.elements.sku.value=imported.sku || '';
      if(imported.product_url)form.elements.product_url.value=imported.product_url;
      if(Number.isSafeInteger(imported.stock) && imported.stock>=0)form.elements.stock.value=imported.stock;
      const result=await addGalleryPhotos(imported.images || [], ()=>mode()==='store' && importedCard===imported);
      if(mode()!=='store' || importedCard!==imported)return;
      if(result.added)form.elements.media_ok.checked=false;
      if(result.error)$('#form-error').textContent=result.error;
    }
    showPhotos();margin();formDirty=Boolean(imported);dialog.showModal();dialog.querySelector('.form-scroll').scrollTop=0;
    (imported && p ? form.elements.price : form.elements.product_url).focus();
  }catch(e){notice(e.message,true);}
}
async function downloadProductPhoto(url){
  const parsed=new URL(url);
  if(['cdn.factori.com','prodxmart.com','www.prodxmart.com'].includes(parsed.hostname)){
    try{
      const data=await readerRequest('store-photo',url);
      const bytes=Uint8Array.from(atob(data.base64),char=>char.charCodeAt(0));
      return new File([bytes],'Factori product photo',{type:data.mime});
    }catch{
      // The existing Studio downloader also supports public HTTPS redirects.
    }
  }
  const response=await fetch('/product-photo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode:'store',url})});
  if(!response.ok)throw Error((await response.json()).error || 'Photo download failed.');
  return new File([await response.blob()],'Product photo.jpg',{type:'image/jpeg'});
}
async function readListing(url, sku, name){
  const [browser, server]=await Promise.allSettled([
    readerRequest('store-details',url),
    fetch('/store-photos',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:'store',url,sku,name})}).then(async response=>{
      const result=await response.json();
      if(!response.ok)throw Error(result.error || 'Could not read the product gallery.');
      return result;
    })
  ]);
  const page=browser.value, record=server.value;
  const verifiedSku=record?.identifiers?.some(id=>id.toUpperCase()===sku.toUpperCase());
  const knownStoreSku=/^(?:SMP|SKUFACTORI)\d+$/i.test(sku);
  if(!verifiedSku && ((sku && page?.sku && page.sku.toUpperCase()!==sku.toUpperCase()) ||
      (record?.identifiers?.length && knownStoreSku)))
    throw Error('The page SKU differs from this product. Check the saved product link.');
  if(!page && !record?.images?.length)throw (server.reason || browser.reason || Error('No product gallery found.'));
  const images=record?.identifiers?.length ? [...record.images,...(page?.images || [])] :
    [...(page?.images || []),...(record?.images || [])];
  return {...page, sku:sku || page?.sku || '', product_url:page?.product_url || url, images:[...new Set(images)].slice(0,6)};
}

async function photoSignature(url){
  const img=new Image();
  img.src=url;
  await img.decode();
  const canvas=document.createElement('canvas');
  canvas.width=canvas.height=32;
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='white';ctx.fillRect(0,0,32,32);
  ctx.drawImage(img,0,0,32,32);
  return {ratio:img.naturalWidth/img.naturalHeight, pixels:ctx.getImageData(0,0,32,32).data};
}

function samePhoto(a,b){
  if(!a || !b || Math.abs(a.ratio/b.ratio-1)>.012)return false;
  let total=0;
  for(let i=0;i<a.pixels.length;i++){
    const delta=Math.abs(a.pixels[i]-b.pixels[i]);
    if(delta>35)return false;
    total+=delta;
  }
  return total/a.pixels.length<2;
}

async function addGalleryPhotos(images, current=()=>true){
  const urls=[...new Set(images)].filter(url=>!selection.some(item=>item.url===url)).slice(0,6);
  if(selection.length>=6 || !urls.length)return {added:0};
  // Old catalogue entries retain local files, without the original gallery URLs.
  const known=await Promise.all(selection.filter(item=>!item.url).map(item=>photoSignature(item.preview).catch(()=>null)));
  const downloads=await Promise.allSettled(urls.map(downloadProductPhoto));
  let added=0;
  for(const [i,result] of downloads.entries()){
    if(!current() || selection.length>=6)break;
    if(result.status!=='fulfilled')continue;
    const preview=URL.createObjectURL(result.value);
    const signature=known.length ? await photoSignature(preview).catch(()=>null) : null;
    const match=known.findIndex(old=>samePhoto(old,signature));
    if(!current() || match>=0){
      if(match>=0)known.splice(match,1);
      URL.revokeObjectURL(preview);continue;
    }
    selection.push({url:urls[i],file:result.value,preview});
    added++;
  }
  const failed=downloads.filter(r=>r.status==='rejected');
  return {added,error:failed.length ? failed.length+' photo(s) could not download. '+failed[0].reason.message : ''};
}

$('#fetch-photos').onclick=async()=>{
  if(saving)return;
  const button=$('#fetch-photos'), status=$('#photo-status');
  const link=form.elements.product_url.value.trim();
  if(!link){status.textContent='Enter the ProdXMart product page link first.';return;}
  if(selection.length>=6){status.textContent='Remove a photo to make space first.';return;}
  saving=true;button.disabled=true;$('#save').disabled=true;
  try{
    status.textContent='Reading the saved product page…';
    const sku=form.elements.sku.value.trim().toLowerCase();
    const product=await readListing(link,sku,form.elements.name.value);
    if(!product.images.length)throw Error('No gallery photos found. Check the product link or choose image files.');
    status.textContent='Downloading product photos…';
    const result=await addGalleryPhotos(product.images);
    if(result.added){form.elements.media_ok.checked=false;showPhotos();}
    status.textContent=product.images.length+' gallery photo(s) found. '+result.added+' added. '+
      (result.error || (result.added?'Review and save the product.':'Matching photos are already selected.'));
  }catch(error){status.textContent=error.message;}
  finally{saving=false;button.disabled=false;$('#save').disabled=false;}
};
$('#add').onclick=()=>openEditor();
form.addEventListener('input', () => { formDirty = true; });
form.addEventListener('change', () => { formDirty = true; });
async function closeForm(){
  if(saving)return;
  if(formDirty && window.StudioUI?.confirmAction){
    const ok = await window.StudioUI.confirmAction('Discard unsaved changes?', 'You have edited fields or photos in this product form.', 'Discard changes');
    if(!ok)return;
  }
  formDirty = false;
  dialog.close();
}
$('#cancel').onclick=closeForm;$('#close').onclick=closeForm;
dialog.addEventListener('cancel',e=>{
  if(saving){e.preventDefault();return;}
  if(formDirty){e.preventDefault();closeForm();}
});
dialog.addEventListener('close',()=>{formDirty=false;selection.filter(x=>x.file).forEach(x=>URL.revokeObjectURL(x.preview));selection=[];});
function addPhotos(files){
  if(saving)return;
  $('#form-error').textContent='';
  if(selection.length+files.length>6 || files.some(f=>f.size>5*1024*1024)){
    $('#form-error').textContent='Select up to six photos, no larger than 5 MB each.';return;
  }
  for(const file of files)selection.push({file,preview:URL.createObjectURL(file)});
  showPhotos();
}
$('#uploads').onchange=e=>{
  addPhotos([...e.target.files]);
  e.target.value='';
};
dialog.addEventListener('paste',event=>{
  const photos=Array.from(event.clipboardData?.files||[]).filter(file=>file.type.startsWith('image/'));
  if(!photos.length)return;
  event.preventDefault();
  addPhotos(photos);
});
function encoded(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onerror=()=>reject(Error('Could not read '+file.name));
    reader.onload=()=>resolve({base64:reader.result.split(',')[1]});reader.readAsDataURL(file);
  });
}
form.onsubmit=async e=>{
  e.preventDefault();if(saving)return;$('#form-error').textContent='';
  saving=true;$('#save').disabled=true;$('#save').textContent='Saving…';
  try{
    const links=$('#image-links').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    if(links.length+selection.length>6 && (links.length || selection.some(x=>x.file)))throw Error('Keep up to six photos in total.');
    const d=Object.fromEntries(new FormData(form));d.mode='store';d.version=editing?.version||'';d.media_ok=form.elements.media_ok.checked;
    d.keep=selection.filter(x=>!x.file).map(x=>x.name);
    d.new=[...await Promise.all(selection.filter(x=>x.file).map(x=>encoded(x.file))),...links.map(url=>({url}))];
    let nextNewIdx = 0;
    d.photo_order=selection.map(x=>x.file ? 'new:' + (nextNewIdx++) : 'keep:' + x.name);
    for (let li = 0; li < links.length; li++) d.photo_order.push('new:' + (nextNewIdx++));
    const r=await fetch('/product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    const j=await r.json();if(!r.ok)throw Error(j.error||'Could not save product.');
    dialog.close();importedCard=null;$('#factori-review').hidden=true;$('#search').value='';$('#filter').value='';if($('#cat-category'))$('#cat-category').value='';if($('#cat-stock'))$('#cat-stock').value='';if($('#cat-margin'))$('#cat-margin').value='';catKpiPreset='';openTab('catalogue');
    await refresh();notice(j.message);
  }catch(err){if(dialog.open)$('#form-error').textContent=err.message;else notice(err.message,true);}
  finally{saving=false;$('#save').disabled=false;$('#save').textContent='Save product';}
};
let importedCard = null;
function readerRequest(type,url){
  const readerId=document.documentElement.dataset.factoriReader;
  if(!readerId || readerId==='1')return Promise.reject(Error('Reload extension 1.4.0 or later and refresh Studio.'));
  return new Promise((resolve,reject)=>{
    const id=crypto.randomUUID();
    function finish(){
      clearTimeout(timer);
      window.removeEventListener('message',receive);
    }
    function receive(event){
      if(event.source!==window || event.origin!==location.origin || event.data?.studioResponse!==id)return;
      finish();
      if(event.data.value)resolve(event.data.value);
      else reject(Error(event.data.error || 'Extension unavailable.'));
    }
    const timer=setTimeout(()=>{finish();reject(Error('The product reader timed out.'));},20000);
    window.addEventListener('message',receive);
    window.postMessage({readerId,id,type,url},location.origin);
  });
}

function readFactoriImport(){
  const prefix = '#factori=';
  if(!location.hash.startsWith(prefix))return;
  const payload = location.hash.slice(prefix.length);
  history.replaceState(null, '', location.pathname + location.search);
  const card = JSON.parse(decodeURIComponent(payload));
  if(!card || typeof card.name !== 'string' || !card.name.trim() ||
      !Number.isFinite(card.price) || card.price <= 0 || !Number.isFinite(card.cost) || card.cost < 0){
    throw Error('Incomplete Factori pricing. Reopen the product card and try again.');
  }
  card.images=Array.isArray(card.images)?card.images.filter(url=>typeof url==='string').slice(0,6):[];
  importedCard = card;
  $('#factori-url').value=card.product_url || '';
  displayImport(card);
  if($('#factori-url').value)loadStoreDetails();
  else $('#factori-status').textContent='Paste the product link from your store to read its name, SKU, available units and gallery.';
}
function displayImport(card){
  const target = $('#factori-target');
  target.replaceChildren(new Option('Choose a product', ''), new Option('Add as a new product', 'new'));
  const sameName = state.products.filter(product => product.name === card.name);
  const match = card.sku ? state.products.find(p => p.sku.toUpperCase() === card.sku) :
    (sameName.length === 1 ? sameName[0] : null);
  for(const product of state.products){
    if(card.sku && product.sku.toUpperCase() !== card.sku)continue;
    target.add(new Option(product.name + ' · ' + product.sku, 'sku:' + product.sku));
  }
  if(match){
    target.value='sku:' + match.sku;
    if(!$('#factori-url').value)$('#factori-url').value=match.product_url || '';
  }
  else if(!state.products.length || card.sku)target.value='new';
  $('#factori-values').textContent = card.name + ' · Customer pays ' + money(card.price) +
    ' · Factori price ' + money(card.cost) + (Number.isFinite(card.profit) ? ' · Factori profit per sale ' + money(card.profit) : '');
  $('#factori-values').textContent+=' · SKU '+(card.sku || 'unavailable')+' · Units '+(card.stock ?? 'unavailable')+' · Photos '+card.images.length;
  $('#factori-apply').disabled=!target.value;
  $('#factori-review').hidden=false;
}
function importBusy(busy){
  for(const id of ['factori-target','factori-url','factori-read','factori-dismiss'])$('#'+id).disabled=busy;
  $('#factori-apply').disabled=busy || !$('#factori-target').value;
}
async function loadStoreDetails(){
  const card=importedCard;
  const url=$('#factori-url').value.trim();
  if(!card || !url)return;
  importBusy(true);
  $('#factori-status').textContent='Reading the product page…';
  try{
    if(!document.documentElement.dataset.factoriReader)await new Promise(resolve=>setTimeout(resolve,450));
    const details=await readListing(url,card.sku || '',card.name);
    if(card!==importedCard)return;
    card.name=details.name || card.name;
    card.fullName=Boolean(details.name);
    card.sku=details.sku || card.sku;
    card.stock=details.stock ?? card.stock;
    card.images=[...new Set([...details.images,...card.images])].slice(0,6);
    card.product_url=details.product_url;
    displayImport(card);
    $('#factori-status').textContent='Review the imported details. Enter any unavailable fields in the editor.';
  }catch(error){$('#factori-status').textContent=error.message;}
  finally{importBusy(false);}
}
$('#factori-read').onclick=loadStoreDetails;
$('#factori-target').onchange=e=>{
  $('#factori-apply').disabled=!e.target.value;
  if(!importedCard?.product_url){
    const product=state.products.find(p=>'sku:'+p.sku===e.target.value);
    $('#factori-url').value=product?.product_url || '';
  }
};
$('#factori-apply').onclick=async()=>{
  const target=$('#factori-target').value;
  if(!importedCard || !target)return;
  importBusy(true);
  $('#factori-status').textContent='Preparing the product and its photos…';
  try{await openEditor(target==='new' ? null : target.slice(4),importedCard);}
  finally{importBusy(false);$('#factori-status').textContent='Review the editor and save the product.';}
};
$('#factori-dismiss').onclick=()=>{importedCard=null;$('#factori-review').hidden=true;};
refresh().then(readFactoriImport).catch(e=>notice(e.message,true));
