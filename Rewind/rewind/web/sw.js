// Rewind no longer uses a service worker. This one removes any older copy and reloads the page.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) await caches.delete(k);
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({type: "window"})) c.navigate(c.url);
})()));
