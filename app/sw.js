// Service worker: notificações push e funcionamento sem internet.
// - Arquivos do app: guardados no aparelho (abre mesmo offline).
// - Dados (/api/dados): a página guarda uma cópia e usa se a rede falhar.
const VERSAO = "prazos-app-v3";
const ARQUIVOS = ["/", "/manifest.webmanifest", "/icone-192.png", "/icone-180.png", "/relatorio.html"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSAO).then(c => c.addAll(ARQUIVOS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("prazos-app-") && k !== VERSAO) await caches.delete(k);
    await self.clients.claim();
  })());
});

// Páginas e arquivos: tenta a rede primeiro (sempre a versão mais nova); sem rede, usa a cópia.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/cal/")) return;
  e.respondWith((async () => {
    try {
      const r = await fetch(e.request);
      if (r.ok) { const c = await caches.open(VERSAO); c.put(e.request, r.clone()); }
      return r;
    } catch (_) {
      return (await caches.match(e.request)) || (await caches.match("/")) || Response.error();
    }
  })());
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {corpo: e.data && e.data.text()}; }
  e.waitUntil(self.registration.showNotification(d.titulo || "Prazos", {
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
