import Redis from "ioredis";

import type { FuturesTickerFields } from "@/lib/server/binanceFutures24hr";

const CACHE_KEY = "futures:tickers:usd_m:v1";
const CACHE_TTL_SEC = 120;

export type FuturesTickerCachePayload = {
  updatedAt: number;
  tickers: Record<string, FuturesTickerFields>;
};

let redisClient: Redis | null | undefined;

function redisUrl(): string | undefined {
  const u = process.env.REDIS_URL?.trim();
  if (u) return u;
  const azure = process.env.AZURE_REDIS_CONNECTION_STRING?.trim();
  return azure || undefined;
}

function getRedis(): Redis | null {
  if (redisClient === undefined) {
    const url = redisUrl();
    if (!url) {
      redisClient = null;
      return null;
    }
    try {
      redisClient = new Redis(url, {
        maxRetriesPerRequest: 2,
        connectTimeout: 8000,
        tls: url.startsWith("rediss://") ? {} : undefined,
      });
    } catch {
      redisClient = null;
    }
  }
  return redisClient;
}

export function isFuturesTickerCacheConfigured(): boolean {
  return Boolean(redisUrl());
}

export async function readFuturesTickerCache(): Promise<FuturesTickerCachePayload | null> {
  const r = getRedis();
  if (!r) return null;
  try {
    const raw = await r.get(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as FuturesTickerCachePayload;
    if (!parsed || typeof parsed.updatedAt !== "number" || typeof parsed.tickers !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

export async function writeFuturesTickerCache(payload: FuturesTickerCachePayload): Promise<void> {
  const r = getRedis();
  if (!r) throw new Error("Redis not configured");
  await r.set(CACHE_KEY, JSON.stringify(payload), "EX", CACHE_TTL_SEC);
}

export const FUTURES_TICKER_CACHE_MAX_AGE_MS = 45_000;
