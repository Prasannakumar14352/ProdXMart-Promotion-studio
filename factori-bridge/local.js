// Only relay requests originating from the local Studio window.
if (location.origin === 'http://127.0.0.1:8765' && location.pathname === '/') {
  const bridgeId = crypto.randomUUID();
  document.documentElement.dataset.factoriReader = bridgeId;
  window.addEventListener('message', async event => {
    const request = event.data;
    if (event.source !== window || event.origin !== location.origin || request?.readerId !== bridgeId ||
        !['store-details','store-photo','get-factori-catalog'].includes(request.type)) return;
    let result;
    try {
      const runtime = globalThis.chrome?.runtime;
      if (!runtime?.id || typeof runtime.sendMessage !== 'function') throw Error('inactive');
      result = await runtime.sendMessage({type:request.type, url:request.url});
    } catch {
      result = {error:'The Chrome extension disconnected. Reload ProdXMart product importer, then refresh the Studio tab.'};
    }
    window.postMessage({studioResponse:request.id, ...result}, location.origin);
  });
}
