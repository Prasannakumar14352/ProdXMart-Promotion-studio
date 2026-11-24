(() => {
  const studio = 'http://127.0.0.1:8765/';
  const page = () => location.pathname.replace(/\/$/, '') === '/dashboard/products';
  function productName(card, anchors) {
    const heading = card.querySelector('h1,h2,h3,h4,[role="heading"]');
    const beforeOrigin = card.innerText.split(/country of origin/i);
    const lines = beforeOrigin.length === 2 ? beforeOrigin[0].split(/\n/).reverse() : [];
    const candidates = [
      heading?.title, heading?.innerText,
      ...anchors.map(link => link.title),
      card.querySelector('img')?.alt,
      ...anchors.map(link => link.innerText),
      ...lines
    ];
    const actions = /^(?:open on your store|view on (?:your )?store|visit store|shop now|open|view|edit(?: product)?|view details|import to prodxmart studio|live|product(?: image)?)$/i;
    for (const candidate of candidates) {
      const name = candidate?.replace(/\s+/g, ' ').trim();
      if (!name || !/[\p{L}]/u.test(name)) continue;
      if (actions.test(name.replace(/[^\p{L}\d]+$/u, '').trim())) continue;
      return name;
    }
    return '';
  }

  function readCard(card) {
    const text = card.innerText;
    const prices = factoriPrices(text);
    if (!prices) return null;
    const anchors = Array.from(card.querySelectorAll('a[href]'));
    const name = productName(card, anchors);
    if (!name) return null;
    const urls = anchors.filter(a=>/^https?:/.test(a.href)).map(a => new URL(a.href));
    const shop = urls.find(u => u.protocol === 'https:' && ['prodxmart.com','www.prodxmart.com'].includes(u.hostname) && u.pathname !== '/');
    const sku = factoriSku(text, urls.map(u=>u.pathname + ' ' + u.search).join(' '));
    const images = [...new Set(Array.from(card.querySelectorAll('img'), img =>
      productImageUrl(img.currentSrc || img.dataset?.src || img.src, location.href)).filter(Boolean))].slice(0,6);
    return {name, sku, stock:factoriStock(text), images, product_url: shop?.href || '', ...prices};
  }

  function findCard(start) {
    for (let card = start; card && card !== document.body; card = card.parentElement) {
      const text = card.innerText || '';
      if ((text.match(/\bfactori\s+price\b/gi) || []).length > 1) return null;
      if (!/factori\s+price/i.test(text)) continue;
      if (/customer\s+pays/i.test(text) && readCard(card)) {
        let parent = card.parentElement;
        while (parent && parent !== document.body && !card.querySelector('img')) {
          if ((parent.innerText.match(/factori\s+price/gi) || []).length !== 1) break;
          if (parent.matches('main,section,nav')) break;
          card = parent;
          parent = card.parentElement;
        }
        return card;
      }
    }
    return null;
  }

  function scan() {
    if (!page()) return [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const labels = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (/^\s*factori(?:\s+price\b|\s*$)/i.test(node.textContent)) labels.push(node.parentElement);
    }
    const found = new Set();
    for (const node of labels) {
      const card = findCard(node);
      if (!card || found.has(card)) continue;
      found.add(card);
      if (card.querySelector('[data-prodx-import]')) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.prodxImport = '';
      button.textContent = 'Import to ProdXMart Studio';
      button.style.cssText = 'margin:8px 0;padding:8px 12px;border:0;border-radius:6px;background:#145f53;color:white;cursor:pointer';
      button.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        const current = page() && readCard(card);
        if (current) window.open(studio + '#factori=' + encodeURIComponent(JSON.stringify(current)), '_blank', 'noopener,noreferrer');
      };
      card.append(button);
    }
    return Array.from(found, readCard).filter(Boolean);
  }

  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (message.type === 'list-factori-products') reply({onPage: page(), cards: scan()});
  });

  let pending = false;
  const observer = new MutationObserver(() => {
    if (pending || !page()) return;
    pending = true;
    setTimeout(() => {pending = false; scan();}, 250);
  });
  observer.observe(document.body, {childList:true, characterData:true, subtree:true});
  scan();
})();
