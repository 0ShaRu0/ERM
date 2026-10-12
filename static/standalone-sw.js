const PREFIX = `erm-independent:${self.registration.scope}:`;
const CACHE = `${PREFIX}v3`;
const ROOT = self.registration.scope;
const SHELL = ["", "index.html", "static/app.css", "static/app.js", "static/local-store.js", "static/usb-merge.js", "static/usb-client.js",
  "static/icon.svg", "static/icon-192.png", "static/icon-512.png", "static/manifest.webmanifest"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL.map(path => new URL(path, ROOT).href))));
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith(PREFIX) && key !== CACHE).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith(new URL("usb-sync/", ROOT).pathname)) return;
  if (event.request.method !== "GET" || !url.href.startsWith(ROOT)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    if (event.request.mode === "navigate") return (await cache.match(ROOT)) || fetch(event.request);
    return (await cache.match(event.request, { ignoreSearch: true })) || fetch(event.request);
  })());
});
