const byId = id => document.getElementById(id);
let cards = [];

async function loadProducts() {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const result = await chrome.tabs.sendMessage(tab.id, {type: 'list-factori-products'});
    cards = result.cards;
    if (!cards.length) {
      byId('status').textContent = result.onPage
        ? 'No cards detected. Copy one product’s text from Factori and use the form below.'
        : 'Open My Products and click the extension again.';
      byId('paste').open = true;
      return;
    }
    byId('product').replaceChildren(...cards.map((card, index) =>
      new Option([card.name, card.sku, '₹' + card.price.toLocaleString('en-IN')].filter(Boolean).join(' · '), String(index))));
    byId('status').textContent = 'Choose a product from the current page.';
    byId('listed').hidden = false;
  } catch {
    byId('status').textContent = 'Open or refresh My Products, then reopen this extension. You can also import copied card text below.';
    byId('paste').open = true;
  }
}

async function review(card) {
  const url = new URL('http://127.0.0.1:8765/');
  url.hash = 'factori=' + encodeURIComponent(JSON.stringify(card));
  try {
    await chrome.tabs.create({url: url.href});
    window.close();
  } catch {
    byId('error').textContent = 'Could not open Studio. Start Studio on port 8765 and try again.';
  }
}

byId('open').onclick = () => {
  const card = cards[Number(byId('product').value)];
  if (card) review(card);
};
byId('use').onclick = () => {
  const name = byId('name').value.trim();
  const text = byId('card').value;
  const prices = factoriPrices(text);
  if (!name || !prices) {
    byId('error').textContent = 'Enter the product name and paste exactly one card with both labelled rupee amounts.';
    return;
  }
  const sku = factoriSku(text);
  review({name, sku, stock:factoriStock(text), product_url: '', ...prices});
};
byId('browse').onclick = async () => {
  await chrome.tabs.create({url:chrome.runtime.getURL('catalogue.html')});
  window.close();
};
loadProducts();
