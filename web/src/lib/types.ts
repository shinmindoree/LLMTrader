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
  path: string | null;
  summary: string | null;
  backtest_ok: boolean;
  repaired: boolean;
  repair_attempts: number;
};

export type StrategyIntakeResponse = {
  intent: "OUT_OF_SCOPE" | "STRATEGY_CREATE" | "STRATEGY_MODIFY" | "STRATEGY_QA";
  status: "READY" | "NEEDS_CLARIFICATION" | "UNSUPPORTED_CAPABILITY" | "OUT_OF_SCOPE";
  user_message: string;
  normalized_spec: {
    symbol: string | null;
    timeframe: string | null;
    entry_logic: string | null;
    exit_logic: string | null;
    risk: Record<string, unknown>;
  } | null;
  missing_fields: string[];
  unsupported_requirements: string[];
  clarification_questions: string[];
  assumptions: string[];
  development_requirements: string[];
};

export type StrategyCapabilitiesResponse = {
  supported_data_sources: string[];
  supported_indicator_scopes: string[];
  supported_context_methods: string[];
  unsupported_categories: string[];
  summary_lines: string[];
};

export type CountItem = {
  name: string;
  count: number;
};

export type StrategyQualitySummaryResponse = {
  window_days: number;
  total_requests: number;
  intake_only_requests: number;
  generate_requests: number;
  generation_success_count: number;
  generation_failure_count: number;
  ready_rate: number;
  clarification_rate: number;
  unsupported_rate: number;
  out_of_scope_rate: number;
  generation_success_rate: number;
  auto_repair_rate: number;
  avg_repair_attempts: number;
  top_missing_fields: CountItem[];
  top_unsupported_requirements: CountItem[];
  top_error_stages: CountItem[];
};

export type StrategySaveResponse = { path: string };

export type StrategySyntaxError = {
  message: string;
  line: number | null;
  column: number | null;
  end_line: number | null;
  end_column: number | null;
};

export type StrategySyntaxCheckResponse = {
  valid: boolean;
  error: StrategySyntaxError | null;
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

export type JobPolicyCheckResponse = {
  ok: boolean;
  blockers: string[];
  warnings: string[];
};

export type StopAllResponse = {
  stopped_queued: number;
  stop_requested_running: number;
};

export type DeleteResponse = {
  ok: boolean;
};

export type DeleteAllResponse = {
  deleted: number;
  skipped_active: number;
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

export type BinanceAssetBalance = {
  asset: string;
  wallet_balance: number;
  available_balance: number;
  unrealized_profit: number;
  margin_balance: number;
};

export type BinancePositionSummary = {
  symbol: string;
  side: "LONG" | "SHORT";
  position_amt: number;
  entry_price: number;
  break_even_price: number;
  unrealized_pnl: number;
  notional: number;
  leverage: number;
  isolated: boolean;
};

export type BinanceAccountSummary = {
  configured: boolean;
  connected: boolean;
  market: "binance_futures";
  mode: "testnet" | "mainnet" | "custom";
  base_url: string;
  total_wallet_balance: number | null;
  total_wallet_balance_btc: number | null;
  total_unrealized_profit: number | null;
  total_margin_balance: number | null;
  available_balance: number | null;
  can_trade: boolean | null;
  update_time: string;
  assets: BinanceAssetBalance[];
  positions: BinancePositionSummary[];
  error: string | null;
};
