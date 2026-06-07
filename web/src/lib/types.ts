export type JobType = "BACKTEST" | "LIVE";

export type JobStatus =
  | "PENDING"
  | "RUNNING"
  | "STOP_REQUESTED"
  | "SUCCEEDED"
  | "STOPPED"
  | "FAILED";

export type StrategyInfo = { name: string; path: string };

export type StrategyContentResponse = {
  name: string;
  path: string;
  code: string;
};

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

export type StrategyChatSessionRecord = {
  session_id: string;
  title: string;
  data: Record<string, unknown>;
  message_count: number;
  created_at: string;
  updated_at: string;
};

/** Lightweight session metadata — no data payload. */
export type StrategyChatSessionSummary = {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

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

export type StrategyParamFieldSpec = {
  type?: string;
  label?: string;
  description?: string;
  group?: string;
  min?: number;
  max?: number;
  enum?: unknown[];
};

export type StrategyParamsExtractResponse = {
  supported: boolean;
  values: Record<string, unknown>;
  schema_fields: Record<string, StrategyParamFieldSpec>;
};

export type StrategyParamsApplyResponse = {
  code: string;
};

// ── Quick Backtest ──────────────────────────────────────────

export type QuickBacktestRequest = {
  code: string;
  symbol?: string;
  interval?: string;
  days?: number;
  initial_balance?: number;
  leverage?: number;
  max_position?: number;
  commission?: number;
  stop_loss_pct?: number;
  strategy_params?: Record<string, unknown>;
};

export type QuickBacktestMetrics = {
  initial_balance: number;
  final_balance: number;
  total_return_pct: number;
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  net_profit: number;
  total_commission: number;
};

export type QuickBacktestTrade = {
  side: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  return_pct: number;
};

export type QuickBacktestEquityPoint = {
  ts: number;
  balance: number;
};

export type QuickBacktestResponse = {
  success: boolean;
  error_code?: string;
  message?: string;
  metrics?: QuickBacktestMetrics;
  trades_summary: QuickBacktestTrade[];
  equity_curve: QuickBacktestEquityPoint[];
  duration_ms: number;
  quota_remaining?: number;
  quota_reset_at?: string;
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

/** Lightweight job representation — no trades in result. */
export type JobSummary = {
  job_id: string;
  type: JobType;
  status: JobStatus;
  strategy_path: string;
  config: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
};

export type JobCounts = {
  backtest_total: number;
  live_total: number;
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

export type BinanceCredentialEnv = "mainnet" | "testnet";

export type BinanceCredential = {
  env: BinanceCredentialEnv;
  configured: boolean;
  api_key_masked?: string;
  /** Operator-supplied memo of the IPs registered on Binance for this
   *  master key. Backend never enforces — Binance does — but storing
   *  it lets the UI surface drift between intent and reality. */
  ip_whitelist?: string[];
};

export type WalletRole = "master" | "sub";
export type WalletPurpose =
  | "generic"
  | "directional"
  | "arbitrage"
  | "derivatives"
  | "earn"
  | "copy_trading";
export type WalletAccountStatus =
  | "active"
  | "disabled"
  | "key_missing"
  | "key_invalid"
  | "binance_missing";

export type WalletAccount = {
  id: string;
  env: BinanceCredentialEnv;
  role: WalletRole;
  purpose: WalletPurpose;
  alias: string;
  sub_account_email: string | null;
  status: WalletAccountStatus;
  api_key_masked: string | null;
  enabled_wallets: Record<string, unknown> | null;
  ip_whitelist: string[] | null;
  created_at: string | null;
  updated_at: string | null;
};

export type WalletSyncSummary = {
  user_id: string;
  env: string;
  ok: boolean;
  ts: string;
  binance_subs: number;
  db_subs: number;
  marked_missing: string[];
  marked_disabled: string[];
  cleared_missing: string[];
  auto_created: string[];
  permissions_synced: string[];
  unmanaged_binance_subs: string[];
  error: string | null;
};

export type CreateSubAccountInput = {
  alias: string;
  purpose: WalletPurpose;
  env?: BinanceCredentialEnv;
  enable_futures?: boolean;
  enable_options?: boolean;
};

export type UpdateWalletKeysInput = {
  api_key: string;
  api_secret: string;
  ip_whitelist?: string[];
  mark_active?: boolean;
};

export type StrategyAllocation = {
  job_id: string;
  wallet_account_id: string;
  allocation_mode: string;
  allocated_usdt: number;
  reserved_usdt: number;
  free_usdt: number;
  max_drawdown_pct: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type WalletTransferRecord = {
  id: string;
  from_wallet_account_id: string | null;
  to_wallet_account_id: string | null;
  from_wallet_type: string;
  to_wallet_type: string;
  asset: string;
  amount: number;
  reason: string | null;
  status: "pending" | "succeeded" | "failed" | string;
  client_tran_id: string | null;
  binance_tran_id: string | null;
  error_message: string | null;
  created_at: string | null;
  completed_at: string | null;
};

export type UserProfile = {
  user_id: string;
  email: string;
  display_name: string;
  plan: string;
  has_binance_keys: boolean;
  binance_configured_envs: BinanceCredentialEnv[];
  plan_expires_at: string | null;
  created_at: string;
};

export type AdminUserItem = {
  user_id: string;
  email: string;
  display_name: string;
  plan: string;
  email_verified: boolean;
  created_at: string;
};

export type AdminUsersResponse = {
  users: AdminUserItem[];
  total: number;
};

export type BinanceKeysStatus = {
  configured: boolean;
  api_key_masked?: string;
  base_url?: string;
};

export type AutoSweepSettings = {
  enabled: boolean;
  futures_buffer_usdt: number;
  sweep_threshold_usdt: number;
  margin_restore_cap_usdt: number;
  mainnet_required: boolean;
  keys_configured: boolean;
  futures_usdt: number | null;
  earn_usdt: number | null;
  last_run_at: string | null;
  last_action: string | null;
  last_error: string | null;
};

export type AutoSweepSettingsInput = {
  enabled: boolean;
  futures_buffer_usdt: number;
  sweep_threshold_usdt: number;
  margin_restore_cap_usdt: number;
};

export type WalletBalance = {
  wallet: string;
  label: string;
  balance_usdt: number;
  unrealized_pnl: number;
  pct: number;
};

export type WalletOverview = {
  total_usdt: number;
  wallets: WalletBalance[];
  as_of: string;
  error: string | null;
};

export type LiveStrategyPositions = {
  job_id: string;
  strategy_path: string;
  strategy_name: string;
  status: string;
  symbols: string[];
  allocated_usdt: number;
  positions: BinancePositionSummary[];
  position_count: number;
  total_notional: number;
  total_unrealized_pnl: number;
};

export type LivePositionsTotals = {
  strategy_count: number;
  open_position_count: number;
  total_notional: number;
  total_unrealized_pnl: number;
};

export type LivePositionsResponse = {
  strategies: LiveStrategyPositions[];
  unattributed: BinancePositionSummary[];
  totals: LivePositionsTotals;
  as_of: string;
  error: string | null;
};

export type BillingStatus = {
  plan: string;
  limits: {
    max_live_jobs: number;
    max_backtest_per_month: number;
    max_llm_generate_per_month: number;
    portfolio_mode: boolean;
  };
  usage: {
    backtest_this_month: number;
    llm_generate_this_month: number;
  };
  plan_expires_at: string | null;
};

export type CheckoutResponse = {
  checkout_url: string;
  session_id: string;
};

export type PortalResponse = {
  portal_url: string;
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
  entry_time: string | null;
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

// ── Portfolio Summary (Quant Asset Management Platform) ────

export type WalletSnapshot = {
  wallet: "futures" | "spot" | "earn";
  balance_usdt: number;
  unrealized_pnl: number;
};

export type AllocationCategory =
  | "Directional_Alpha"
  | "Market_Neutral_Arbitrage"
  | "Yield_Earn"
  | "Cash";

export type AllocationSlice = {
  category: AllocationCategory;
  allocated_usdt: number;
  pct: number;
};

export type PortfolioSummaryResponse = {
  total_aum_usdt: number;
  total_unrealized_pnl: number;
  total_realized_pnl_today: number;
  wallets: WalletSnapshot[];
  allocation: AllocationSlice[];
  as_of: string;
};

export type StrategyModuleStatus = {
  module_id: string;
  name: string;
  category: "Directional_Alpha" | "Market_Neutral_Arbitrage" | "Yield_Earn";
  enabled: boolean;
  allocated_usdt: number;
  running_job_ids: string[];
  unrealized_pnl: number | null;
  realized_pnl_today: number | null;
  status: "running" | "idle" | "error" | "stopped";
  params: Record<string, unknown>;
};

export type StrategyModuleCatalogResponse = {
  modules: StrategyModuleStatus[];
};

export type FundingArbitrageParams = {
  symbol: string;
  allocated_usdt: number;
  hold_days?: number | null;
  entry_deadband_pct: number;
  exit_deadband_pct: number;
  margin_alert_ratio: number;
  rebalance_transfer_pct: number;
  env: "mainnet" | "testnet";
};

export type FundingArbitrageStatusResponse = {
  running: boolean;
  symbol: string | null;
  spot_qty: number | null;
  futures_short_qty: number | null;
  current_funding_rate: number | null;
  annualized_funding_pct: number | null;
  next_funding_time: string | null;
  unrealized_pnl: number | null;
  accumulated_funding_income: number;
  entry_fee: number | null;
  exit_fee: number | null;
  last_funding_ts: string | null;
  params: FundingArbitrageParams | null;
  last_error: string | null;
};

export type FundingScreenerItem = {
  symbol: string;
  current_rate_pct: number;   // 마지막 정산 펀딩비 (%)
  annualized_pct: number;     // 연환산 (%)
  half_life_settlements: number;
  entry_threshold_pct: number;
  score: number;              // >1 = 수익 가능
  avg_rate_pct: number;
  n_samples: number;
};

export type FundingScreenerResponse = {
  items: FundingScreenerItem[];
  roundtrip_cost_pct: number;
  error: string | null;
  as_of: string;
};
