// Inspect the rendered catalogue controls so the retailer session and site routing stay in Chrome.
function factoriPager(expectedPage) {
  const selector = 'button,a[href],[role="button"]';
  const controls = Array.from(document.querySelectorAll(selector)).filter(el=>el.getClientRects().length);
  const label = el => (el.getAttribute('aria-label') || el.title || el.innerText || '').trim();
  const disabled = el => el.disabled || el.getAttribute('aria-disabled') === 'true' ||
    Boolean(el.closest('[inert],li.disabled,[data-disabled="true"]'));
  const pageNumber = el => {
    const match = label(el).match(/^(?:(?:go to )?page\s*)?(\d+)$/i);
    return match ? Number(match[1]) : null;
  };
  const info = document.body.innerText.match(/\bpage\s+(\d+)\s*(?:of|\/)\s*(\d+)\b/i) ||
    document.body.innerText.match(/(?:^|\n)\s*(\d+)\s*\/\s*(\d+)\s*(?:$|\n)/);
  const marked = controls.find(el=>el.matches('[aria-current="page"],[aria-current="true"]') && pageNumber(el));
  const current = info ? Number(info[1]) : marked ? pageNumber(marked) : null;
  const total = info ? Number(info[2]) : null;
  const nextNumber = (current || expectedPage) + 1;
  const numeric = controls.filter(el=>pageNumber(el) !== null);
  const nav = el => el.closest('[aria-label*="pagin" i],[class*="pagin" i],[data-slot*="pagin" i],nav');
  function inNumberGroup(el) {
    for (let box=el.parentElement;box && box !== document.body;box=box.parentElement) {
      if (box.querySelectorAll(selector).length > 60 || /factori\s+price/i.test(box.innerText)) break;
      if (numeric.some(n=>n!==el && box.contains(n))) return true;
    }
    return false;
  }
  const nextLabel = /^(?:(?:go to (?:the )?)?next(?:\s+page)?|(?:go to )?page\s+next)\s*[›»→>]*$/i;
  const next = controls.find(el=>el.rel==='next' || (nextLabel.test(label(el)) &&
    (nav(el) || inNumberGroup(el) || info || /page/i.test(label(el)) ||
      controls.some(other=>/^(?:prev|previous)(?: page)?$/i.test(label(other)) && other.parentElement===el.parentElement))));
  const numbered = numeric.find(el=>pageNumber(el)===nextNumber && !disabled(el) && (nav(el) || inNumberGroup(el)));
  const arrow = controls.find(el=>(/^[›»→>]$/.test(label(el)) ||
    (!label(el) && el.querySelector('[class*="chevron-right" i],[class*="arrow-right" i]'))) &&
    (nav(el) || inNumberGroup(el) || el.parentElement?.innerText.match(/page\s+\d+\s*(?:of|\/)/i)));
  const control = numbered || next || arrow;
  return {current, total, control, end: Boolean(total && current===total) ||
    Boolean(control && disabled(control) && (!total || current===total))};
}
