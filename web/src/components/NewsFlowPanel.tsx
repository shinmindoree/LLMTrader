"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { useI18n } from "@/lib/i18n";
import { currencyCodesToUsdtPerps } from "@/lib/binanceSymbol";
import type { NewsPostDto } from "@/lib/newsTypes";
import type { FuturesTickerRow } from "@/lib/useBinanceFuturesTickerStream";

const linkFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2962ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#131722]";

type NewsApiOk = { posts: NewsPostDto[]; filter: string; notConfigured?: boolean };

async function fetchNews(filter: string): Promise<NewsApiOk> {
  const res = await fetch(`/api/news/cryptocompare?filter=${encodeURIComponent(filter)}`, {
    cache: "no-store",
  });
  const json = (await res.json()) as NewsApiOk & { error?: string };
  if (!res.ok && res.status !== 503) {
    throw new Error(json.error ?? res.statusText);
  }
  return {
    posts: Array.isArray(json.posts) ? json.posts : [],
    filter: typeof json.filter === "string" ? json.filter : filter,
    notConfigured: res.status === 503 || (typeof json.error === "string" && json.error.includes("CRYPTOCOMPARE_API_KEY")),
  };
}

type ExtraTickerPayload = { tickers?: Record<string, { last: number; pct24h: number }> };

async function fetchExtraTickers(symbols: string): Promise<ExtraTickerPayload> {
  const res = await fetch(`/api/binance/futures-tickers?symbols=${encodeURIComponent(symbols)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()) as ExtraTickerPayload;
}

function formatLast(price: number): string {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (price >= 1) return price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  return price.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 8 });
}

function formatPct(pct: number): string {
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function NewsFlowPanel({
  tickersBySymbol,
}: {
  tickersBySymbol: Record<string, FuturesTickerRow>;
}) {
  const { t, locale } = useI18n();
  const [filter, setFilter] = useState("hot");
  const { data, error, isLoading, mutate } = useSWR(["news-cryptocompare", filter], () => fetchNews(filter), {
    refreshInterval: 60_000,
    revalidateOnFocus: true,
  });

  const posts = data?.posts ?? [];
  const [selectedIndex, setSelectedIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (selectedIndex >= posts.length) {
      setSelectedIndex(posts.length > 0 ? posts.length - 1 : 0);
    }
  }, [posts.length, selectedIndex]);

  const selectedId = posts[selectedIndex]?.id;
  useEffect(() => {
    if (!selectedId) return;
    const el = document.getElementById(`news-item-${selectedId}`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedIndex, selectedId]);

  const selected = posts[selectedIndex] ?? null;

  const relatedSyms = useMemo(
    () => (selected ? currencyCodesToUsdtPerps(selected.currencies) : []),
    [selected],
  );

  const missingForRest = useMemo(() => {
    return relatedSyms.filter((s) => !tickersBySymbol[s]);
  }, [relatedSyms, tickersBySymbol]);

  const extraKey = missingForRest.length > 0 ? [...missingForRest].sort().join(",") : null;

  const { data: extraTickers } = useSWR(
    extraKey ? ["futures-tickers-news-extra", extraKey] : null,
    () => fetchExtraTickers(extraKey!),
    { revalidateOnFocus: false, dedupingInterval: 20_000 },
  );

  const mergedForRelated = useMemo(() => {
    const out: Record<string, { last: number; pct24h: number }> = {};
    for (const sym of relatedSyms) {
      const live = tickersBySymbol[sym];
      if (live) {
        out[sym] = { last: live.last, pct24h: live.pct24h };
        continue;
      }
      const rest = extraTickers?.tickers?.[sym];
      if (rest) out[sym] = rest;
    }
    return out;
  }, [relatedSyms, tickersBySymbol, extraTickers]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (posts.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, posts.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      }
    },
    [posts.length],
  );

  const formatTime = (iso: string | null) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString(locale === "ko" ? "ko-KR" : "en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      });
    } catch {
      return iso;
    }
  };

  const notConfigured = data?.notConfigured === true;

  return (
    <section
      className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4"
      aria-labelledby="news-flow-heading"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="news-flow-heading" className="text-sm font-semibold text-[#d1d4dc]">
          {t.dashboard.newsFlowTitle}
        </h2>
        <div className="flex items-center gap-2">
          <label htmlFor="news-flow-filter" className="sr-only">
            {t.dashboard.newsFlowFilterLabel}
          </label>
          <select
            id="news-flow-filter"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setSelectedIndex(0);
            }}
            className={`rounded border border-[#2a2e39] bg-[#131722] px-2 py-1 text-xs text-[#d1d4dc] ${linkFocusRing}`}
          >
            <option value="hot">{t.dashboard.newsFlowFilterHot}</option>
            <option value="rising">{t.dashboard.newsFlowFilterRising}</option>
          </select>
          <button
            type="button"
            onClick={() => void mutate()}
            className={`rounded border border-[#2a2e39] px-2 py-1 text-xs font-medium text-[#d1d4dc] hover:border-[#2962ff] ${linkFocusRing}`}
          >
            {t.dashboard.newsFlowRefresh}
          </button>
        </div>
      </div>

      <p className="mb-2 text-[10px] text-[#555]">{t.dashboard.newsFlowKeyboardHint}</p>

      {error ? (
        <div className="rounded border border-[#ef5350]/35 bg-[#ef5350]/10 px-3 py-2 text-sm text-[#d1d4dc]">
          <p>{t.dashboard.newsFlowError}</p>
          <button
            type="button"
            onClick={() => void mutate()}
            className={`mt-2 text-xs font-medium text-[#2962ff] hover:text-[#5b8cff] ${linkFocusRing}`}
          >
            {t.dashboard.newsFlowRetry}
          </button>
        </div>
      ) : null}

      {!error && isLoading && posts.length === 0 ? (
        <div className="flex min-h-[280px] items-center justify-center text-sm text-[#868993]">
          {t.dashboard.newsFlowLoading}
        </div>
      ) : null}

      {!error && !isLoading && posts.length === 0 ? (
        <div className="rounded border border-dashed border-[#2a2e39] px-3 py-6 text-center text-sm text-[#868993]">
          {notConfigured ? t.dashboard.newsFlowNotConfigured : t.dashboard.newsFlowEmpty}
        </div>
      ) : null}

      {posts.length > 0 ? (
        <div className="grid min-h-[420px] grid-cols-1 gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div
            ref={listRef}
            role="listbox"
            tabIndex={0}
            aria-label={t.dashboard.newsFlowListAria}
            aria-activedescendant={selected ? `news-item-${selected.id}` : undefined}
            onKeyDown={onKeyDown}
            className={`max-h-[420px] overflow-y-auto rounded border border-[#2a2e39] bg-[#131722] p-1 outline-none ${linkFocusRing}`}
          >
            {posts.map((p, idx) => {
              const active = idx === selectedIndex;
              return (
                <button
                  key={p.id}
                  type="button"
                  id={`news-item-${p.id}`}
                  role="option"
                  aria-selected={active}
                  onClick={() => setSelectedIndex(idx)}
                  className={`w-full rounded px-2 py-2.5 text-left transition-colors ${
                    active ? "bg-[#2962ff]/20" : "hover:bg-[#2a2e39]/80"
                  }`}
                >
                  <div className="text-[10px] text-[#555]">
                    {formatTime(p.publishedAt)}
                    {p.sourceTitle ? ` · ${p.sourceTitle}` : ""}
                  </div>
                  <div className="mt-0.5 text-xs font-medium leading-snug text-[#d1d4dc]">{p.title}</div>
                </button>
              );
            })}
          </div>

          <div className="flex max-h-[420px] min-h-0 flex-col overflow-y-auto rounded border border-[#2a2e39] bg-[#131722] p-4">
            {selected ? (
              <>
                <h3 className="shrink-0 text-sm font-semibold leading-snug text-[#d1d4dc]">{selected.title}</h3>
                <p className="mt-2 text-[11px] text-[#868993]">
                  {formatTime(selected.publishedAt)}
                  {selected.sourceTitle ? ` · ${selected.sourceTitle}` : ""}
                  {selected.domain ? ` · ${selected.domain}` : ""}
                </p>
                {selected.url ? (
                  <a
                    href={selected.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`mt-3 inline-flex w-fit text-xs font-medium text-[#2962ff] hover:text-[#5b8cff] ${linkFocusRing} rounded-sm`}
                  >
                    {t.dashboard.newsFlowOpenArticle} →
                  </a>
                ) : null}

                {relatedSyms.length > 0 ? (
                  <div className="mt-4 border-t border-[#2a2e39] pt-3">
                    <h4 className="text-[10px] font-semibold uppercase tracking-wide text-[#555]">
                      {t.dashboard.newsFlowRelatedPrices}
                    </h4>
                    <div className="mt-2 space-y-2">
                      {relatedSyms.map((sym) => {
                        const row = mergedForRelated[sym];
                        const pct = row?.pct24h ?? 0;
                        const pctClass =
                          pct > 0 ? "text-[#26a69a]" : pct < 0 ? "text-[#ef5350]" : "text-[#868993]";
                        return (
                          <div
                            key={sym}
                            className="flex items-center justify-between rounded bg-[#1e222d] px-2 py-1.5 text-xs"
                          >
                            <span className="font-medium text-[#d1d4dc]">{sym}</span>
                            <span className="tabular-nums text-[#b2b5be]">
                              {row ? formatLast(row.last) : "—"}
                            </span>
                            <span className={`w-16 text-right text-[11px] font-medium tabular-nums ${pctClass}`}>
                              {row ? formatPct(pct) : "—"}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                {selected.body ? (
                  <div className="mt-4 border-t border-[#2a2e39] pt-3">
                    <p className="whitespace-pre-line text-xs leading-relaxed text-[#b2b5be]">
                      {selected.body}
                    </p>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-[#868993]">{t.dashboard.newsFlowPickOne}</p>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
