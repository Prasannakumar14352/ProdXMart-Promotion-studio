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
  return labelled?.[1] || (text + ' ' + pathname).match(/\b(?:SKUFACTORI|SMP)\d+\b/i)?.[0].toUpperCase() || '';
}

function factoriVariant(text) {
  const match = (text || '').match(/(?:^|\n)\s*(?:variant|colour|color|size)\s*[:：]\s*([^\n]{1,60})/i);
  return match ? match[1].replace(/\s+/g, ' ').trim() : '';
}

function productImageUrl(value, base) {
  if (!value) return '';
  try {
    const url = new URL(value, base);
    if (url.protocol === 'https:' && !url.username && !url.password && !url.port &&
        !/\b(?:logo|placeholder|avatar|favicon)\b/i.test(url.pathname)) return url.href;
  } catch {}
  return '';
}

function productImageSource(img, base) {
  const set = img.getAttribute?.('data-srcset') || img.getAttribute?.('srcset') || '';
  const candidates = [img.getAttribute?.('data-zoom-image'), img.getAttribute?.('data-src'),
    img.getAttribute?.('data-original'), img.currentSrc, ...set.split(',').reverse().map(s=>s.trim().split(/\s+/)[0]), img.src];
  return candidates.map(value=>productImageUrl(value,base)).find(Boolean) || '';
}
