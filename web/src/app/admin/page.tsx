"use client";

import { useCallback, useEffect, useState } from "react";

import { getStrategyCapabilities, getStrategyQualitySummary } from "@/lib/api";
import type {
  CountItem,
  StrategyCapabilitiesResponse,
  StrategyQualitySummaryResponse,
} from "@/lib/types";

const ERROR_STAGE_LABELS: Record<string, string> = {
  invalid_input: "Input validation failed",
  strategy_dirs: "Strategy directory setup failed",
  client_init: "Model client initialization failed",
  intake_blocked: "Request blocked during intake",
  model_generation: "Strategy generation failed",
  stream_generation: "Streaming generation failed",
  stream_exception: "Streaming exception",
  empty_code: "Generated code was empty",
  verification: "Strategy verification failed",
  unhandled: "Unhandled internal error",
};

const MISSING_FIELD_LABELS: Record<string, string> = {
  symbol: "Trading symbol",
  timeframe: "Timeframe",
  entry_logic: "Entry rules",
  exit_logic: "Exit rules",
  risk: "Risk settings",
};

const CONTEXT_METHOD_LABELS: Record<string, string> = {
  current_price: "Current market price",
  position_size: "Current position size",
  position_entry_price: "Average entry price",
  unrealized_pnl: "Unrealized PnL",
  balance: "Available balance",
  buy: "Market buy",
  sell: "Market sell",
  close_position: "Close position",
  calc_entry_quantity: "Auto-calculate order size",
  enter_long: "Open long position",
  enter_short: "Open short position",
  get_indicator: "Read indicator values",
  register_indicator: "Register custom indicators",
  get_open_orders: "View open orders",
};

const UNSUPPORTED_CATEGORY_LABELS: Record<string, string> = {
  social_stream: "Social data",
  news_feed: "News feed",
  sentiment_engine: "Sentiment signals",
  onchain_feed: "On-chain data",
  macro_feed: "Macro data",
};

function formatRatio(value: number | null | undefined): string {
  const num = typeof value === "number" ? value : 0;
  return `${(num * 100).toFixed(1)}%`;
}

function stageLabel(raw: string): string {
  const key = raw.trim();
  return ERROR_STAGE_LABELS[key] ?? key.replace(/_/g, " ");
}

function missingFieldLabel(raw: string): string {
  const key = raw.trim();
  return MISSING_FIELD_LABELS[key] ?? key.replace(/_/g, " ");
}

function unsupportedLabel(raw: string): string {
  return raw.trim().replace(/_/g, " ");
}

function uniqueNonEmpty(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function formatDataSourceLabel(source: string): string {
  const normalized = source.toLowerCase();
  if (normalized.includes("binance") && normalized.includes("ohlcv")) {
    return "Binance candle data (OHLCV)";
  }
  if (normalized.includes("ohlcv")) {
    return "Candle data (OHLCV)";
  }
  return source.trim();
}

function formatIndicatorScopeLabels(scopes: string[]): string[] {
  let supportsTalib = false;
  let supportsCustom = false;
  const fallback: string[] = [];

  for (const scope of scopes) {
    const normalized = scope.toLowerCase();
    if (
      normalized.includes("ta-lib") ||
      normalized.includes("talib") ||
      normalized.includes("builtin")
    ) {
      supportsTalib = true;
    }
    if (normalized.includes("custom") || normalized.includes("register_indicator")) {
      supportsCustom = true;
    }
    if (
      !normalized.includes("ctx.get_indicator") &&
      !normalized.includes("ctx.register_indicator")
    ) {
      fallback.push(scope.trim());
    }
  }

  const labels: string[] = [];
  if (supportsTalib) labels.push("TA-Lib built-in indicators");
  if (supportsCustom) labels.push("Custom indicators in strategy code");
  return uniqueNonEmpty([...labels, ...fallback]);
}

function prettifyMethodName(raw: string): string {
  const value = raw.trim().replace(/_/g, " ");
  if (!value) return "";
  return value.slice(0, 1).toUpperCase() + value.slice(1);
}

function formatContextMethodLabels(methods: string[]): string[] {
  const expanded = methods.flatMap((method) =>
    method
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
  return uniqueNonEmpty(
    expanded.map((method) => CONTEXT_METHOD_LABELS[method] ?? prettifyMethodName(method)),
  );
}

function formatUnsupportedCategoryLabel(category: string): string {
  const normalized = category.trim();
  return UNSUPPORTED_CATEGORY_LABELS[normalized] ?? normalized.replace(/_/g, " ");
}

function TopList({
  title,
  items,
  labelFormatter,
}: {
  title: string;
  items: CountItem[];
  labelFormatter?: (value: string) => string;
}) {
  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
      <h2 className="text-sm font-semibold text-[#d1d4dc]">{title}</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-xs text-[#868993]">No aggregated data available.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li key={`${title}-${item.name}`} className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-xs text-[#9aa0ad]">
                {labelFormatter ? labelFormatter(item.name) : item.name}
              </span>
              <span className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-0.5 text-xs text-[#d1d4dc]">
                {item.count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AdminPage() {
  const [summary, setSummary] = useState<StrategyQualitySummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [capabilities, setCapabilities] = useState<StrategyCapabilitiesResponse | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    getStrategyQualitySummary(7)
      .then(setSummary)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    getStrategyCapabilities()
      .then(setCapabilities)
      .catch((e) => setCapabilityError(String(e)));
  }, []);

  const dataSourceLabels = capabilities
    ? uniqueNonEmpty(capabilities.supported_data_sources.map(formatDataSourceLabel))
    : [];
  const indicatorLabels = capabilities
    ? formatIndicatorScopeLabels(capabilities.supported_indicator_scopes)
    : [];
  const contextMethodLabels = capabilities
    ? formatContextMethodLabels(capabilities.supported_context_methods)
    : [];
  const unsupportedLabels = capabilities
    ? uniqueNonEmpty(capabilities.unsupported_categories.map(formatUnsupportedCategoryLabel))
    : [];

  return (
    <main className="w-full px-6 py-10">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">Admin</h1>
          <p className="mt-1 text-xs text-[#868993]">
            Strategy quality metrics for the last {summary?.window_days ?? 7} days
          </p>
        </div>
        <button
          className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-sm text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#252936] disabled:opacity-60"
          disabled={loading}
          onClick={refresh}
          type="button"
        >
          Refresh
        </button>
      </div>

      {error ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          Failed to load quality metrics: {error}
        </p>
      ) : null}

      {!summary && loading ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
          Loading quality metrics...
        </div>
      ) : null}

      {summary ? (
        <>
          <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Total Requests</div>
              <div className="mt-1 text-2xl font-semibold text-[#d1d4dc]">{summary.total_requests}</div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Generation Success Rate</div>
              <div className="mt-1 text-2xl font-semibold text-[#26a69a]">
                {formatRatio(summary.generation_success_rate)}
              </div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Clarification Rate</div>
              <div className="mt-1 text-2xl font-semibold text-[#2962ff]">
                {formatRatio(summary.clarification_rate)}
              </div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Unsupported Rate</div>
              <div className="mt-1 text-2xl font-semibold text-[#f9a825]">
                {formatRatio(summary.unsupported_rate)}
              </div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Auto-Fix Rate</div>
              <div className="mt-1 text-2xl font-semibold text-[#d1d4dc]">
                {formatRatio(summary.auto_repair_rate)}
              </div>
            </div>
          </section>

          <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Generation Requests</div>
              <div className="mt-1 text-lg font-semibold text-[#d1d4dc]">{summary.generate_requests}</div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Intake-Only Requests</div>
              <div className="mt-1 text-lg font-semibold text-[#d1d4dc]">{summary.intake_only_requests}</div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Successful Generations</div>
              <div className="mt-1 text-lg font-semibold text-[#26a69a]">
                {summary.generation_success_count}
              </div>
            </div>
            <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
              <div className="text-xs text-[#868993]">Failed Generations</div>
              <div className="mt-1 text-lg font-semibold text-[#ef5350]">
                {summary.generation_failure_count}
              </div>
            </div>
          </section>

          <section className="mt-6 grid gap-3 lg:grid-cols-3">
            <TopList
              title="Most Common Missing Inputs"
              items={summary.top_missing_fields}
              labelFormatter={missingFieldLabel}
            />
            <TopList
              title="Most Common Unsupported Requests"
              items={summary.top_unsupported_requirements}
              labelFormatter={unsupportedLabel}
            />
            <TopList
              title="Top Failure Stages"
              items={summary.top_error_stages}
              labelFormatter={stageLabel}
            />
          </section>
        </>
      ) : null}

      <section className="mt-8 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
        <h2 className="text-sm font-semibold text-[#d1d4dc]">Current Strategy Generation Scope</h2>
        {capabilityError ? (
          <p className="mt-2 text-xs text-[#ef5350]">
            Failed to load capability info: {capabilityError}
          </p>
        ) : capabilities ? (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <h3 className="text-xs font-semibold text-[#d1d4dc]">Market Data</h3>
              <p className="mt-1 text-xs text-[#868993]">
                Strategies are generated using the data sources below.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {dataSourceLabels.length > 0 ? (
                  dataSourceLabels.map((item) => (
                    <span
                      key={`data-${item}`}
                      className="rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1 text-xs text-[#9aa0ad]"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <p className="text-xs text-[#868993]">Loading available data sources...</p>
                )}
              </div>
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <h3 className="text-xs font-semibold text-[#d1d4dc]">Indicator Support</h3>
              <p className="mt-1 text-xs text-[#868993]">
                You can use both built-in and custom indicators.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {indicatorLabels.length > 0 ? (
                  indicatorLabels.map((item) => (
                    <span
                      key={`indicator-${item}`}
                      className="rounded border border-[#2962ff]/30 bg-[#0f1b3a] px-2 py-1 text-xs text-[#8fa8ff]"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <p className="text-xs text-[#868993]">Loading indicator capabilities...</p>
                )}
              </div>
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3 md:col-span-2">
              <h3 className="text-xs font-semibold text-[#d1d4dc]">Execution Controls</h3>
              <p className="mt-1 text-xs text-[#868993]">
                These controls are available for entries, exits, and position management.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {contextMethodLabels.length > 0 ? (
                  contextMethodLabels.map((item) => (
                    <span
                      key={`method-${item}`}
                      className="rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1 text-xs text-[#9aa0ad]"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <p className="text-xs text-[#868993]">Loading execution controls...</p>
                )}
              </div>
            </div>
            {unsupportedLabels.length > 0 ? (
              <div className="rounded border border-[#f9a825]/30 bg-[#2b2417] p-3 md:col-span-2">
                <h3 className="text-xs font-semibold text-[#f9a825]">Currently Unsupported</h3>
                <p className="mt-1 text-xs text-[#d7b36a]">
                  Items requiring external integrations are outside the current generation scope.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {unsupportedLabels.map((item) => (
                    <span
                      key={`unsupported-${item}`}
                      className="rounded border border-[#f9a825]/40 bg-[#2f2718] px-2 py-1 text-xs text-[#f7c65e]"
                    >
                      {item}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="mt-2 text-xs text-[#868993]">Loading capability info...</p>
        )}
      </section>
    </main>
  );
}
