/* Offline support. The site's own files: network first, so a new AutoScout.py
   shows up as soon as you're online, with the saved copy as the fallback.
   The Python runtime (a fixed, versioned CDN URL) and fonts: cache first. */
const CACHE = "autoscout-v1";
const SHELL = ["./", "index.html", "app.css", "app.js", "worker.js", "AutoScout.py", "py/tkstub.py", "py/webbridge.py",
  "manifest.webmanifest", "assets/logo-56.png", "assets/icon-192.png", "assets/icon-512.png", "assets/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) {
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }).then((r) => r || caches.match("index.html"))));
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
  // everything else (FTCScout API, YouTube) goes straight to the network
});
