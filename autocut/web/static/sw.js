// Service worker: makes autocut installable ("Add to Home Screen") and opens instantly.
// Only the app shell is cached; the API and videos always come live from your computer.
const CACHE = "autocut-shell-v2";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/review.js", "/static/editor.js", "/static/editor.css", "/static/icon-192.png",
  "/static/icon-512.png", "/static/apple-touch-icon.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return; // never cache data
  // network first (so updates show up), cache as fallback when the computer is unreachable
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && SHELL.includes(url.pathname)) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request)));
});
