/* Offline support. The site's own files: network first, so a new AutoScout.py
   shows up as soon as you're online, with the saved copy as the fallback. On a
   slow connection (venue Wi-Fi) the saved copy is used after a few seconds and
   the download carries on in the background to refresh it for next time.
   The Python runtime (a fixed, versioned CDN URL) and fonts: cache first. */
const CACHE = "autoscout-v1";      // same name: keeps the downloaded Python runtime
const SHELL = ["./", "index.html", "app.css", "app.js", "worker.js", "AutoScout.py", "py/tkstub.py", "py/xpr.py", "py/webbridge.py",
  "github-storage-worker/client.js",
  "manifest.webmanifest", "assets/logo-56.png", "assets/icon-192.png", "assets/icon-512.png", "assets/apple-touch-icon.png"];
const NETWORK_WAIT_MS = 4000;     // how long to wait for the network before using the saved copy
// Once the network has been too slow, use saved copies straight away for a
// minute, so a page load gets one consistent set of files instead of a mix.
let slowUntil = 0;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function networkFirst(e) {
  const req = e.request;
  const network = fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); e.waitUntil(caches.open(CACHE).then((c) => c.put(req, copy))); }
    return res;
  });
  e.waitUntil(network.catch(() => {}));      // let a slow download finish and refresh the cache
  const saved = () => caches.match(req, { ignoreSearch: true }).then((r) => r || caches.match("index.html"));
  return new Promise((resolve) => {
    let done = false;
    const finish = (p) => p.then((r) => { if (!done && r) { done = true; resolve(r); } return r; });
    const wait = Date.now() < slowUntil ? 0 : NETWORK_WAIT_MS;
    const timer = setTimeout(() => {
      finish(caches.match(req, { ignoreSearch: true })).then((r) => { if (r) slowUntil = Date.now() + 60000; });
    }, wait);
    network.then((res) => { clearTimeout(timer); if (!done) { done = true; slowUntil = 0; resolve(res); } })
      .catch(() => { clearTimeout(timer); if (!done) saved().then((r) => { if (!done) { done = true; resolve(r || Response.error()); } }); });
  });
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) {
    e.respondWith(networkFirst(e));
    return;
  }
  const fixed = url.hostname === "cdn.jsdelivr.net" && url.pathname.startsWith("/pyodide/");
  const fonts = url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com";
  if (fixed || fonts) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok || res.type === "opaque") { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
      return res;
    })));
  }
  // everything else (FTCScout API, team storage, YouTube) goes straight to the network
});
