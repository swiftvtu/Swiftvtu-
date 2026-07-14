/**
 * SwiftVTU Service Worker
 * Handles: offline caching, background sync, push notifications
 */

const APP_VERSION    = 'v1.0.0';
const CACHE_STATIC   = `swiftvtu-static-${APP_VERSION}`;
const CACHE_DYNAMIC  = `swiftvtu-dynamic-${APP_VERSION}`;
const CACHE_API      = `swiftvtu-api-${APP_VERSION}`;

// Files to cache immediately on install (app shell)
const STATIC_ASSETS = [
  '/vtu-app.html',
  '/manifest.json',
  '/sw.js',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap',
];

// API routes to cache responses from (read-only, safe to cache)
const CACHEABLE_API = [
  '/api/v1/auth/me',
  '/api/v1/wallet/transactions',
];

// API routes that should queue when offline for background sync
const SYNC_QUEUE_KEY = 'swiftvtu-sync-queue';


// ════════════════════════════════════════════════════
// INSTALL — cache the app shell
// ════════════════════════════════════════════════════
self.addEventListener('install', event => {
  console.log('[SW] Installing…');
  event.waitUntil(
    caches.open(CACHE_STATIC).then(cache => {
      // Use individual adds so one failure doesn't break the whole install
      return Promise.allSettled(
        STATIC_ASSETS.map(url => cache.add(url).catch(e => console.warn('[SW] Failed to cache:', url, e)))
      );
    }).then(() => self.skipWaiting())
  );
});


// ════════════════════════════════════════════════════
// ACTIVATE — clean up old caches
// ════════════════════════════════════════════════════
self.addEventListener('activate', event => {
  console.log('[SW] Activating…');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_STATIC && key !== CACHE_DYNAMIC && key !== CACHE_API)
          .map(key => { console.log('[SW] Deleting old cache:', key); return caches.delete(key); })
      )
    ).then(() => self.clients.claim())
  );
});


// ════════════════════════════════════════════════════
// FETCH — smart caching strategy
// ════════════════════════════════════════════════════
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests for caching (POST/PUT handled by background sync)
  if (request.method !== 'GET') return;

  // Skip chrome-extension and non-http requests
  if (!request.url.startsWith('http')) return;

  // ── Strategy 1: API /auth/me and /transactions → Network first, fallback to cache ──
  if (CACHEABLE_API.some(path => url.pathname.startsWith(path))) {
    event.respondWith(networkFirstWithCache(request, CACHE_API, 5000));
    return;
  }

  // ── Strategy 2: Google Fonts → Cache first (rarely changes) ──
  if (url.hostname.includes('fonts.googleapis.com') || url.hostname.includes('fonts.gstatic.com')) {
    event.respondWith(cacheFirstWithNetwork(request, CACHE_STATIC));
    return;
  }

  // ── Strategy 3: App shell (HTML, SW, manifest) → Cache first ──
  if (url.pathname.endsWith('.html') || url.pathname.endsWith('manifest.json') || url.pathname.endsWith('sw.js')) {
    event.respondWith(cacheFirstWithNetwork(request, CACHE_STATIC));
    return;
  }

  // ── Strategy 4: Everything else → Network first, dynamic cache fallback ──
  event.respondWith(networkFirstWithCache(request, CACHE_DYNAMIC, 8000));
});


// ── Network first (with timeout), then cache ──
async function networkFirstWithCache(request, cacheName, timeout = 6000) {
  const cache = await caches.open(cacheName);
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const response = await fetch(request, { signal: controller.signal });
    clearTimeout(timer);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    // Return offline page for navigation requests
    if (request.mode === 'navigate') {
      return caches.match('/vtu-app.html');
    }
    return new Response(JSON.stringify({ detail: 'You are offline' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// ── Cache first, then network ──
async function cacheFirstWithNetwork(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Offline', { status: 503 });
  }
}


// ════════════════════════════════════════════════════
// BACKGROUND SYNC — retry failed transactions
// ════════════════════════════════════════════════════
self.addEventListener('sync', event => {
  console.log('[SW] Background sync:', event.tag);
  if (event.tag === 'swiftvtu-tx-sync') {
    event.waitUntil(replayQueuedRequests());
  }
});

async function replayQueuedRequests() {
  const clients = await self.clients.matchAll();
  try {
    // Read queue from IndexedDB (via message to client)
    const queue = await getQueueFromIDB();
    if (!queue || !queue.length) return;

    const remaining = [];
    for (const item of queue) {
      try {
        const response = await fetch(item.url, {
          method:  item.method,
          headers: item.headers,
          body:    item.body,
        });
        if (response.ok) {
          console.log('[SW] Replayed queued request:', item.url);
          // Notify all clients
          clients.forEach(client => client.postMessage({
            type: 'SYNC_SUCCESS',
            url:  item.url,
            tag:  item.tag,
          }));
        } else {
          remaining.push(item);
        }
      } catch {
        remaining.push(item); // still offline, keep in queue
      }
    }
    await saveQueueToIDB(remaining);
  } catch (e) {
    console.error('[SW] Sync error:', e);
  }
}

// Simple IDB helpers for the sync queue
function openIDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('swiftvtu-sw', 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore('queue', { keyPath: 'id', autoIncrement: true });
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

async function getQueueFromIDB() {
  const db    = await openIDB();
  const tx    = db.transaction('queue', 'readonly');
  const store = tx.objectStore('queue');
  return new Promise((resolve, reject) => {
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror   = () => reject(req.error);
  });
}

async function saveQueueToIDB(items) {
  const db    = await openIDB();
  const tx    = db.transaction('queue', 'readwrite');
  const store = tx.objectStore('queue');
  store.clear();
  items.forEach(item => store.add(item));
  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror    = () => reject(tx.error);
  });
}


// ════════════════════════════════════════════════════
// PUSH NOTIFICATIONS
// ════════════════════════════════════════════════════
self.addEventListener('push', event => {
  if (!event.data) return;

  let data;
  try { data = event.data.json(); }
  catch { data = { title: 'SwiftVTU', body: event.data.text() }; }

  const options = {
    body:    data.body  || 'You have a new notification',
    icon:    data.icon  || '/icons/icon-192.png',
    badge:   data.badge || '/icons/icon-96.png',
    image:   data.image,
    vibrate: [100, 50, 100],
    data:    { url: data.url || '/vtu-app.html', ...data.data },
    actions: data.actions || [],
    tag:     data.tag || 'swiftvtu-notif',
    requireInteraction: data.requireInteraction || false,
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'SwiftVTU', options)
  );
});

// Handle notification click — open/focus the app
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/vtu-app.html';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      // Focus existing window if open
      for (const client of clients) {
        if (client.url.includes('vtu-app') && 'focus' in client) {
          return client.focus().then(c => c.postMessage({ type: 'NAVIGATE', page: event.notification.data?.page }));
        }
      }
      // Otherwise open new window
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});

// Handle notification dismiss
self.addEventListener('notificationclose', event => {
  console.log('[SW] Notification dismissed:', event.notification.tag);
});


// ════════════════════════════════════════════════════
// MESSAGE HANDLER — from main app
// ════════════════════════════════════════════════════
self.addEventListener('message', event => {
  const { type, payload } = event.data || {};

  if (type === 'SKIP_WAITING') {
    self.skipWaiting();
  }

  if (type === 'QUEUE_REQUEST') {
    // Store a request for background sync retry
    openIDB().then(db => {
      const tx    = db.transaction('queue', 'readwrite');
      const store = tx.objectStore('queue');
      store.add({ ...payload, queued: Date.now() });
    });
    self.registration.sync.register('swiftvtu-tx-sync').catch(() => {});
  }

  if (type === 'CLEAR_CACHE') {
    caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))));
  }
});
