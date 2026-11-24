// Only relay requests originating from the local Studio window.
if (location.origin === 'http://127.0.0.1:8765' && location.pathname === '/') {
  document.documentElement.dataset.factoriReader = '1';
  window.addEventListener('message', async event => {
    const request = event.data;
    if (event.source !== window || event.origin !== location.origin || request?.studioRequest !== true ||
        !['store-details','store-photo'].includes(request.type)) return;
    let result;
    try { result = await chrome.runtime.sendMessage({type:request.type, url:request.url}); }
    catch (error) { result = {error:error.message}; }
    window.postMessage({studioResponse:request.id, ...result}, location.origin);
  });
}
