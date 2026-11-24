function storeDetails() {
  const structured = [];
  function collect(value) {
    if (Array.isArray(value)) return value.forEach(collect);
    if (!value || typeof value !== 'object') return;
    if ([value['@type']].flat().includes('Product')) structured.push(value);
    if (value['@graph']) collect(value['@graph']);
    if (value.mainEntity) collect(value.mainEntity);
  }
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { collect(JSON.parse(script.textContent)); } catch {}
  }
  const main = document.querySelector('main') || document.body;
  const heading = main.querySelector('h1');
  const scope = heading?.closest('[itemtype$="/Product"],article') || main;
  const text = scope.innerText.split(/(?:related products|you may also like|similar products|recently viewed)/i)[0];
  const pageSku = factoriSku(text, location.pathname);
  const product = structured.find(item => item.sku && String(item.sku).toUpperCase() === pageSku.toUpperCase()) ||
    structured.find(item => item.name && item.name === heading?.textContent.trim()) ||
    (structured.length === 1 ? structured[0] : null);
  const name = (product?.name || heading?.textContent || document.querySelector('meta[property="og:title"]')?.content || '').trim();
  const sku = String(product?.sku || pageSku);
  const images = [];
  function add(value) {
    const url = productImageUrl(value, location.href);
    if (url && !images.includes(url) && images.length < 6) images.push(url);
  }
  for (const item of [product?.image].flat()) add(typeof item === 'string' ? item : item?.contentUrl || item?.url);
  add(document.querySelector('meta[property="og:image"]')?.content);
  const numericSku = /^(?:SKUFACTORI|SMP)(\d+)$/i.exec(sku)?.[1];
  for (const img of scope.querySelectorAll('img')) {
    const src = productImageSource(img, location.href);
    const inGallery = img.closest('[class*="gallery" i],[id*="gallery" i],[class*="thumbnail" i],[class*="product-image" i]');
    if (img.closest('[class*="related" i],[class*="recommend" i]')) continue;
    if (inGallery || (sku && src.toLowerCase().includes(sku.toLowerCase())) || (name && img.alt.trim().toLowerCase() === name.toLowerCase()) ||
        (numericSku && new RegExp('[-_/]' + numericSku + '[-_.]').test(src))) add(src);
  }
  let stock = factoriStock(text);
  const offers = [product?.offers].flat().filter(Boolean);
  if (stock === null && offers.length === 1) {
    const level = offers[0].inventoryLevel?.value;
    if (level != null && /^\d+$/.test(String(level))) stock = Number(level);
    else if (/\/OutOfStock$/.test(offers[0].availability || '')) stock = 0;
  }
  return {name, sku, stock, images, product_url: location.href};
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type === 'read-product-dom') reply(storeDetails());
});
