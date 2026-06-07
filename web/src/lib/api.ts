import type {
  BillingStatus,
  BinanceAccountSummary,
  BinanceCredential,
  BinanceCredentialEnv,
  BinanceKeysStatus,
  CheckoutResponse,
  CreateSubAccountInput,
  DeleteAllResponse,
  DeleteResponse,
  Job,
  JobCounts,
  JobEvent,
  JobPolicyCheckResponse,
  JobStatus,
  JobSummary,
  JobType,
  Order,
  PortalResponse,
  QuickBacktestRequest,
  QuickBacktestResponse,
  StopAllResponse,
  StrategyAllocation,
  StrategyCapabilitiesResponse,
  StrategyContentResponse,
  StrategyGenerationResponse,
  StrategyIntakeResponse,
  StrategyInfo,
  StrategyQualitySummaryResponse,
  StrategySaveResponse,
  StrategyChatSessionRecord,
  StrategyChatSessionSummary,
  StrategyParamsApplyResponse,
  StrategyParamsExtractResponse,
  StrategySyntaxCheckResponse,
  Trade,
  UpdateWalletKeysInput,
  UserProfile,
  AdminUsersResponse,
  AutoSweepSettings,
  AutoSweepSettingsInput,
  WalletAccount,
  WalletAccountStatus,
  WalletOverview,
  WalletSyncSummary,
  WalletTransferRecord,
  LivePositionsResponse,
} from "@/lib/types";

const CHAT_USER_ID_STORAGE_KEY = "llmtrader.chat_user_id";

function redirectToAuthOn401(): never {
  if (typeof window === "undefined") {
    throw new Error("Session expired");
  }
  const returnPath = window.location.pathname === "/auth" ? "/dashboard" : window.location.pathname + window.location.search;
  const params = new URLSearchParams();
  params.set("returnUrl", returnPath);
  params.set("reason", "session_expired");
  window.location.href = `/auth?${params.toString()}`;
  throw new Error("Session expired");
}

function parseSupabaseUserId(value: string): string | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const root = parsed as Record<string, unknown>;
    const directUser = root.user;
    if (directUser && typeof directUser === "object") {
      const directId = (directUser as Record<string, unknown>).id;
      if (typeof directId === "string" && directId.trim()) return directId.trim();
    }
    const currentSession = root.currentSession;
    if (!currentSession || typeof currentSession !== "object") return null;
    const user = (currentSession as Record<string, unknown>).user;
    if (!user || typeof user !== "object") return null;
    const id = (user as Record<string, unknown>).id;
    return typeof id === "string" && id.trim() ? id.trim() : null;
  } catch {
    return null;
  }
}

function getChatUserId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const explicit = window.localStorage.getItem(CHAT_USER_ID_STORAGE_KEY);
    if (explicit && explicit.trim()) {
      return explicit.trim();
    }
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);
      if (!key) continue;
      if (!key.startsWith("sb-") || !key.endsWith("-auth-token")) continue;
      const raw = window.localStorage.getItem(key);
      if (!raw) continue;
      const extracted = parseSupabaseUserId(raw);
      if (extracted) {
        return extracted;
      }
    }
  } catch {
    return null;
  }
  return null;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const chatUserId = getChatUserId();
  const headers = new Headers(init?.headers);
  headers.set("content-type", "application/json");
  if (chatUserId) {
    headers.set("x-chat-user-id", chatUserId);
  }
  const res = await fetch(path, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 401) {
      redirectToAuthOn401();
    }
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const text = await res.text();
  try {
    return JSON.parse(text) as T;
  } catch (e) {
    throw new Error(
      `JSON parse error at ${path}: ${
        e instanceof SyntaxError ? e.message : String(e)
      } (${text.length} bytes)`,
    );
  }
}

export async function listStrategies(): Promise<StrategyInfo[]> {
  return json<StrategyInfo[]>("/api/backend/api/strategies");
}

export async function deleteStrategy(path: string): Promise<DeleteResponse> {
  const params = new URLSearchParams();
  params.set("path", path);
  return json<DeleteResponse>(`/api/backend/api/strategies?${params.toString()}`, {
    method: "DELETE",
  });
}

export async function getStrategyContent(path: string): Promise<StrategyContentResponse> {
  const params = new URLSearchParams();
  params.set("path", path);
  return json<StrategyContentResponse>(`/api/backend/api/strategies/content?${params.toString()}`);
}

export async function intakeStrategy(
  userPrompt: string,
  messages?: { role: string; content: string }[],
): Promise<StrategyIntakeResponse> {
  const body: Record<string, unknown> = {
    user_prompt: userPrompt,
  };
  if (messages && messages.length > 0) {
    body.messages = messages;
  }
  return json<StrategyIntakeResponse>("/api/backend/api/strategies/intake", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getStrategyCapabilities(): Promise<StrategyCapabilitiesResponse> {
  return json<StrategyCapabilitiesResponse>("/api/backend/api/strategies/capabilities");
}

export async function getStrategyQualitySummary(days = 7): Promise<StrategyQualitySummaryResponse> {
  return json<StrategyQualitySummaryResponse>(
    `/api/backend/api/strategies/quality/summary?days=${encodeURIComponent(String(days))}`,
  );
}

export async function getAdminUsers(): Promise<AdminUsersResponse> {
  return json<AdminUsersResponse>("/api/backend/api/admin/users");
}

export async function deleteAdminUser(userId: string): Promise<{ deleted: boolean; user_id: string }> {
  return json<{ deleted: boolean; user_id: string }>(
    `/api/backend/api/admin/users/${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
}

export async function generateStrategy(
  userPrompt: string,
  strategyName?: string,
  messages?: { role: string; content: string }[],
): Promise<StrategyGenerationResponse> {
  const body: Record<string, unknown> = {
    user_prompt: userPrompt,
    strategy_name: strategyName?.trim() ? strategyName.trim() : undefined,
  };
  if (messages && messages.length > 0) {
    body.messages = messages;
  }
  return json<StrategyGenerationResponse>("/api/backend/api/strategies/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function saveStrategy(
  code: string,
  strategyName?: string,
): Promise<StrategySaveResponse> {
  return json<StrategySaveResponse>("/api/backend/api/strategies/save", {
    method: "POST",
    body: JSON.stringify({
      code,
      strategy_name: strategyName?.trim() ? strategyName.trim() : undefined,
    }),
  });
}

export async function validateStrategySyntax(
  code: string,
): Promise<StrategySyntaxCheckResponse> {
  return json<StrategySyntaxCheckResponse>("/api/backend/api/strategies/validate-syntax", {
    method: "POST",
    body: JSON.stringify({
      code,
    }),
  });
}

export async function extractStrategyParams(code: string): Promise<StrategyParamsExtractResponse> {
  return json<StrategyParamsExtractResponse>("/api/backend/api/strategies/params/extract", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function applyStrategyParams(
  code: string,
  param_values: Record<string, unknown>,
): Promise<StrategyParamsApplyResponse> {
  return json<StrategyParamsApplyResponse>("/api/backend/api/strategies/params/apply", {
    method: "POST",
    body: JSON.stringify({ code, param_values }),
  });
}

export async function quickBacktest(
  params: QuickBacktestRequest,
): Promise<QuickBacktestResponse> {
  return json<QuickBacktestResponse>("/api/backend/api/strategies/backtest/quick", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function strategyChat(
  code: string,
  summary: string | null,
  messages: { role: string; content: string }[],
): Promise<{ content: string }> {
  return json<{ content: string }>("/api/backend/api/strategies/chat", {
    method: "POST",
    body: JSON.stringify({
      code,
      summary: summary ?? undefined,
      messages,
    }),
  });
}

export type StrategyChatStreamCallbacks = {
  onToken: (token: string) => void;
  onRefusalReplace?: (message: string) => void;
  onDone: (payload: { error?: string }) => void;
};

export async function strategyChatStream(
  code: string,
  summary: string | null,
  messages: { role: string; content: string }[],
  callbacks: StrategyChatStreamCallbacks,
): Promise<void> {
  const FIRST_EVENT_TIMEOUT_MS = 180_000;
  const EVENT_GAP_TIMEOUT_MS = 300_000;

  const chatUserId = getChatUserId();
  const headers = new Headers();
  headers.set("content-type", "application/json");
  if (chatUserId) {
    headers.set("x-chat-user-id", chatUserId);
  }
  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  let settled = false;
  let sawEvent = false;
  let stallTimer: ReturnType<typeof setTimeout> | null = null;

  const clearStallTimer = () => {
    if (stallTimer) {
      clearTimeout(stallTimer);
      stallTimer = null;
    }
  };

  const doneWith = (payload: { error?: string }) => {
    if (settled) return;
    settled = true;
    clearStallTimer();
    callbacks.onDone(payload);
  };

  const timeoutMessage = () =>
    sawEvent
      ? "Stream response was interrupted. Please try again later."
      : "Generation server is slow to respond. Please try again later.";

  const armStallTimer = (ms: number) => {
    clearStallTimer();
    stallTimer = setTimeout(() => {
      controller.abort();
      doneWith({ error: timeoutMessage() });
    }, ms);
  };

  armStallTimer(FIRST_EVENT_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch("/api/backend/api/strategies/chat/stream", {
      method: "POST",
      headers,
      body: JSON.stringify({
        code,
        summary: summary ?? undefined,
        messages,
      }),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    doneWith({ error: controller.signal.aborted ? timeoutMessage() : String(e) });
    return;
  }

  if (!res.ok) {
    if (res.status === 401) {
      redirectToAuthOn401();
    }
    const text = await res.text().catch(() => "");
    doneWith({ error: `${res.status} ${res.statusText}: ${text}` });
    return;
  }
  reader = res.body?.getReader() ?? null;
  if (!reader) {
    doneWith({ error: "No response body" });
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      armStallTimer(EVENT_GAP_TIMEOUT_MS);
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";
      for (const event of events) {
        const dataLines = event
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart());
        if (dataLines.length === 0) continue;
        sawEvent = true;
        armStallTimer(EVENT_GAP_TIMEOUT_MS);
        try {
          const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
          if (typeof data.token === "string") {
            callbacks.onToken(data.token);
          }
          if (data.done === true) {
            doneWith({});
            return;
          }
          if (typeof data.error === "string") {
            doneWith({ error: data.error });
            return;
          }
        } catch {
          // skip malformed event payload
        }
      }
    }
    doneWith({ error: "Stream ended without done" });
  } catch (e) {
    doneWith({ error: controller.signal.aborted ? timeoutMessage() : String(e) });
  } finally {
    clearStallTimer();
    reader.releaseLock();
  }
}

export async function listStrategyChatSessions(): Promise<StrategyChatSessionRecord[]> {
  return json<StrategyChatSessionRecord[]>("/api/backend/api/strategies/chat/sessions?limit=200");
}

/** Lightweight session list — metadata only, no data payload. */
export async function listStrategyChatSessionSummaries(): Promise<StrategyChatSessionSummary[]> {
  return json<StrategyChatSessionSummary[]>("/api/backend/api/strategies/chat/sessions/list?limit=200");
}

export async function getStrategyChatSession(sessionId: string): Promise<StrategyChatSessionRecord> {
  return json<StrategyChatSessionRecord>(
    `/api/backend/api/strategies/chat/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export async function upsertStrategyChatSession(
  sessionId: string,
  payload: { title?: string; data: Record<string, unknown> },
): Promise<StrategyChatSessionRecord> {
  return json<StrategyChatSessionRecord>(
    `/api/backend/api/strategies/chat/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export async function deleteStrategyChatSession(sessionId: string): Promise<DeleteResponse> {
  return json<DeleteResponse>(
    `/api/backend/api/strategies/chat/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

export type GenerateStreamCallbacks = {
  onToken: (token: string) => void;
  onPhase?: (phase: string, detail?: { progress?: number; attempt?: number; max_attempts?: number }) => void;
  onIntent?: (intent: string) => void;
  onPlanPreview?: (preview: string, planSpec: Record<string, unknown>) => void;
  onDone: (payload: {
    code?: string;
    summary?: string | null;
    backtest_ok?: boolean;
    repaired?: boolean;
    repair_attempts?: number;
    rejected?: boolean;
    error?: string;
  }) => void;
};

export async function generateStrategyStream(
  userPrompt: string,
  callbacks: GenerateStreamCallbacks,
  strategyName?: string,
  messages?: { role: string; content: string }[],
  confirmedPlan?: Record<string, unknown>,
): Promise<void> {
  const FIRST_EVENT_TIMEOUT_MS = 180_000;
  const EVENT_GAP_TIMEOUT_MS = 300_000;

  const body: Record<string, unknown> = {
    user_prompt: userPrompt,
    strategy_name: strategyName?.trim() ? strategyName.trim() : undefined,
  };
  if (messages && messages.length > 0) {
    body.messages = messages;
  }
  if (confirmedPlan) {
    body.confirmed_plan = confirmedPlan;
  }
  const chatUserId = getChatUserId();
  const headers = new Headers();
  headers.set("content-type", "application/json");
  if (chatUserId) {
    headers.set("x-chat-user-id", chatUserId);
  }
  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  let settled = false;
  let sawEvent = false;
  let stallTimer: ReturnType<typeof setTimeout> | null = null;
  const accumulatedTokens: string[] = [];

  const clearStallTimer = () => {
    if (stallTimer) {
      clearTimeout(stallTimer);
      stallTimer = null;
    }
  };

  const doneWith = (payload: {
    code?: string;
    summary?: string | null;
    backtest_ok?: boolean;
    repaired?: boolean;
    repair_attempts?: number;
    rejected?: boolean;
    error?: string;
  }) => {
    if (settled) return;
    settled = true;
    clearStallTimer();
    // If there's an error but we have partial output, include it
    if (payload.error && !payload.code && accumulatedTokens.length > 0) {
      const partialCode = accumulatedTokens.join("");
      if (partialCode.trim().length > 50) {
        payload.code = partialCode;
        payload.error = `${payload.error} (partial output preserved)`;
      }
    }
    callbacks.onDone(payload);
  };

  const timeoutMessage = () =>
    sawEvent
      ? "Stream response was interrupted. Please try again later."
      : "Generation server is slow to respond. Please try again later.";

  const armStallTimer = (ms: number) => {
    clearStallTimer();
    stallTimer = setTimeout(() => {
      controller.abort();
      doneWith({ error: timeoutMessage() });
    }, ms);
  };

  armStallTimer(FIRST_EVENT_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch("/api/backend/api/strategies/generate/stream", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    doneWith({ error: controller.signal.aborted ? timeoutMessage() : String(e) });
    return;
  }

  if (!res.ok) {
    if (res.status === 401) {
      redirectToAuthOn401();
    }
    const text = await res.text().catch(() => "");
    doneWith({ error: `${res.status} ${res.statusText}: ${text}` });
    return;
  }
  reader = res.body?.getReader() ?? null;
  if (!reader) {
    doneWith({ error: "No response body" });
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      armStallTimer(EVENT_GAP_TIMEOUT_MS);
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";
      for (const event of events) {
        const dataLines = event
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart());
        if (dataLines.length === 0) continue;
        sawEvent = true;
        armStallTimer(EVENT_GAP_TIMEOUT_MS);
        try {
          const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
          if (typeof data.phase === "string" && callbacks.onPhase) {
            callbacks.onPhase(data.phase, {
              progress: typeof data.progress === "number" ? data.progress : undefined,
              attempt: typeof data.attempt === "number" ? data.attempt : undefined,
              max_attempts: typeof data.max_attempts === "number" ? data.max_attempts : undefined,
            });
          }
          if (typeof data.intent === "string" && callbacks.onIntent) {
            callbacks.onIntent(data.intent);
            return;
          }
          if (typeof data.plan_preview === "string" && callbacks.onPlanPreview) {
            const planSpec = (data.plan_spec as Record<string, unknown>) ?? {};
            callbacks.onPlanPreview(data.plan_preview, planSpec);
            return;
          }
          if (typeof data.token === "string") {
            accumulatedTokens.push(data.token);
            callbacks.onToken(data.token);
          }
          if (data.done === true) {
            doneWith({
              code: typeof data.code === "string" ? data.code : undefined,
              summary:
                data.summary !== undefined && data.summary !== null
                  ? String(data.summary)
                  : null,
              backtest_ok: data.backtest_ok === true,
              repaired: data.repaired === true,
              repair_attempts:
                typeof data.repair_attempts === "number" ? Number(data.repair_attempts) : 0,
              rejected: data.rejected === true,
              error: typeof data.error === "string" ? data.error : undefined,
            });
            return;
          }
          if (typeof data.error === "string") {
            doneWith({ error: data.error });
            return;
          }
        } catch {
          // skip malformed event payload
        }
      }
    }
    doneWith({ error: "Stream ended without done" });
  } catch (e) {
    doneWith({ error: controller.signal.aborted ? timeoutMessage() : String(e) });
  } finally {
    clearStallTimer();
    reader.releaseLock();
  }
}

export async function listJobs(options?: {
  type?: JobType;
  limit?: number;
  status?: JobStatus;
}): Promise<Job[]> {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 50));
  if (options?.type) {
    params.set("type", options.type);
  }
  if (options?.status) {
    params.set("status", options.status);
  }
  return json<Job[]>(`/api/backend/api/jobs?${params.toString()}`);
}

/** Lightweight job list — excludes heavy trades data from result. */
export async function listJobSummaries(options?: {
  type?: JobType;
  limit?: number;
  status?: JobStatus;
}): Promise<JobSummary[]> {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 50));
  if (options?.type) {
    params.set("type", options.type);
  }
  if (options?.status) {
    params.set("status", options.status);
  }
  return json<JobSummary[]>(`/api/backend/api/jobs/list?${params.toString()}`);
}

export async function getJobCounts(): Promise<JobCounts> {
  return json<JobCounts>("/api/backend/api/jobs/counts");
}

export async function getBinanceAccountSummary(): Promise<BinanceAccountSummary> {
  return json<BinanceAccountSummary>("/api/backend/api/binance/account/summary");
}

export async function getPortfolioSummary(): Promise<
  import("@/lib/types").PortfolioSummaryResponse
> {
  return json("/api/backend/api/portfolio/summary");
}

export async function getStrategyModuleCatalog(): Promise<
  import("@/lib/types").StrategyModuleCatalogResponse
> {
  return json("/api/backend/api/strategy-modules");
}

export async function getFundingArbStatus(): Promise<
  import("@/lib/types").FundingArbitrageStatusResponse
> {
  return json("/api/backend/api/funding-arb/status");
}

export async function startFundingArb(
  params: import("@/lib/types").FundingArbitrageParams,
): Promise<import("@/lib/types").FundingArbitrageStatusResponse> {
  return json("/api/backend/api/funding-arb/start", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function stopFundingArb(): Promise<
  import("@/lib/types").FundingArbitrageStatusResponse
> {
  return json("/api/backend/api/funding-arb/stop", { method: "POST" });
}

export async function getFundingScreener(
  topN = 20,
  env: "mainnet" | "testnet" = "mainnet",
): Promise<import("@/lib/types").FundingScreenerResponse> {
  return json(`/api/backend/api/funding-arb/screener?top_n=${topN}&env=${env}`);
}

export async function getFundingSymbolDetail(
  symbol: string,
): Promise<import("@/lib/types").FundingSymbolDetailResponse> {
  const s = encodeURIComponent(symbol.trim().toUpperCase());
  return json(`/api/backend/api/funding-arb/symbol-detail?symbol=${s}`);
}

export async function listFuturesSymbols(): Promise<string[]> {
  return json<string[]>("/api/backend/api/binance/futures/symbols");
}

export async function getJob(jobId: string): Promise<Job> {
  return json<Job>(`/api/backend/api/jobs/${jobId}`);
}

export async function stopJob(jobId: string): Promise<{ ok: boolean }> {
  return json<{ ok: boolean }>(`/api/backend/api/jobs/${jobId}/stop`, { method: "POST" });
}

export async function stopAllJobs(type?: JobType): Promise<StopAllResponse> {
  const suffix = type ? `?type=${encodeURIComponent(type)}` : "";
  return json<StopAllResponse>(`/api/backend/api/jobs/stop-all${suffix}`, { method: "POST" });
}

export async function deleteJob(jobId: string): Promise<DeleteResponse> {
  return json<DeleteResponse>(`/api/backend/api/jobs/${jobId}`, { method: "DELETE" });
}

export async function deleteAllJobs(type?: JobType): Promise<DeleteAllResponse> {
  const suffix = type ? `?type=${encodeURIComponent(type)}` : "";
  return json<DeleteAllResponse>(`/api/backend/api/jobs${suffix}`, { method: "DELETE" });
}

export async function listEvents(jobId: string, afterEventId = 0): Promise<JobEvent[]> {
  return json<JobEvent[]>(
    `/api/backend/api/jobs/${jobId}/events?after_event_id=${afterEventId}&limit=200`,
  );
}

export async function listOrders(jobId: string): Promise<Order[]> {
  return json<Order[]>(`/api/backend/api/jobs/${jobId}/orders`);
}

export async function listTrades(jobId: string): Promise<Trade[]> {
  return json<Trade[]>(`/api/backend/api/jobs/${jobId}/trades`);
}

export interface BacktestAnalysis {
  strengths?: string[];
  weaknesses?: string[];
  suggestions?: string[];
  parameter_changes?: Record<string, { current: unknown; suggested: unknown; reason: string }>;
  risk_assessment?: string;
  expected_impact?: string;
  raw_analysis?: string;
}

export async function analyzeBacktestResults(
  code: string,
  backtestResults: string,
  summary?: string | null,
): Promise<BacktestAnalysis> {
  const body: Record<string, unknown> = {
    code,
    backtest_results: backtestResults,
  };
  if (summary) {
    body.summary = summary;
  }
  const res = await fetch("/api/backend/api/strategies/analyze", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Analyze failed: ${res.status}`);
  }
  const data = await res.json();
  return data.analysis as BacktestAnalysis;
}

export async function listTradesBatch(jobIds: string[]): Promise<Record<string, Trade[]>> {
  if (jobIds.length === 0) return {};
  const params = new URLSearchParams();
  params.set("job_ids", jobIds.join(","));
  return json<Record<string, Trade[]>>(`/api/backend/api/jobs/trades/batch?${params.toString()}`);
}

export async function createJob(body: {
  type: "BACKTEST" | "LIVE";
  strategy_path: string;
  config: Record<string, unknown>;
}): Promise<Job> {
  return json<Job>("/api/backend/api/jobs", { method: "POST", body: JSON.stringify(body) });
}

export async function preflightJob(body: {
  type: "BACKTEST" | "LIVE";
  config: Record<string, unknown>;
}): Promise<JobPolicyCheckResponse> {
  return json<JobPolicyCheckResponse>("/api/backend/api/jobs/preflight", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getUserProfile(): Promise<UserProfile> {
  return json<UserProfile>("/api/backend/api/me");
}

export async function listBinanceCredentials(): Promise<BinanceCredential[]> {
  return json<BinanceCredential[]>("/api/backend/api/me/binance-keys");
}

export async function setBinanceCredential(
  env: string,
  body: { api_key: string; api_secret: string; ip_whitelist?: string[] | string },
): Promise<BinanceCredential> {
  return json<BinanceCredential>(`/api/backend/api/me/binance-keys/${env}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteBinanceCredential(env: string): Promise<{ ok: boolean }> {
  return json(`/api/backend/api/me/binance-keys/${env}`, { method: "DELETE" });
}

// ── Sub-account wallet topology ─────────────────────────────────────────

export async function listWalletAccounts(
  env?: BinanceCredentialEnv,
): Promise<WalletAccount[]> {
  const qs = env ? `?env=${encodeURIComponent(env)}` : "";
  return json<WalletAccount[]>(`/api/backend/api/me/wallets${qs}`);
}

export async function getWalletAccount(walletId: string): Promise<WalletAccount> {
  return json<WalletAccount>(
    `/api/backend/api/me/wallets/${encodeURIComponent(walletId)}`,
  );
}

/**
 * @deprecated Sub-account creation now happens on Binance directly.
 * The backend route returns 410 Gone; the new Settings → Sub account
 * page uses {@link syncWalletAccounts} + {@link listWalletAccounts}
 * instead. Kept here only so legacy imports compile while we tree-shake.
 */
export async function createSubAccount(
  body: CreateSubAccountInput,
): Promise<WalletAccount> {
  return json<WalletAccount>("/api/backend/api/me/wallets/subaccounts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateWalletKeys(
  walletId: string,
  body: UpdateWalletKeysInput,
): Promise<WalletAccount> {
  return json<WalletAccount>(
    `/api/backend/api/me/wallets/${encodeURIComponent(walletId)}/keys`,
    { method: "PUT", body: JSON.stringify(body) },
  );
}

export async function updateWalletMeta(
  walletId: string,
  body: {
    purpose?: string;
    enabled_wallets?: Record<string, boolean>;
    ip_whitelist?: string[];
  },
): Promise<WalletAccount> {
  return json<WalletAccount>(
    `/api/backend/api/me/wallets/${encodeURIComponent(walletId)}/meta`,
    { method: "PUT", body: JSON.stringify(body) },
  );
}

export async function updateWalletStatus(
  walletId: string,
  status: WalletAccountStatus,
): Promise<WalletAccount> {
  return json<WalletAccount>(
    `/api/backend/api/me/wallets/${encodeURIComponent(walletId)}/status`,
    { method: "PUT", body: JSON.stringify({ status }) },
  );
}

export async function deleteWalletAccount(
  walletId: string,
): Promise<void> {
  await fetch(
    `/api/backend/api/me/wallets/${encodeURIComponent(walletId)}`,
    { method: "DELETE", cache: "no-store" },
  );
}

export async function syncWalletAccounts(
  env: BinanceCredentialEnv = "mainnet",
): Promise<WalletSyncSummary> {
  return json<WalletSyncSummary>(
    `/api/backend/api/me/wallets/sync?env=${encodeURIComponent(env)}`,
    { method: "POST" },
  );
}

export async function getWalletSyncStatus(): Promise<WalletSyncSummary | null> {
  return json<WalletSyncSummary | null>(
    `/api/backend/api/me/wallets/sync/status`,
  );
}

export async function getJobAllocation(
  jobId: string,
): Promise<StrategyAllocation | null> {
  return json<StrategyAllocation | null>(
    `/api/backend/api/me/jobs/${encodeURIComponent(jobId)}/allocation`,
  );
}

export async function upsertJobAllocation(
  jobId: string,
  body: {
    wallet_account_id: string;
    allocated_usdt: number;
    allocation_mode?: string;
    max_drawdown_pct?: number | null;
  },
): Promise<StrategyAllocation> {
  return json<StrategyAllocation>(
    `/api/backend/api/me/jobs/${encodeURIComponent(jobId)}/allocation`,
    { method: "PUT", body: JSON.stringify(body) },
  );
}

export async function deleteJobAllocation(jobId: string): Promise<void> {
  await fetch(
    `/api/backend/api/me/jobs/${encodeURIComponent(jobId)}/allocation`,
    { method: "DELETE", cache: "no-store" },
  );
}

export async function listWalletTransfers(
  limit = 50,
): Promise<WalletTransferRecord[]> {
  return json<WalletTransferRecord[]>(
    `/api/backend/api/me/wallet-transfers?limit=${encodeURIComponent(String(limit))}`,
  );
}

/**
 * Backward-compatible helper: reports whether the user has ANY Binance
 * credential configured. Derived from the new per-env credential list endpoint.
 */
export async function getBinanceKeysStatus(): Promise<BinanceKeysStatus> {
  const creds = await listBinanceCredentials();
  return { configured: creds.some((c) => c.configured) };
}

export async function getAutoSweepSettings(): Promise<AutoSweepSettings> {
  return json<AutoSweepSettings>("/api/backend/api/me/auto-sweep");
}

export async function setAutoSweepSettings(
  body: AutoSweepSettingsInput,
): Promise<AutoSweepSettings> {
  return json<AutoSweepSettings>("/api/backend/api/me/auto-sweep", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function getWalletOverview(): Promise<WalletOverview> {
  return json<WalletOverview>("/api/backend/api/binance/wallet/overview");
}

export async function getLivePositions(): Promise<LivePositionsResponse> {
  return json<LivePositionsResponse>("/api/backend/api/live/positions");
}

export async function getBillingStatus(): Promise<BillingStatus> {
  return json<BillingStatus>("/api/backend/api/billing/status");
}

export async function createCheckoutSession(plan: string): Promise<CheckoutResponse> {
  return json<CheckoutResponse>("/api/backend/api/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ plan }),
  });
}

export async function createBillingPortalSession(): Promise<PortalResponse> {
  return json<PortalResponse>("/api/backend/api/billing/portal", { method: "POST" });
}

export async function testLlmEndpoint(input: string): Promise<{ output: string }> {
  return json<{ output: string }>("/api/backend/api/llm-test", {
    method: "POST",
    body: JSON.stringify({ input: (input || "").trim() || "Hello" }),
  });
}

// ---------------------------------------------------------------------------
// Upbit / Bridge transfer API
// ---------------------------------------------------------------------------

export interface UpbitBalanceItem {
  currency: string;
  balance: number;
  locked: number;
}

export interface UpbitAccount {
  balances: UpbitBalanceItem[];
  krw_usdt_price: number;
}

export interface UpbitKeysStatus {
  configured: boolean;
  access_key_masked?: string;
}

export interface BridgeTransfer {
  id: string;
  direction: "UPBIT_TO_BINANCE" | "BINANCE_TO_UPBIT";
  status: "PENDING" | "CONVERTING" | "WITHDRAWING" | "CONFIRMING" | "COMPLETED" | "FAILED";
  network: string;
  requested_usdt: number;
  actual_usdt: number | null;
  krw_amount: number | null;
  fee_usdt: number | null;
  src_withdrawal_id: string | null;
  dst_deposit_address: string | null;
  dst_txid: string | null;
  error_message: string | null;
  initiated_at: string;
  completed_at: string | null;
  updated_at: string;
}

export async function getUpbitKeysStatus(): Promise<UpbitKeysStatus> {
  return json<UpbitKeysStatus>("/api/backend/api/me/upbit-keys");
}

export async function setUpbitKeys(body: {
  access_key: string;
  secret_key: string;
}): Promise<{ ok: boolean; access_key_masked: string }> {
  return json("/api/backend/api/me/upbit-keys", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteUpbitKeys(): Promise<{ ok: boolean }> {
  return json("/api/backend/api/me/upbit-keys", { method: "DELETE" });
}

export async function getUpbitAccount(): Promise<UpbitAccount> {
  return json<UpbitAccount>("/api/backend/api/upbit/account");
}

export async function startOnramp(body: {
  usdt_amount: number;
  network?: string;
  convert_from_krw?: boolean;
}): Promise<{ id: string; status: string; withdrawal_uuid: string; deposit_address: string }> {
  return json("/api/backend/api/bridge/onramp", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function startOfframp(body: {
  usdt_amount: number;
  network?: string;
  sell_to_krw?: boolean;
  redeem_from_earn?: boolean;
}): Promise<{ id: string; status: string; withdrawal_id: string; deposit_address: string }> {
  return json("/api/backend/api/bridge/offramp", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listBridgeTransfers(): Promise<{ transfers: BridgeTransfer[] }> {
  return json<{ transfers: BridgeTransfer[] }>("/api/backend/api/bridge/transfers");
}

export async function syncTransferStatus(id: string): Promise<{ id: string; status: string; changed: boolean }> {
  return json(`/api/backend/api/bridge/transfers/${id}/sync`, { method: "POST" });
}
