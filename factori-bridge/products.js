(() => {
  const studio = 'http://127.0.0.1:8765/';
  const page = () => location.pathname.replace(/\/$/, '') === '/dashboard/products';
  const amounts = /^\s*:?\s*([−-]?)\s*(?:₹|Rs\.?|INR)\s*([−-]?\d[\d,]*(?:\.\d{1,2})?)(?![\d.,])/i;
  const label = /^factori\s+price$/i;

  function currency(text, field) {
    const parts = text.split(new RegExp('\\b' + field + '\\b', 'i'));
    if (parts.length !== 2) return null;
    const found = parts[1].trimStart().match(amounts);
    if (!found) return null;
    return Number((found[1] + found[2]).replace('−', '-').replaceAll(',', ''));
  }

  function readCard(card) {
    const text = card.innerText;
    const price = currency(text, 'customer\\s+pays');
    const cost = currency(text, 'factori\\s+price');
    if (price === null || cost === null || price <= 0 || cost < 0) return null;
    const heading = card.querySelector('h1,h2,h3,h4,[role="heading"]');
    const anchors = Array.from(card.querySelectorAll('a[href]'));
    const productLink = anchors.find(a => a.innerText.trim().length > 5 && !/^(edit|view|details|import)/i.test(a.innerText.trim()));
    const image = card.querySelector('img');
    const beforeOrigin = text.split(/country of origin/i);
    const titleLine = beforeOrigin.length === 2 ? beforeOrigin[0].trim().split(/\n/).pop() : '';
    const name = (productLink?.title || heading?.innerText || productLink?.innerText || image?.alt || titleLine || '').trim();
    if (!name) return null;
    const urls = anchors.filter(a=>/^https?:/.test(a.href)).map(a => new URL(a.href));
    const shop = urls.find(u => u.protocol === 'https:' && ['prodxmart.com','www.prodxmart.com'].includes(u.hostname) && u.pathname !== '/');
    const sku = (text.match(/\bSKUFACTORI\d+\b/i) || urls.map(u=>u.pathname).join(' ').match(/\bSKUFACTORI\d+\b/i))?.[0]?.toUpperCase() || '';
    return {name, sku, product_url: shop?.href || '', price, cost, profit: currency(text, 'profit\\s+per\\s+sale')};
  }

  function findCard(start) {
    for (let card = start; card && card !== document.body; card = card.parentElement) {
      const text = card.innerText || '';
      if ((text.match(/\bfactori\s+price\b/gi) || []).length > 1) return null;
      if (/customer\s+pays/i.test(text) && readCard(card)) return card;
    }
    return null;
  }

  function scan() {
    if (!page()) return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const labels = [];
    while (walker.nextNode()) {
      if (label.test(walker.currentNode.textContent.trim())) labels.push(walker.currentNode.parentElement);
    }
    for (const node of labels) {
      const card = findCard(node);
      if (!card || card.querySelector('[data-prodx-import]')) continue;
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
  }

  let pending = false;
  const observer = new MutationObserver(() => {
    if (pending || !page()) return;
    pending = true;
    setTimeout(() => {pending = false; scan();}, 250);
  });
  observer.observe(document.body, {childList:true, characterData:true, subtree:true});
  scan();
})();
