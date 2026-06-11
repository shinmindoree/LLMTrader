"use client";

import { useMemo, useState } from "react";
import { useI18n } from "@/lib/i18n";
import type { KimpScreenerItem, KimpScreenerResponse } from "@/lib/types";
import type { KimpScreenerStreamStatus } from "@/lib/useKimpScreenerStream";

type SortKey =
  | "symbol"
  | "kimp_pct"
  | "zscore_30d"
  | "mean_30d_pct"
  | "n_samples_30d";

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtKrw(v: number): string {
  return `₩${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtUsd(v: number): string {
  return `$${v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function fmtZ(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

function kimpClass(v: number): string {
  if (v >= 0.005) return "text-rose-400";
  if (v <= -0.005) return "text-emerald-400";
  return "text-[#c3c5cc]";
}

function zClass(v: number | null | undefined): string {
  if (v == null) return "text-[#868993]";
  const abs = Math.abs(v);
  if (abs >= 2) return "text-rose-400";
  if (abs >= 1) return "text-amber-400";
  return "text-[#c3c5cc]";
}

type Props = {
  symbol: string;
  onSelect: (symbol: string) => void;
  data: KimpScreenerResponse | null;
  error: Error | null;
  isLoading: boolean;
  isValidating: boolean;
  status: KimpScreenerStreamStatus;
  onRefresh: () => void;
};

export default function KimpScreenerTable({
  symbol,
  onSelect,
  data,
  error,
  isLoading,
  isValidating,
  status,
  onRefresh,
}: Props) {
  const { t } = useI18n();
  const s = t.hubs.arbitrage.kimp.screener;

  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("kimp_pct");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const items = useMemo<KimpScreenerItem[]>(() => {
    if (!data?.items) return [];
    const q = query.trim().toUpperCase();
    const filtered = q
      ? data.items.filter((it) => it.symbol.includes(q))
      : data.items.slice();
    filtered.sort((a, b) => {
      const av = (a[sortKey] ?? -Infinity) as number;
      const bv = (b[sortKey] ?? -Infinity) as number;
      let cmp = 0;
      if (typeof av === "string" || typeof bv === "string") {
        cmp = String(av).localeCompare(String(bv));
      } else {
        cmp = av === bv ? 0 : av < bv ? -1 : 1;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return filtered;
  }, [data, query, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "symbol" ? "asc" : "desc");
    }
  }

  function sortArrow(key: SortKey): string {
    if (key !== sortKey) return "";
    return sortDir === "asc" ? "▲" : "▼";
  }

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="flex flex-col gap-2 border-b border-[#26272d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-white">{s.title}</div>
          <div className="text-xs text-[#868993]">{s.subtitle}</div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={s.searchPlaceholder}
            className="rounded-md border border-[#26272d] bg-[#0e0f14] px-2 py-1 text-xs text-[#c3c5cc] placeholder:text-[#5b5d66] focus:border-[#3a3b44] focus:outline-none"
          />
          <button
            type="button"
            onClick={onRefresh}
            disabled={isValidating}
            className="rounded-md border border-[#26272d] bg-[#1a1b22] px-2.5 py-1 text-xs text-[#c3c5cc] hover:bg-[#22232b] disabled:opacity-50"
          >
            {isValidating ? s.refreshing : s.refresh}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-xs">
          <thead className="bg-[#0e0f14] text-[10px] uppercase tracking-wider text-[#868993]">
            <tr>
              <Th onClick={() => toggleSort("symbol")} arrow={sortArrow("symbol")}>
                {s.columns.symbol}
              </Th>
              <Th align="right">{s.columns.upbit}</Th>
              <Th align="right">{s.columns.binance}</Th>
              <Th align="right">{s.columns.usdtKrw}</Th>
              <Th
                align="right"
                onClick={() => toggleSort("kimp_pct")}
                arrow={sortArrow("kimp_pct")}
              >
                {s.columns.kimp}
              </Th>
              <Th
                align="right"
                onClick={() => toggleSort("mean_30d_pct")}
                arrow={sortArrow("mean_30d_pct")}
              >
                {s.columns.mean30d}
              </Th>
              <Th align="right">{s.columns.std30d}</Th>
              <Th
                align="right"
                onClick={() => toggleSort("zscore_30d")}
                arrow={sortArrow("zscore_30d")}
              >
                {s.columns.zscore}
              </Th>
              <Th
                align="right"
                onClick={() => toggleSort("n_samples_30d")}
                arrow={sortArrow("n_samples_30d")}
              >
                {s.columns.samples}
              </Th>
            </tr>
          </thead>
          <tbody>
            {isLoading && !data ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-[#868993]">
                  {t.hubs.arbitrage.kimp.common.loading}
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-[#868993]">
                  {s.empty}
                </td>
              </tr>
            ) : (
              items.map((it) => {
                const selected = it.symbol === symbol;
                return (
                  <tr
                    key={it.symbol}
                    onClick={() => onSelect(it.symbol)}
                    className={`cursor-pointer border-t border-[#1a1b22] tabular-nums transition-colors hover:bg-[#1a1b22] ${
                      selected ? "bg-[#1a1b22]" : ""
                    }`}
                  >
                    <td className="px-3 py-2 font-medium text-white">
                      {it.symbol}
                    </td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">
                      {fmtKrw(it.upbit_krw_price)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#c3c5cc]">
                      {fmtUsd(it.binance_usdt_price)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">
                      {it.usdt_krw_rate.toFixed(2)}
                    </td>
                    <td className={`px-3 py-2 text-right font-medium ${kimpClass(it.kimp_pct)}`}>
                      {fmtPct(it.kimp_pct)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">
                      {fmtPct(it.mean_30d_pct)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">
                      {fmtPct(it.std_30d_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right ${zClass(it.zscore_30d)}`}>
                      {fmtZ(it.zscore_30d)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#868993]">
                      {it.n_samples_30d}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {data?.errors && data.errors.length > 0 ? (
        <div className="border-t border-[#26272d] bg-[#1a1b22] px-4 py-2 text-[11px] text-amber-400">
          <span className="font-semibold">{s.errorsTitle}: </span>
          {data.errors.slice(0, 3).join(" · ")}
        </div>
      ) : null}

      {data?.as_of ? (
        <div className="flex items-center justify-between border-t border-[#26272d] px-4 py-2 text-[10px] text-[#5b5d66]">
          <span className={status === "live" ? "text-emerald-400" : "text-amber-400"}>
            {status === "live" ? s.live : s.fallback}
          </span>
          <span>
            {s.asOf}: {new Date(data.as_of).toLocaleTimeString()}
          </span>
        </div>
      ) : null}

      {error ? (
        <div className="border-t border-[#26272d] px-4 py-2 text-[11px] text-rose-400">
          {t.hubs.arbitrage.kimp.common.loadFailed}
        </div>
      ) : null}
    </div>
  );
}

function Th({
  children,
  align = "left",
  onClick,
  arrow,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  onClick?: () => void;
  arrow?: string;
}) {
  const className = `px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${
    onClick ? "cursor-pointer select-none hover:text-[#c3c5cc]" : ""
  }`;
  return (
    <th className={className} onClick={onClick}>
      {children}
      {arrow ? <span className="ml-1 text-[#868993]">{arrow}</span> : null}
    </th>
  );
}
