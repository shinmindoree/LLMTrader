import type { Job, JobEvent, JobType, Order, StopAllResponse, StrategyInfo, Trade } from "@/lib/types";

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
