const windowMs = 60_000;
const maxRequests = 60;

type Entry = { count: number; resetAt: number };
const store = new Map<string, Entry>();

let lastCleanup = Date.now();
const CLEANUP_INTERVAL = 5 * 60_000;

function cleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL) return;
  lastCleanup = now;
  for (const [key, entry] of store) {
    if (entry.resetAt <= now) store.delete(key);
  }
}

export function isRateLimited(ip: string): boolean {
  cleanup();
  const now = Date.now();
  const entry = store.get(ip);
  if (!entry || entry.resetAt <= now) {
    store.set(ip, { count: 1, resetAt: now + windowMs });
    return false;
  }
  entry.count += 1;
  return entry.count > maxRequests;
}

export function getRateLimitHeaders(ip: string): Record<string, string> {
  const entry = store.get(ip);
  if (!entry) return {};
  const remaining = Math.max(0, maxRequests - entry.count);
  const reset = Math.ceil(entry.resetAt / 1000);
  return {
    "X-RateLimit-Limit": String(maxRequests),
    "X-RateLimit-Remaining": String(remaining),
    "X-RateLimit-Reset": String(reset),
  };
}
