// The catalogue tab owns the scan so closing the toolbar popup does not interrupt it.
async function scanCatalogue(state, {snapshot, advance, wait, progress}) {
  for (;;) {
    let ready;
    let previousSample = '';
    for (let attempt=0; attempt<45; attempt++) {
      const page = await snapshot(state.pages + 1);
      if (page?.cards.length) {
        const signature = JSON.stringify(page.cards.map(p=>[p.sku,p.product_url,p.name,p.price,p.cost]));
        const expected = state.pages + 1;
        if (signature === previousSample && !state.seenPages.has(signature) && (!page.current || page.current===expected)) {
          ready = {page, signature};
          break;
        }
        previousSample = signature;
      } else previousSample = '';
      await wait();
    }
    if (!ready) throw Error('Waiting for new products. In the Factori tab, check your sign-in and navigate to page ' + (state.pages+1) + ', then resume.');
    const {page,signature} = ready;
    state.seenPages.add(signature);
    state.pages++;
    state.total = page.total || state.total;
    for (const card of page.cards) {
      const normSku = (card.sku || '').trim().toUpperCase();
      const normVariant = (card.variant || '').trim().toLowerCase();
      const key = normSku ? 'sku:' + normSku + '::' + normVariant : card.product_url ? 'url:' + card.product_url : '';
      if (key && state.keys.has(key)) continue;
      if (key) state.keys.add(key);
      state.cards.push({...card, page: card.page || state.pages});
    }
    progress();
    if (page.end) return;
    if (!await advance(state.pages)) throw Error('Could not confirm the next page. In the Factori tab, open page ' + (state.pages+1) + ' if available, then resume.');
    await wait();
  }
}
