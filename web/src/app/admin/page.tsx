"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import useSWR from "swr";
import { getStrategyCapabilities, getStrategyQualitySummary, testLlmEndpoint, getAdminUsers, deleteAdminUser } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type {
  CountItem,
  AdminUserItem,
  AdminUsersResponse,
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
  const router = useRouter();
  const { t } = useI18n();
  const [accessState, setAccessState] = useState<"checking" | "allowed" | "denied">("checking");
  const [activeTab, setActiveTab] = useState<"quality" | "users">("quality");
  const [llmInput, setLlmInput] = useState("Hello");
  const [llmOutput, setLlmOutput] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/session", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) {
          if (active) setAccessState("denied");
          router.replace("/dashboard");
          return;
        }
        const payload = (await res.json()) as { isAdmin?: boolean };
        if (!payload.isAdmin) {
          if (active) setAccessState("denied");
          router.replace("/dashboard");
          return;
        }
        if (active) setAccessState("allowed");
      })
      .catch(() => {
        if (active) setAccessState("denied");
        router.replace("/dashboard");
      });
    return () => {
      active = false;
    };
  }, [router]);

  const isAllowed = accessState === "allowed";

  const { data: summary, error: summaryError, isLoading: loading, mutate: refreshSummary } = useSWR<StrategyQualitySummaryResponse>(
    isAllowed ? "adminQualitySummary" : null,
    () => getStrategyQualitySummary(7),
  );

  const { data: capabilities, error: capabilityErrorObj } = useSWR<StrategyCapabilitiesResponse>(
    isAllowed ? "adminCapabilities" : null,
    () => getStrategyCapabilities(),
  );

  const error = summaryError ? String(summaryError) : null;
  const capabilityError = capabilityErrorObj ? String(capabilityErrorObj) : null;

  const { data: usersData, error: usersErrorObj, isLoading: usersLoading, mutate: refreshUsers } = useSWR<AdminUsersResponse>(
    isAllowed && activeTab === "users" ? "adminUsers" : null,
    () => getAdminUsers(),
  );
  const usersError = usersErrorObj ? String(usersErrorObj) : null;

  async function handleDeleteUser(userId: string, email: string) {
    if (!confirm(`Delete user "${email}"?\nThis cannot be undone.`)) return;
    setDeletingUserId(userId);
    try {
      await deleteAdminUser(userId);
      await refreshUsers();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete user");
    } finally {
      setDeletingUserId(null);
    }
  }

  async function handleTestLlm() {
    setLlmLoading(true);
    setLlmOutput(null);
    setLlmError(null);
    try {
      const res = await testLlmEndpoint(llmInput);
      setLlmOutput(res.output);
    } catch (err: unknown) {
      setLlmError(err instanceof Error ? err.message : t.settings.llmTestFailed);
    } finally {
      setLlmLoading(false);
    }
  }

  if (accessState !== "allowed") {
    return (
      <main className="w-full px-6 py-10">
        <div className="text-[#868993]">Checking admin access...</div>
      </main>
    );
  }

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
        </div>
      </div>

      {/* Tabs */}
      <div className="mt-4 flex gap-1 border-b border-[#2a2e39]">
        <button
          type="button"
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "quality"
              ? "border-b-2 border-[#2962ff] text-[#d1d4dc]"
              : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
          onClick={() => setActiveTab("quality")}
        >
          Strategy Quality
        </button>
        <button
          type="button"
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "users"
              ? "border-b-2 border-[#2962ff] text-[#d1d4dc]"
              : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
          onClick={() => setActiveTab("users")}
        >
          Users {usersData ? `(${usersData.total})` : ""}
        </button>
      </div>

      {/* Users Tab */}
      {activeTab === "users" ? (
        <div className="mt-6">
          <div className="flex items-center justify-between gap-4">
            <p className="text-xs text-[#868993]">
              {usersData ? `${usersData.total} registered users` : "Loading..."}
            </p>
            <button
              className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-sm text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#252936] disabled:opacity-60"
              disabled={usersLoading}
              onClick={() => void refreshUsers()}
              type="button"
            >
              Refresh
            </button>
          </div>
          {usersError ? (
            <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
              Failed to load users: {usersError}
            </p>
          ) : null}
          {usersLoading && !usersData ? (
            <div className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
              Loading users...
            </div>
          ) : usersData ? (
            <div className="mt-4 overflow-x-auto rounded border border-[#2a2e39]">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-[#2a2e39] bg-[#1e222d]">
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]">Email</th>
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]">Display Name</th>
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]">Plan</th>
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]">Verified</th>
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]">Registered</th>
                    <th className="px-4 py-3 text-xs font-semibold text-[#868993]"></th>
                  </tr>
                </thead>
                <tbody>
                  {usersData.users.map((user) => (
                    <tr key={user.user_id} className="border-b border-[#2a2e39] bg-[#131722] hover:bg-[#1e222d]">
                      <td className="px-4 py-3 text-[#d1d4dc]">{user.email}</td>
                      <td className="px-4 py-3 text-[#9aa0ad]">{user.display_name}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded px-2 py-0.5 text-xs font-medium ${
                          user.plan === "free"
                            ? "bg-[#2a2e39] text-[#868993]"
                            : "bg-[#2962ff]/20 text-[#8fa8ff]"
                        }`}>
                          {user.plan}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {user.email_verified ? (
                          <span className="text-[#26a69a]">✓</span>
                        ) : (
                          <span className="text-[#ef5350]">✗</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-[#868993]">
                        {new Date(user.created_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-xs text-[#ef5350] transition-colors hover:bg-[#ef5350]/10 disabled:opacity-40"
                          disabled={deletingUserId === user.user_id}
                          onClick={() => void handleDeleteUser(user.user_id, user.email)}
                        >
                          {deletingUserId === user.user_id ? "Deleting..." : "Delete"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Quality Tab */}
      {activeTab === "quality" ? (
        <>
      <div className="mt-6 flex items-center justify-between gap-4">
        <p className="text-xs text-[#868993]">
          Strategy quality metrics for the last {summary?.window_days ?? 7} days
        </p>
        <button
          className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-sm text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#252936] disabled:opacity-60"
          disabled={loading}
          onClick={() => void refreshSummary()}
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
        <h2 className="text-sm font-semibold text-[#d1d4dc]">{t.settings.llmTest}</h2>
        <p className="mt-1 text-xs text-[#868993]">{t.settings.llmTestDesc}</p>
        <div className="mt-3 space-y-3">
          <input
            id="llm-test-input"
            name="llm-test-input"
            className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
            onChange={(e) => setLlmInput(e.target.value)}
            placeholder={t.settings.llmTestPlaceholder}
            value={llmInput}
          />
          <button
            className="rounded-lg bg-[#2962ff] px-4 py-2 text-sm font-medium text-white hover:bg-[#2962ff]/80 transition-colors disabled:opacity-50"
            disabled={llmLoading}
            onClick={handleTestLlm}
            type="button"
          >
            {llmLoading ? t.settings.llmTesting : t.settings.llmTestSend}
          </button>
          {llmError ? (
            <div className="rounded-lg border border-[#ef5350]/30 bg-[#ef5350]/10 px-4 py-3 text-sm text-[#ef5350]">
              {llmError}
            </div>
          ) : null}
          {llmOutput !== null && !llmError ? (
            <div className="rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm whitespace-pre-wrap text-[#d1d4dc]">
              {llmOutput}
            </div>
          ) : null}
        </div>
      </section>

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
        </>
      ) : null}
    </main>
  );
}
