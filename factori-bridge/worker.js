importScripts('fields.js');
const shopHosts = ['prodxmart.com', 'www.prodxmart.com'];
function storeUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !shopHosts.includes(url.hostname) || url.port ||
      url.username || url.password || url.pathname === '/') throw Error('Enter the full ProdXMart product link.');
  return url;
}

async function readStore(value) {
  const url = storeUrl(value);
  const tab = await chrome.tabs.create({url: url.href, active: false});
  let best;
  try {
    for (let attempt = 0; attempt < 22; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 450));
      let row;
      try { row = await chrome.tabs.sendMessage(tab.id, {type: 'read-product-dom'}); } catch { continue; }
      if (!row || !row.name || !row.product_url) continue;
      const final = storeUrl(row.product_url);
      if (final.pathname.replace(/\/$/,'') !== url.pathname.replace(/\/$/,'')) continue;
      if ([...url.searchParams].some(([key,value])=>final.searchParams.get(key)!==value)) continue;
      if (!row.sku && row.stock === null && !row.images.length) continue;
      const stable = best && best.name === row.name && best.sku === row.sku && best.stock === row.stock &&
        best.images.join(' ') === row.images.join(' ');
      best = row;
      if (stable && row.sku && row.stock !== null && row.images.length) break;
    }
    if (!best) throw Error('Could not read product details. Check that the link opens a product and retry.');
    return best;
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function readPhoto(value) {
  const url = productImageUrl(value);
  if (!url || !['cdn.factori.com',...shopHosts].includes(new URL(url).hostname)) throw Error('Use the Studio image downloader for this host.');
  const response = await fetch(url, {credentials:'omit', redirect:'error', signal:AbortSignal.timeout(10000)});
  if (!response.ok) throw Error('Image host returned HTTP ' + response.status);
  const limit = 5 * 1024 * 1024;
  if (Number(response.headers.get('content-length')) > limit) throw Error('Photo exceeds 5 MB.');
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.length;
    if (size > limit) { await reader.cancel(); throw Error('Photo exceeds 5 MB.'); }
    for (let i=0;i<value.length;i+=8192) chunks.push(String.fromCharCode(...value.subarray(i,i+8192)));
  }
  const bytes = chunks.join('');
  let mime;
  if (bytes.startsWith('\xFF\xD8\xFF')) mime='image/jpeg';
  else if (bytes.startsWith('\x89PNG\r\n\x1a\n')) mime='image/png';
  else if (bytes.startsWith('RIFF') && bytes.slice(8,12)==='WEBP') mime='image/webp';
  else throw Error('Photo is not a supported JPG, PNG or WebP.');
  return {base64:btoa(bytes), mime};
}

let stagedCatalogBatch = null;

chrome.runtime.onMessage.addListener((message,sender,reply) => {
  if (message.type === 'stage-factori-catalog' && sender.id === chrome.runtime.id) {
    stagedCatalogBatch = message.batch || null;
    reply({ok: true, count: stagedCatalogBatch?.cards?.length || 0});
    return false;
  }
  if (sender.url !== 'http://127.0.0.1:8765/' && !sender.url?.startsWith('http://127.0.0.1:8765/#')) return;
  if (message.type === 'get-factori-catalog') {
    reply({value: stagedCatalogBatch});
    return false;
  }
  if (!['store-details','store-photo'].includes(message.type)) return;
  const task = message.type === 'store-details' ? readStore(message.url) : readPhoto(message.url);
  task.then(value=>reply({value}),error=>reply({error:error.message}));
  return true;
});
