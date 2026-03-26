// Read-only endpoints (polling, listing) get a generous limit
const readWindowMs = 60_000;
const maxReadRequests = 300;

// Write endpoints (create, stop, delete, generate) get a stricter limit
const writeWindowMs = 60_000;
const maxWriteRequests = 60;

type Entry = { count: number; resetAt: number };
const readStore = new Map<string, Entry>();
const writeStore = new Map<string, Entry>();

let lastCleanup = Date.now();
const CLEANUP_INTERVAL = 5 * 60_000;

function cleanupStore(store: Map<string, Entry>, now: number) {
  for (const [key, entry] of store) {
    if (entry.resetAt <= now) store.delete(key);
  }
}

function cleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL) return;
  lastCleanup = now;
  cleanupStore(readStore, now);
  cleanupStore(writeStore, now);
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function checkLimit(
  store: Map<string, Entry>,
  key: string,
  windowMs: number,
  maxRequests: number,
): boolean {
  cleanup();
  const now = Date.now();
  const entry = store.get(key);
  if (!entry || entry.resetAt <= now) {
    store.set(key, { count: 1, resetAt: now + windowMs });
    return false;
  }
  entry.count += 1;
  return entry.count > maxRequests;
}

export function isRateLimited(key: string, method: string): boolean {
  if (WRITE_METHODS.has(method.toUpperCase())) {
    return checkLimit(writeStore, key, writeWindowMs, maxWriteRequests);
  }
  return checkLimit(readStore, key, readWindowMs, maxReadRequests);
}

export function getRateLimitHeaders(key: string, method: string): Record<string, string> {
  const isWrite = WRITE_METHODS.has(method.toUpperCase());
  const store = isWrite ? writeStore : readStore;
  const max = isWrite ? maxWriteRequests : maxReadRequests;
  const entry = store.get(key);
  if (!entry) return {};
  const remaining = Math.max(0, max - entry.count);
  const reset = Math.ceil(entry.resetAt / 1000);
  return {
    "X-RateLimit-Limit": String(max),
    "X-RateLimit-Remaining": String(remaining),
    "X-RateLimit-Reset": String(reset),
    "Retry-After": String(Math.max(1, Math.ceil((entry.resetAt - Date.now()) / 1000))),
  };
}
