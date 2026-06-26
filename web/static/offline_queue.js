/* offline_queue.js — LocalStorage queue za offline unose
 *
 * API:
 *   TerenOffline.save(sirova, projekt_key)  — spremi u red čekanja
 *   TerenOffline.count()                    — broj pending stavki
 *   TerenOffline.sync()                     — pošalji sve na /teren/unos/sync
 *   TerenOffline.isOnline()                 — trenutni online status
 *
 * Postavi window.__terenSyncQueue = TerenOffline.sync da SW može triggerati.
 */

const TerenOffline = (() => {
  const KEY = "teren_offline_queue";

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
  }

  function save_queue(q) {
    localStorage.setItem(KEY, JSON.stringify(q));
  }

  function save(sirova, projekt_key) {
    const q = load();
    q.push({ sirova, projekt_key, ts: new Date().toISOString() });
    save_queue(q);
    updateBanner();
  }

  function count() { return load().length; }

  function isOnline() { return navigator.onLine; }

  async function sync() {
    if (!isOnline()) return;
    const q = load();
    if (!q.length) return;

    const failed = [];
    for (const item of q) {
      try {
        const res = await fetch("/teren/unos/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sirova: item.sirova, projekt_key: item.projekt_key }),
        });
        const data = await res.json();
        if (!data.ok) failed.push(item);
      } catch {
        failed.push(item);
      }
    }
    save_queue(failed);
    updateBanner();

    const ok = q.length - failed.length;
    if (ok > 0) showToast(`✅ Sinkronizirano ${ok} unos${ok === 1 ? "" : "a"}.`);
    if (failed.length > 0) showToast(`⚠️ ${failed.length} unos${failed.length === 1 ? "" : "a"} nije sinkronizirano.`);
  }

  function updateBanner() {
    const n = count();
    const banner = document.getElementById("offline-banner");
    const countEl = document.getElementById("offline-count");
    if (!banner) return;
    if (n > 0) {
      banner.style.display = "flex";
      if (countEl) countEl.textContent = n;
    } else {
      banner.style.display = "none";
    }
  }

  function showToast(msg) {
    let t = document.getElementById("teren-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "teren-toast";
      t.style.cssText = "position:fixed;bottom:80px;left:50%;transform:translateX(-50%);" +
        "background:#1e293b;color:#fff;padding:.75rem 1.25rem;border-radius:8px;" +
        "font-size:.9rem;z-index:9999;max-width:90vw;text-align:center;";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = "1";
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.style.opacity = "0"; }, 3500);
  }

  // Online/offline eventovi
  window.addEventListener("online", () => {
    showToast("Veza uspostavljena — sinkroniziram...");
    setTimeout(sync, 500);
    updateBanner();
    // Background Sync kao fallback
    if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
      navigator.serviceWorker.ready.then((reg) => {
        if ("sync" in reg) reg.sync.register("teren-sync-unos").catch(() => {});
      });
    }
  });

  window.addEventListener("offline", () => {
    showToast("Nema interneta — unosi se čuvaju lokalno.");
    updateBanner();
  });

  // Eksponiraj sync za SW poruke
  window.__terenSyncQueue = sync;

  // Inicijalizacija pri učitavanju
  document.addEventListener("DOMContentLoaded", () => {
    updateBanner();
    if (isOnline() && count() > 0) sync();
  });

  return { save, count, sync, isOnline };
})();
