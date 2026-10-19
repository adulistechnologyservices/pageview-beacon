// Pageview beacon. Drop this at the end of the page, set SITE_ID and ENDPOINT.
//
// Sets and reads no cookie, sends no identifier, and nothing personal: the
// site id, the page, the referrer, the viewport width and a mobile flag.
// Analytics must never break the page, so the whole thing is wrapped in try.
(function () {
  var SITE_ID = 'your-site-slug';
  var ENDPOINT = 'https://YOUR-API-ID.execute-api.REGION.amazonaws.com/pageview';

  try {
    var payload = JSON.stringify({
      site_id: SITE_ID,
      path: location.pathname.split('/').pop() || 'index.html',
      referrer: document.referrer || '',
      screen_w: window.innerWidth || 0,
      is_mobile: window.matchMedia('(max-width: 720px)').matches
    });

    if (navigator.sendBeacon) {
      // sendBeacon survives the tab being closed mid-flight, which is why it is
      // the first choice. But the Blob type MUST be text/plain.
      //
      // application/json is not a CORS-simple content type. Cross-origin, it
      // forces a preflight (an OPTIONS request the browser has to make first),
      // and a beacon cannot perform a preflight. The browser queues it,
      // sendBeacon() returns true -- which only means "queued" -- and the
      // request is then dropped without a word. The server parses the body as
      // JSON regardless of the header, so nothing is lost by lying about it.
      navigator.sendBeacon(ENDPOINT, new Blob([payload], { type: 'text/plain' }));
    } else {
      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true
      }).catch(function () {});
    }
  } catch (e) { /* never let analytics take the page down */ }
})();
