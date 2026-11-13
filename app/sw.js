// Service worker: recebe as notificações push e abre o app ao tocar nelas.
// Não guarda dados em cache: os prazos sempre vêm atualizados da API.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {corpo: e.data && e.data.text()}; }
  e.waitUntil(self.registration.showNotification(d.titulo || "Prazos OAB", {
    body: d.corpo || "",
    icon: "/icone-192.png",
    badge: "/icone-192.png",
    tag: d.tag || undefined,
    renotify: !!d.tag,
    data: {url: d.url || "/"},
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const abertas = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const c of abertas) { if ("focus" in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  })());
});

// Necessário para o app ser considerado instalável em alguns navegadores.
self.addEventListener("fetch", () => {});
