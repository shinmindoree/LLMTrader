"use client";

import { useMemo, useState, type ReactElement } from "react";
import useSWR from "swr";
import {
  executeWalletTransfer,
  getWalletBalances,
  listWalletTransferLegs,
} from "@/lib/api";
import type {
  TransferLegRecord,
  UiWalletType,
  WalletBalanceCell,
  WalletBalanceRow,
  WalletBalancesResponse,
} from "@/lib/types";

// ── constants ────────────────────────────────────────────────────────

const WALLET_TYPE_LABEL: Record<UiWalletType, string> = {
  SPOT: "Spot",
  USDT_FUTURE: "USDⓈ-M Futures",
  COIN_FUTURE: "COIN-M Futures",
  MARGIN: "Margin",
  OPTION: "Options",
  EARN_FLEXIBLE: "Simple Earn",
};

const WALLET_TYPE_ORDER: UiWalletType[] = [
  "SPOT",
  "USDT_FUTURE",
  "COIN_FUTURE",
  "MARGIN",
  "OPTION",
  "EARN_FLEXIBLE",
];

const ENABLED_FLAG_KEY: Record<UiWalletType, string | null> = {
  SPOT: null,
  USDT_FUTURE: "futures_um",
  COIN_FUTURE: "futures_cm",
  MARGIN: "margin",
  OPTION: "options",
  EARN_FLEXIBLE: "earn",
};

const SUPPORTED_ASSETS = ["USDT", "USDC", "BTC", "ETH", "BNB"];

// Wallet types that only accept a restricted asset list. Cells for other
// assets are rendered as disabled.
const WALLET_ASSET_WHITELIST: Partial<Record<UiWalletType, string[]>> = {
  EARN_FLEXIBLE: ["USDT"],
};

function isAssetAllowed(wallet: UiWalletType, asset: string): boolean {
  const allow = WALLET_ASSET_WHITELIST[wallet];
  return allow ? allow.includes(asset) : true;
}

// ── helpers ──────────────────────────────────────────────────────────

function fmtAmount(value: number, asset: string): string {
  const digits = asset === "BTC" || asset === "ETH" ? 6 : 2;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function findCell(
  row: WalletBalanceRow | undefined,
  wallet: UiWalletType,
  asset: string,
): WalletBalanceCell | undefined {
  if (!row) return undefined;
  return row.balances[wallet]?.find((c) => c.asset === asset);
}

function isWalletEnabled(row: WalletBalanceRow, wallet: UiWalletType): boolean {
  if (row.role === "master") return true;
  const flag = ENABLED_FLAG_KEY[wallet];
  if (!flag) return true;
  const value = row.enabled_wallets[flag];
  return value !== false;
}

// Cells / wallet options that require a more restricted asset than what
// the user picked are rendered as inert but still visible so the user
// understands the wallet exists and just doesn't support this asset.
function isCellSelectable(
  row: WalletBalanceRow,
  wallet: UiWalletType,
  asset: string,
): boolean {
  return isWalletEnabled(row, wallet) && isAssetAllowed(wallet, asset);
}

function statusBadge(status: string): ReactElement {
  const cls: Record<string, string> = {
    PENDING: "bg-[#3a3f4e] text-[#868993]",
    SUCCEEDED: "bg-[#0a2a1a] text-[#26a17b]",
    FAILED: "bg-[#2a1010] text-[#ef4444]",
  };
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${
        cls[status] ?? "bg-[#3a3f4e] text-[#868993]"
      }`}
    >
      {status}
    </span>
  );
}

// ── balance grid ─────────────────────────────────────────────────────

function BalanceGrid({
  data,
  asset,
  onPick,
}: {
  data: WalletBalancesResponse | undefined;
  asset: string;
  onPick: (rowId: string | null, wallet: UiWalletType) => void;
}) {
  if (!data) {
    return (
      <p className="px-4 py-8 text-center text-sm text-[#868993]">
        잔고 불러오는 중…
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#2a2e39] text-xs text-[#868993]">
            <th className="px-4 py-2 text-left">계정</th>
            {WALLET_TYPE_ORDER.map((wt) => (
              <th key={wt} className="px-4 py-2 text-right">
                {WALLET_TYPE_LABEL[wt]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => {
            const rowId = row.wallet_account_id;
            const label = row.role === "master" ? "Master" : row.alias;
            return (
              <tr
                key={`${row.role}:${rowId ?? "master"}`}
                className="border-b border-[#2a2e39]/50"
              >
                <td className="px-4 py-2 text-[#d1d4dc]">
                  <div className="font-medium">{label}</div>
                  {row.email && (
                    <div className="text-xs text-[#4a4f5e]">{row.email}</div>
                  )}
                </td>
                {WALLET_TYPE_ORDER.map((wt) => {
                  const enabled = isCellSelectable(row, wt, asset);
                  const assetOk = isAssetAllowed(wt, asset);
                  const cell = findCell(row, wt, asset);
                  const value = cell?.free ?? 0;
                  const total = cell?.total ?? 0;
                  const reason = !assetOk
                    ? `${WALLET_TYPE_LABEL[wt]} 지갑은 ${asset} 미지원`
                    : !isWalletEnabled(row, wt)
                      ? "wallet 비활성"
                      : "이 셀을 출발지로 선택";
                  return (
                    <td
                      key={wt}
                      className={[
                        "px-4 py-2 text-right font-mono",
                        enabled
                          ? "text-[#d1d4dc]"
                          : "text-[#3a3f4e] line-through",
                      ].join(" ")}
                    >
                      <button
                        type="button"
                        disabled={!enabled}
                        onClick={() => onPick(rowId, wt)}
                        className={[
                          "rounded px-2 py-1 transition-colors",
                          enabled
                            ? "hover:bg-[#2a2e39]"
                            : "cursor-not-allowed",
                        ].join(" ")}
                        title={reason}
                      >
                        {value > 0 ? fmtAmount(value, asset) : "—"}
                        {total > value && (
                          <span className="ml-1 text-xs text-[#868993]">
                            (잠금 {fmtAmount(total - value, asset)})
                          </span>
                        )}
                      </button>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── form ─────────────────────────────────────────────────────────────

type FormState = {
  fromId: string | null;
  fromWallet: UiWalletType;
  toId: string | null;
  toWallet: UiWalletType;
  asset: string;
  amount: string;
};

const INITIAL_FORM: FormState = {
  fromId: null,
  fromWallet: "SPOT",
  toId: null,
  toWallet: "USDT_FUTURE",
  asset: "USDT",
  amount: "",
};

function TransferForm({
  data,
  onSubmitted,
  prefill,
}: {
  data: WalletBalancesResponse | undefined;
  onSubmitted: () => void;
  prefill?: { rowId: string | null; wallet: UiWalletType } | null;
}) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(
    null,
  );

  useMemo(() => {
    if (!prefill) return;
    setForm((f) => ({
      ...f,
      fromId: prefill.rowId,
      fromWallet: prefill.wallet,
    }));
  }, [prefill]);

  const rows = data?.rows ?? [];
  const fromRow = rows.find(
    (r) => (r.wallet_account_id ?? null) === form.fromId,
  );
  const toRow = rows.find(
    (r) => (r.wallet_account_id ?? null) === form.toId,
  );
  const fromCell = findCell(fromRow, form.fromWallet, form.asset);
  const available = fromCell?.free ?? 0;

  const samePoint =
    form.fromId === form.toId && form.fromWallet === form.toWallet;

  const accountOptions = rows.map((r) => ({
    value: r.wallet_account_id ?? "",
    label: r.role === "master" ? "Master" : r.alias,
  }));

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    const amt = parseFloat(form.amount);
    if (!amt || amt <= 0) {
      setMsg({ type: "err", text: "금액을 0보다 큰 값으로 입력하세요." });
      return;
    }
    if (samePoint) {
      setMsg({ type: "err", text: "출발지와 도착지가 동일합니다." });
      return;
    }
    if (
      !isAssetAllowed(form.fromWallet, form.asset) ||
      !isAssetAllowed(form.toWallet, form.asset)
    ) {
      setMsg({
        type: "err",
        text: `${form.asset} 자산은 Simple Earn 지갑에서 지원되지 않습니다 (USDT만 가능).`,
      });
      return;
    }
    if (available > 0 && amt > available) {
      setMsg({
        type: "err",
        text: `사용 가능 잔고(${fmtAmount(available, form.asset)} ${form.asset})를 초과합니다.`,
      });
      return;
    }
    setLoading(true);
    try {
      const result = await executeWalletTransfer({
        from_wallet_account_id: form.fromId,
        to_wallet_account_id: form.toId,
        from_wallet_type: form.fromWallet,
        to_wallet_type: form.toWallet,
        asset: form.asset,
        amount: amt,
      });
      const legText = result.leg_total > 1 ? ` (${result.leg_total}-leg)` : "";
      setMsg({
        type: "ok",
        text: `이체 완료${legText} — intent ${result.intent_id.slice(0, 8)}…`,
      });
      setForm((f) => ({ ...f, amount: "" }));
      onSubmitted();
    } catch (err) {
      setMsg({
        type: "err",
        text: err instanceof Error ? err.message : "이체 실패",
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6"
    >
      <h2 className="text-base font-semibold text-[#d1d4dc]">이체 실행</h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-2 rounded-lg border border-[#2a2e39]/60 p-3">
          <label className="text-xs font-semibold text-[#868993]">
            출발지 (From)
          </label>
          <select
            value={form.fromId ?? ""}
            onChange={(e) =>
              setField("fromId", e.target.value === "" ? null : e.target.value)
            }
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc]"
          >
            {accountOptions.map((o) => (
              <option key={o.value || "master"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            value={form.fromWallet}
            onChange={(e) =>
              setField("fromWallet", e.target.value as UiWalletType)
            }
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc]"
          >
            {WALLET_TYPE_ORDER.map((wt) => {
              const enabled = fromRow ? isWalletEnabled(fromRow, wt) : true;
              const assetOk = isAssetAllowed(wt, form.asset);
              return (
                <option key={wt} value={wt} disabled={!enabled || !assetOk}>
                  {WALLET_TYPE_LABEL[wt]}
                  {!enabled ? " (비활성)" : !assetOk ? ` (${form.asset} 미지원)` : ""}
                </option>
              );
            })}
          </select>
          <div className="text-xs text-[#868993]">
            사용 가능:{" "}
            <span className="font-mono text-[#d1d4dc]">
              {fmtAmount(available, form.asset)} {form.asset}
            </span>
          </div>
        </div>

        <div className="space-y-2 rounded-lg border border-[#2a2e39]/60 p-3">
          <label className="text-xs font-semibold text-[#868993]">
            도착지 (To)
          </label>
          <select
            value={form.toId ?? ""}
            onChange={(e) =>
              setField("toId", e.target.value === "" ? null : e.target.value)
            }
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc]"
          >
            {accountOptions.map((o) => (
              <option key={o.value || "master"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            value={form.toWallet}
            onChange={(e) =>
              setField("toWallet", e.target.value as UiWalletType)
            }
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc]"
          >
            {WALLET_TYPE_ORDER.map((wt) => {
              const enabled = toRow ? isWalletEnabled(toRow, wt) : true;
              const assetOk = isAssetAllowed(wt, form.asset);
              return (
                <option key={wt} value={wt} disabled={!enabled || !assetOk}>
                  {WALLET_TYPE_LABEL[wt]}
                  {!enabled ? " (비활성)" : !assetOk ? ` (${form.asset} 미지원)` : ""}
                </option>
              );
            })}
          </select>
          <div className="text-xs text-[#868993]">
            현재 잔고:{" "}
            <span className="font-mono text-[#d1d4dc]">
              {fmtAmount(
                findCell(toRow, form.toWallet, form.asset)?.free ?? 0,
                form.asset,
              )}{" "}
              {form.asset}
            </span>
          </div>
        </div>
      </div>

      <div className="flex gap-3">
        <div className="w-32">
          <label className="mb-1 block text-xs text-[#868993]">자산</label>
          <select
            value={form.asset}
            onChange={(e) => setField("asset", e.target.value)}
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc]"
          >
            {SUPPORTED_ASSETS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-xs text-[#868993]">금액</label>
          <div className="flex gap-2">
            <input
              type="number"
              min="0"
              step="any"
              value={form.amount}
              onChange={(e) => setField("amount", e.target.value)}
              placeholder="0.00"
              className="flex-1 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
            />
            <button
              type="button"
              onClick={() =>
                setField("amount", available > 0 ? String(available) : "")
              }
              className="rounded bg-[#2962ff]/15 px-3 py-2 text-xs font-medium text-[#2962ff] hover:bg-[#2962ff]/25"
            >
              MAX
            </button>
          </div>
        </div>
      </div>

      {msg && (
        <p
          className={`text-sm ${
            msg.type === "ok" ? "text-[#26a17b]" : "text-[#ef4444]"
          }`}
        >
          {msg.text}
        </p>
      )}

      <button
        type="submit"
        disabled={loading || samePoint}
        className="w-full rounded bg-[#2962ff] py-2.5 text-sm font-semibold text-white hover:bg-[#1e4fd8] disabled:opacity-50"
      >
        {loading ? "이체 처리 중…" : "이체 실행"}
      </button>

      {form.fromWallet === "OPTION" ||
      form.toWallet === "OPTION" ||
      form.fromWallet === "EARN_FLEXIBLE" ||
      form.toWallet === "EARN_FLEXIBLE" ? (
        <p className="rounded bg-[#2a2000]/40 px-3 py-2 text-xs text-[#F0B90B]">
          Options · Simple Earn 지갑은 Binance 정책상 universal transfer 가 지원되지
          않습니다. 시스템이 자동으로 Spot 경유 multi-leg 이체로 분해합니다
          (Earn 은 subscribe/redeem, Options 는 asset/transfer 호출).
          Sub-account 의 경우 해당 sub 의 거래용 API key 가 등록되어 있어야 합니다.
          Simple Earn 은 현재 USDT 만 지원하며 mainnet 전용입니다.
        </p>
      ) : null}
    </form>
  );
}

// ── history ──────────────────────────────────────────────────────────

function legAccountLabel(
  id: string | null,
  rows: WalletBalanceRow[],
): string {
  if (id === null) return "Master";
  const row = rows.find((r) => r.wallet_account_id === id);
  return row?.alias ?? id.slice(0, 8);
}

function HistoryTable({
  rows,
  balanceRows,
}: {
  rows: TransferLegRecord[];
  balanceRows: WalletBalanceRow[];
}) {
  if (rows.length === 0) {
    return (
      <p className="px-6 py-8 text-center text-sm text-[#868993]">
        이체 이력이 없습니다.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#2a2e39] text-xs text-[#868993]">
            <th className="px-4 py-3 text-left">시각</th>
            <th className="px-4 py-3 text-left">경로</th>
            <th className="px-4 py-3 text-right">금액</th>
            <th className="px-4 py-3 text-left">상태</th>
            <th className="px-4 py-3 text-left">Binance Tx</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((leg) => (
            <tr
              key={leg.id}
              className="border-b border-[#2a2e39]/50 hover:bg-[#2a2e39]/20"
            >
              <td className="px-4 py-2 text-xs text-[#868993]">
                {leg.created_at
                  ? new Date(leg.created_at).toLocaleString("ko-KR")
                  : "—"}
              </td>
              <td className="px-4 py-2 text-[#d1d4dc]">
                {leg.leg_total > 1 && (
                  <span className="mr-2 inline-block rounded bg-[#2962ff]/15 px-1.5 py-0.5 text-xs font-medium text-[#2962ff]">
                    {leg.leg_index}/{leg.leg_total}
                  </span>
                )}
                {legAccountLabel(leg.from_wallet_account_id, balanceRows)}{" "}
                <span className="text-[#868993]">
                  ({leg.from_wallet_type})
                </span>{" "}
                <span className="text-[#4a4f5e]">→</span>{" "}
                {legAccountLabel(leg.to_wallet_account_id, balanceRows)}{" "}
                <span className="text-[#868993]">({leg.to_wallet_type})</span>
              </td>
              <td className="px-4 py-2 text-right font-mono text-[#d1d4dc]">
                {fmtAmount(leg.amount, leg.asset)} {leg.asset}
              </td>
              <td className="px-4 py-2">{statusBadge(leg.status)}</td>
              <td className="px-4 py-2 font-mono text-xs text-[#868993]">
                {leg.binance_tran_id ? leg.binance_tran_id : "—"}
                {leg.error_message && (
                  <div className="text-[#ef4444]">{leg.error_message}</div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── main tab ─────────────────────────────────────────────────────────

export function InternalTransferTab() {
  const {
    data: balances,
    error: balErr,
    mutate: mutateBalances,
    isLoading,
  } = useSWR<WalletBalancesResponse>(
    "wallet-balances:mainnet",
    () => getWalletBalances("mainnet"),
    { refreshInterval: 30_000 },
  );
  const { data: history, mutate: mutateHistory } = useSWR<TransferLegRecord[]>(
    "wallet-transfer-legs",
    () => listWalletTransferLegs(50),
    { refreshInterval: 10_000 },
  );

  const [prefill, setPrefill] = useState<
    { rowId: string | null; wallet: UiWalletType } | null
  >(null);

  function handleSubmitted() {
    mutateBalances();
    mutateHistory();
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d]">
        <div className="flex items-center justify-between border-b border-[#2a2e39] px-6 py-4">
          <h2 className="text-base font-semibold text-[#d1d4dc]">잔고 매트릭스</h2>
          <button
            type="button"
            onClick={() => mutateBalances()}
            className="rounded border border-[#2a2e39] px-3 py-1 text-xs text-[#868993] hover:bg-[#2a2e39] hover:text-[#d1d4dc]"
          >
            {isLoading ? "불러오는 중…" : "새로고침"}
          </button>
        </div>
        {balErr ? (
          <p className="px-6 py-8 text-center text-sm text-[#ef4444]">
            잔고 조회 실패: {(balErr as Error).message}
          </p>
        ) : (
          <BalanceGrid
            data={balances}
            asset="USDT"
            onPick={(rowId, wallet) => setPrefill({ rowId, wallet })}
          />
        )}
      </div>

      <TransferForm
        data={balances}
        onSubmitted={handleSubmitted}
        prefill={prefill}
      />

      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d]">
        <div className="border-b border-[#2a2e39] px-6 py-4">
          <h2 className="text-base font-semibold text-[#d1d4dc]">이체 이력</h2>
        </div>
        <HistoryTable
          rows={history ?? []}
          balanceRows={balances?.rows ?? []}
        />
      </div>
    </div>
  );
}
