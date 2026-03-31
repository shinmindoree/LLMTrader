import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/auth";
import type { CryptoPanicPostDto } from "@/lib/cryptopanicTypes";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CRYPTOPANIC = "https://cryptopanic.com/api/v1/posts/";
const CACHE_MS = 45_000;

const ALLOWED_FILTERS = new Set(["rising", "hot", "bullish", "bearish", "important", "saved", "lol"]);

type CacheEntry = { at: number; body: unknown };

const memoryCache = new Map<string, CacheEntry>();

function normalizePosts(payload: unknown): CryptoPanicPostDto[] {
  if (!payload || typeof payload !== "object") return [];
  const root = payload as Record<string, unknown>;
  const results = root.results ?? root.data;
  if (!Array.isArray(results)) return [];
  const out: CryptoPanicPostDto[] = [];
  for (const item of results) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const id = String(row.id ?? row.pk ?? "");
    const title = typeof row.title === "string" ? row.title : "";
    const url = typeof row.url === "string" ? row.url : typeof row.link === "string" ? row.link : "";
    if (!id || !title) continue;
    const publishedAt =
      typeof row.published_at === "string"
        ? row.published_at
        : typeof row.created_at === "string"
          ? row.created_at
          : null;
    let sourceTitle: string | null = null;
    let domain: string | null = null;
    const source = row.source;
    if (source && typeof source === "object") {
      const s = source as Record<string, unknown>;
      if (typeof s.title === "string") sourceTitle = s.title;
      if (typeof s.domain === "string") domain = s.domain;
    }
    const currencies: string[] = [];
    const cur = row.currencies;
    if (Array.isArray(cur)) {
      for (const c of cur) {
        if (typeof c === "string") {
          currencies.push(c.toUpperCase());
        } else if (c && typeof c === "object") {
          const code = (c as Record<string, unknown>).code;
          if (typeof code === "string") currencies.push(code.toUpperCase());
        }
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

  const token = process.env.CRYPTOPANIC_AUTH_TOKEN?.trim();
  if (!token) {
    return NextResponse.json(
      { error: "CRYPTOPANIC_AUTH_TOKEN is not configured", posts: [] as CryptoPanicPostDto[] },
      { status: 503 },
    );
  }

  const filterParam = (req.nextUrl.searchParams.get("filter") ?? "hot").toLowerCase();
  const filter = ALLOWED_FILTERS.has(filterParam) ? filterParam : "hot";
  const publicOnly = req.nextUrl.searchParams.get("public") !== "false";

  const cacheKey = `${filter}:${publicOnly ? "1" : "0"}`;
  const now = Date.now();
  const hit = memoryCache.get(cacheKey);
  if (hit && now - hit.at < CACHE_MS) {
    return NextResponse.json(hit.body, {
      status: 200,
      headers: { "cache-control": "private, max-age=30" },
    });
  }

  const upstream = new URL(CRYPTOPANIC);
  upstream.searchParams.set("auth_token", token);
  upstream.searchParams.set("filter", filter);
  if (publicOnly) {
    upstream.searchParams.set("public", "true");
  }

  try {
    const res = await fetch(upstream.toString(), { cache: "no-store" });
    const json = (await res.json()) as unknown;
    if (!res.ok) {
      const detail =
        json && typeof json === "object" && "info" in json
          ? String((json as Record<string, unknown>).info)
          : res.statusText;
      return NextResponse.json({ error: detail || "CryptoPanic error", posts: [] }, { status: res.status });
    }
    const posts = normalizePosts(json);
    const body = { posts, filter };
    memoryCache.set(cacheKey, { at: now, body });
    return NextResponse.json(body, {
      status: 200,
      headers: { "cache-control": "private, max-age=30" },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Fetch failed";
    return NextResponse.json({ error: message, posts: [] }, { status: 502 });
  }
}
