import type {
  BillingStatus,
  BinanceAccountSummary,
  BinanceKeysStatus,
  CheckoutResponse,
  DeleteAllResponse,
  DeleteResponse,
  Job,
  JobEvent,
  JobPolicyCheckResponse,
  JobType,
  Order,
  PortalResponse,
  StopAllResponse,
  StrategyCapabilitiesResponse,
  StrategyContentResponse,
  StrategyGenerationResponse,
  StrategyIntakeResponse,
  StrategyInfo,
  StrategyQualitySummaryResponse,
  StrategySaveResponse,
  StrategyChatSessionRecord,
  StrategySyntaxCheckResponse,
  Trade,
  UserProfile,
} from "@/lib/types";

const CHAT_USER_ID_STORAGE_KEY = "llmtrader.chat_user_id";

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
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
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

export async function listStrategyChatSessions(): Promise<StrategyChatSessionRecord[]> {
  return json<StrategyChatSessionRecord[]>("/api/backend/api/strategies/chat/sessions?limit=200");
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
  onDone: (payload: {
    code?: string;
    summary?: string | null;
    backtest_ok?: boolean;
    repaired?: boolean;
    repair_attempts?: number;
    error?: string;
  }) => void;
};

export async function generateStrategyStream(
  userPrompt: string,
  callbacks: GenerateStreamCallbacks,
  strategyName?: string,
  messages?: { role: string; content: string }[],
): Promise<void> {
  const FIRST_EVENT_TIMEOUT_MS = 90_000;
  const EVENT_GAP_TIMEOUT_MS = 120_000;

  const body: Record<string, unknown> = {
    user_prompt: userPrompt,
    strategy_name: strategyName?.trim() ? strategyName.trim() : undefined,
  };
  if (messages && messages.length > 0) {
    body.messages = messages;
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
    error?: string;
  }) => {
    if (settled) return;
    settled = true;
    clearStallTimer();
    callbacks.onDone(payload);
  };

  const timeoutMessage = () =>
    sawEvent
      ? "스트림 응답이 중단되었습니다. 잠시 후 다시 시도해주세요."
      : "생성 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.";

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

export async function listJobs(options?: { type?: JobType; limit?: number }): Promise<Job[]> {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 50));
  if (options?.type) {
    params.set("type", options.type);
  }
  return json<Job[]>(`/api/backend/api/jobs?${params.toString()}`);
}

export async function getBinanceAccountSummary(): Promise<BinanceAccountSummary> {
  return json<BinanceAccountSummary>("/api/backend/api/binance/account/summary");
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

export async function getBinanceKeysStatus(): Promise<BinanceKeysStatus> {
  return json<BinanceKeysStatus>("/api/backend/api/me/binance-keys");
}

export async function setBinanceKeys(body: {
  api_key: string;
  api_secret: string;
  base_url?: string;
}): Promise<{ ok: boolean; api_key_masked: string; base_url: string }> {
  return json("/api/backend/api/me/binance-keys", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteBinanceKeys(): Promise<{ ok: boolean }> {
  return json("/api/backend/api/me/binance-keys", { method: "DELETE" });
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
  return json<{ output: string }>("/api/relay/test", {
    method: "POST",
    body: JSON.stringify({ input: (input || "").trim() || "Hello" }),
  });
}
