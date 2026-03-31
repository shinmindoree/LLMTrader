import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";
import type { NewsPostDto } from "@/lib/newsTypes";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CRYPTOCOMPARE_NEWS = "https://min-api.cryptocompare.com/data/v2/news/";
const CACHE_MS = 120_000; // 2 min — CryptoCompare server cache is 120s anyway

/**
 * CryptoCompare doesn't have sentiment filters like CryptoPanic.
 * Map our UI filters → sortOrder + optional categories:
 *   popular  ≈ hot
 *   latest   ≈ rising / recent
 *   categories can narrow by topic (e.g. "Regulation", "Mining", "Exchange")
 *   but no direct bullish/bearish/important mapping → fall back to popular.
 */
const FILTER_MAP: Record<string, { sortOrder: string; categories?: string }> = {
  hot: { sortOrder: "popular" },
  rising: { sortOrder: "latest" },
  bullish: { sortOrder: "popular" },
  bearish: { sortOrder: "popular" },
  important: { sortOrder: "popular" },
};

const ALLOWED_FILTERS = new Set(Object.keys(FILTER_MAP));

type CacheEntry = { at: number; body: unknown };
const memoryCache = new Map<string, CacheEntry>();

/**
 * Normalise CryptoCompare news response → NewsPostDto[].
 *
 * CryptoCompare fields per article:
 *   id, title, url, published_on (unix), source, body, tags, categories,
 *   source_info { name, lang, img }, imageurl
 */
function normalizePosts(payload: unknown): NewsPostDto[] {
  if (!payload || typeof payload !== "object") return [];
  const root = payload as Record<string, unknown>;
  const items = root.Data;
  if (!Array.isArray(items)) return [];

  const out: NewsPostDto[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;

    const id = String(row.id ?? "");
    const title = typeof row.title === "string" ? row.title : "";
    const url = typeof row.url === "string" ? row.url : typeof row.guid === "string" ? row.guid : "";
    if (!id || !title) continue;

    // published_on is a unix timestamp (seconds)
    let publishedAt: string | null = null;
    if (typeof row.published_on === "number") {
      publishedAt = new Date(row.published_on * 1000).toISOString();
    }

    // source_info.name / extract domain from url
    let sourceTitle: string | null = null;
    let domain: string | null = null;
    const sourceInfo = row.source_info;
    if (sourceInfo && typeof sourceInfo === "object") {
      const si = sourceInfo as Record<string, unknown>;
      if (typeof si.name === "string") sourceTitle = si.name;
    }
    if (typeof row.source === "string") {
      sourceTitle = sourceTitle ?? row.source;
    }
    try {
      domain = new URL(url).hostname;
    } catch {
      /* ignore */
    }

    // CryptoCompare tags categories like "BTC|ETH|Altcoin|Trading"
    const currencies: string[] = [];
    const cats = typeof row.categories === "string" ? row.categories : "";
    const tags = typeof row.tags === "string" ? row.tags : "";
    const combined = `${cats}|${tags}`;
    // Known coin tickers that are likely to appear in categories/tags
    for (const token of combined.split("|")) {
      const t = token.trim().toUpperCase();
      // Only accept 2-6 char all-alpha tokens that look like tickers
      if (t.length >= 2 && t.length <= 6 && /^[A-Z]+$/.test(t)) {
        currencies.push(t);
      }
    }

    out.push({ id, title, url, publishedAt, sourceTitle, domain, currencies });
  }
  return out;
}

export async function GET(req: NextRequest): Promise<Response> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const apiKey = process.env.CRYPTOCOMPARE_API_KEY?.trim();
  if (!apiKey) {
    return NextResponse.json(
      { error: "CRYPTOCOMPARE_API_KEY is not configured", posts: [] as NewsPostDto[] },
      { status: 503 },
    );
  }

  const filterParam = (req.nextUrl.searchParams.get("filter") ?? "hot").toLowerCase();
  const filter = ALLOWED_FILTERS.has(filterParam) ? filterParam : "hot";
  const mapped = FILTER_MAP[filter] ?? FILTER_MAP.hot;

  const cacheKey = filter;
  const now = Date.now();
  const hit = memoryCache.get(cacheKey);
  if (hit && now - hit.at < CACHE_MS) {
    return NextResponse.json(hit.body, {
      status: 200,
      headers: { "cache-control": "private, max-age=60" },
    });
  }

  const upstream = new URL(CRYPTOCOMPARE_NEWS);
  upstream.searchParams.set("sortOrder", mapped.sortOrder);
  upstream.searchParams.set("lang", "EN");
  upstream.searchParams.set("extraParams", "alphaweaver");
  if (mapped.categories) {
    upstream.searchParams.set("categories", mapped.categories);
  }

  try {
    const res = await fetch(upstream.toString(), {
      cache: "no-store",
      headers: { Authorization: `Apikey ${apiKey}` },
    });
    const json = (await res.json()) as unknown;
    if (!res.ok) {
      const detail =
        json && typeof json === "object" && "Message" in json
          ? String((json as Record<string, unknown>).Message)
          : res.statusText;
      return NextResponse.json({ error: detail || "CryptoCompare error", posts: [] }, { status: res.status });
    }
    // CryptoCompare returns Type=1 for errors even with 200 status
    if (json && typeof json === "object") {
      const root = json as Record<string, unknown>;
      if (root.Type === 1 || root.Type === 2) {
        return NextResponse.json(
          { error: String(root.Message ?? "API error"), posts: [] },
          { status: 502 },
        );
      }
    }
    const posts = normalizePosts(json);
    const body = { posts, filter };
    memoryCache.set(cacheKey, { at: now, body });
    return NextResponse.json(body, {
      status: 200,
      headers: { "cache-control": "private, max-age=60" },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Fetch failed";
    return NextResponse.json({ error: message, posts: [] }, { status: 502 });
  }
}
