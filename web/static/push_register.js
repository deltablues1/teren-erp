/* push_register.js — registrira service worker i subscribira na push notifikacije */

(async function () {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;

  // VAPID public key umetnut od servera kao <meta name="vapid-public-key">
  const metaKey = document.querySelector('meta[name="vapid-public-key"]');
  const vapidPublicKey = metaKey ? metaKey.content : "";

  // Registracija SW
  let reg;
  try {
    reg = await navigator.serviceWorker.register("/static/sw.js", { scope: "/teren/" });
    await navigator.serviceWorker.ready;
  } catch (err) {
    console.warn("[PWA] SW registracija nije uspjela:", err);
    return;
  }

  // Slušaj poruke od SW (Background Sync request)
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data && e.data.type === "SW_SYNC_REQUEST") {
      window.__terenSyncQueue && window.__terenSyncQueue();
    }
  });

  if (!vapidPublicKey) return;

  // Provjeri postojeću subscription
  try {
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") return;

      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });
    }

    // Pošalji subscription na server
    await fetch("/teren/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    });
  } catch (err) {
    console.warn("[PWA] Push subscription nije uspjela:", err);
  }
})();

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}
