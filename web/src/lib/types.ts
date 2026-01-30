export type JobType = "BACKTEST" | "LIVE";

export type JobStatus =
  | "PENDING"
  | "RUNNING"
  | "STOP_REQUESTED"
  | "SUCCEEDED"
  | "STOPPED"
  | "FAILED";

export type StrategyInfo = { name: string; path: string };

export type StrategyGenerationResponse = {
  code: string;
  model_used: string | null;
  path: string;
};

export type Job = {
  job_id: string;
  type: JobType;
  status: JobStatus;
  strategy_path: string;
  config: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
};

export type StopAllResponse = {
  stopped_queued: number;
  stop_requested_running: number;
};

export type JobEvent = {
  event_id: number;
  job_id: string;
  ts: string;
  kind: string;
  level: string;
  message: string;
  payload: Record<string, unknown> | null;
};

export type Order = {
  order_id: number;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  quantity: number | null;
  price: number | null;
  executed_qty: number | null;
  avg_price: number | null;
  ts: string;
  raw: Record<string, unknown> | null;
};

export type Trade = {
  trade_id: number;
  symbol: string;
  order_id: number | null;
  quantity: number | null;
  price: number | null;
  realized_pnl: number | null;
  commission: number | null;
  ts: string;
  raw: Record<string, unknown> | null;
};
