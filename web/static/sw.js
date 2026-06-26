/* Service Worker — Teren PWA
 * Brine o: offline caching stranica, push notifikacijama, background sync
 */

const CACHE = "teren-v1";
const PRECACHE = ["/teren", "/teren/unos", "/teren/zadaci", "/teren/login"];

// ── Install: predcache ključnih stranica ──────────────────────────────────────
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) =>
      Promise.allSettled(PRECACHE.map((url) => c.add(url).catch(() => {})))
    )
  );
  self.skipWaiting();
});

// ── Activate: briši stare cacheve ────────────────────────────────────────────
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch: network-first, fallback na cache za GET stranice ──────────────────
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Preskočimo non-GET i API pozive (parse, spremi, push)
  if (req.method !== "GET") return;
  if (url.pathname.includes("/unos/") || url.pathname.includes("/push/")) return;
  // Preskočimo cross-origin (CDN)
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    fetch(req)
      .then((res) => {
        // Spremi svježi odgovor u cache (samo 200)
        if (res.ok && url.pathname.startsWith("/teren")) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(req, clone));
        }
        return res;
      })
      .catch(() => caches.match(req).then((cached) => cached || offlinePage()))
  );
});

function offlinePage() {
  return new Response(
    `<!doctype html><html lang="hr"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Offline — Teren</title>
    <style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
    min-height:100vh;margin:0;background:#0f766e;color:#fff;text-align:center;padding:2rem}
    h1{font-size:2rem}p{opacity:.85}</style></head>
    <body><div><h1>Nema veze</h1>
    <p>Provjeri internet i pokušaj ponovo.<br>Unosi se mogu upisati offline i sinkronizirati kasnije.</p>
    <button onclick="location.reload()" style="margin-top:1.5rem;padding:.8rem 2rem;
    background:#fff;color:#0f766e;border:none;border-radius:8px;font-size:1rem;cursor:pointer">
    Pokušaj ponovo</button></div></body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } }
  );
}

// ── Push: prikaži notifikaciju ────────────────────────────────────────────────
self.addEventListener("push", (e) => {
  let data = { title: "Teren ERP", body: "Imate novi zadatak." };
  try { data = e.data ? e.data.json() : data; } catch {}

  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      tag: "teren-zadatak",
      renotify: true,
      vibrate: [200, 100, 200],
    })
  );
});

// ── Notification click: otvori zadatke ───────────────────────────────────────
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((cs) => {
        const existing = cs.find((c) => c.url.includes("/teren"));
        if (existing) return existing.focus();
        return clients.openWindow("/teren/zadaci");
      })
  );
});

// ── Background Sync: pošalji offline queue ────────────────────────────────────
self.addEventListener("sync", (e) => {
  if (e.tag === "teren-sync-unos") {
    e.waitUntil(syncPendingUnosi());
  }
});

async function syncPendingUnosi() {
  // SW ne može čitati localStorage — poruka klientima da sami sync-aju
  const cs = await clients.matchAll({ type: "window" });
  cs.forEach((c) => c.postMessage({ type: "SW_SYNC_REQUEST" }));
}
