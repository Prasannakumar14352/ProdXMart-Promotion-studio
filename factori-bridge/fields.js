// Read explicit inventory counts, never the quantity picker or a sales price.
function factoriStock(text) {
  const number = '(\\d[\\d,]*)';
  const patterns = [
    new RegExp('(?:available (?:units|stock|quantity)|stock quantity|ready stock)\\s*[:·-]?\\s*' + number + '(?![\\d.,])', 'i'),
    new RegExp('(?:^|[^\\d.,₹])' + number + '\\s*(?:(?:units|pcs|pieces)\\s+)?(?:available|in stock)\\b', 'i')
  ];
  for (const pattern of patterns) {
    const counts = new Set(Array.from(text.matchAll(new RegExp(pattern.source, 'gi')), match=>Number(match[1].replaceAll(',', ''))));
    if (counts.size === 1) return counts.values().next().value;
    if (counts.size > 1) return null;
  }
  const stockLine = text.match(/(?:^|\n)\s*(?:stock|available units|available quantity)\s*[:：]\s*(\d[\d,]*)\s*(?:units|pcs|pieces)?\s*(?:$|\n)/i);
  if (stockLine) return Number(stockLine[1].replaceAll(',', ''));
  if (/(?:^|\n)\s*out of stock\s*(?:$|\n)/i.test(text)) return 0;
  return null;
}

function factoriSku(text, pathname = '') {
  const labelled = text.match(/\bSKU\s*[:#：]\s*([A-Z0-9][A-Z0-9._-]{0,119})\b/i);
  return labelled?.[1] || (text + ' ' + pathname).match(/\bSKUFACTORI\d+\b/i)?.[0].toUpperCase() || '';
}

function productImageUrl(value, base) {
  if (!value) return '';
  try {
    const url = new URL(value, base);
    if (url.protocol === 'https:' && !url.username && !url.password && !url.port &&
        ['cdn.factori.com','prodxmart.com','www.prodxmart.com'].includes(url.hostname) &&
        !/\b(?:logo|placeholder|avatar|favicon)\b/i.test(url.pathname)) return url.href;
  } catch {}
  return '';
}
