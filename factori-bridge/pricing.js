// Shared by the catalogue content script and the toolbar's paste fallback.
function factoriPrices(text) {
  function amount(label) {
    const parts = text.split(new RegExp(label, 'i'));
    if (parts.length !== 2) return null;
    const match = parts[1].match(/^\s*:?\s*([−-]?)\s*(?:₹|Rs\.?|INR)\s*([−-]?\d[\d,]*(?:\.\d{1,2})?)(?![\d.,])/i);
    if (!match) return null;
    const value = Number((match[1] + match[2]).replace('−', '-').replaceAll(',', ''));
    return Number.isFinite(value) ? value : null;
  }
  const price = amount('customer\\s+pays');
  const cost = amount('factori\\s+price');
  if (price === null || cost === null || price <= 0 || cost < 0) return null;
  return {price, cost, profit: amount('profit\\s+per\\s+sale')};
}
