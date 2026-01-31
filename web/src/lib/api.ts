import type {
  Job,
  JobEvent,
  JobType,
  Order,
  StopAllResponse,
  StrategyGenerationResponse,
  StrategyInfo,
  StrategySaveResponse,
  Trade,
} from "@/lib/types";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
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

export type GenerateStreamCallbacks = {
  onToken: (token: string) => void;
  onDone: (payload: {
    code?: string;
    summary?: string | null;
    backtest_ok?: boolean;
    error?: string;
  }) => void;
};

export async function generateStrategyStream(
  userPrompt: string,
  callbacks: GenerateStreamCallbacks,
  strategyName?: string,
  messages?: { role: string; content: string }[],
): Promise<void> {
  const body: Record<string, unknown> = {
    user_prompt: userPrompt,
    strategy_name: strategyName?.trim() ? strategyName.trim() : undefined,
  };
  if (messages && messages.length > 0) {
    body.messages = messages;
  }
  const res = await fetch("/api/backend/api/strategies/generate/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    callbacks.onDone({ error: `${res.status} ${res.statusText}: ${text}` });
    return;
  }
  const reader = res.body?.getReader();
  if (!reader) {
    callbacks.onDone({ error: "No response body" });
    return;
  }
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const data = JSON.parse(line.slice(6)) as Record<string, unknown>;
          if (typeof data.token === "string") {
            callbacks.onToken(data.token);
          }
          if (data.done === true) {
            callbacks.onDone({
              code: typeof data.code === "string" ? data.code : undefined,
              summary:
                data.summary !== undefined && data.summary !== null
                  ? String(data.summary)
                  : null,
              backtest_ok: data.backtest_ok === true,
              error: typeof data.error === "string" ? data.error : undefined,
            });
            return;
          }
          if (typeof data.error === "string") {
            callbacks.onDone({ error: data.error });
            return;
          }
        } catch {
          // skip malformed line
        }
      }
    }
    callbacks.onDone({ error: "Stream ended without done" });
  } finally {
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
