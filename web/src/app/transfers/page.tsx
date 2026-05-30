"use client";

import { useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  getUpbitKeysStatus,
  setUpbitKeys,
  deleteUpbitKeys,
  getUpbitAccount,
  listBridgeTransfers,
  startOnramp,
  startOfframp,
  syncTransferStatus,
} from "@/lib/api";
import type { UpbitKeysStatus, UpbitAccount, BridgeTransfer } from "@/lib/api";

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtUsdt(v: number | null | undefined) {
  if (v == null) return "—";
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtKrw(v: number | null | undefined) {
  if (v == null) return "—";
  return `₩${v.toLocaleString("ko-KR")}`;
}

function statusBadge(status: BridgeTransfer["status"]) {
  const map: Record<string, string> = {
    PENDING: "bg-[#3a3f4e] text-[#868993]",
    CONVERTING: "bg-[#1a3050] text-[#2962ff]",
    WITHDRAWING: "bg-[#2a2000] text-[#F0B90B]",
    CONFIRMING: "bg-[#1a2a3a] text-[#60a5fa]",
    COMPLETED: "bg-[#0a2a1a] text-[#26a17b]",
    FAILED: "bg-[#2a1010] text-[#ef4444]",
  };
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${map[status] ?? "bg-[#3a3f4e] text-[#868993]"}`}>
      {status}
    </span>
  );
}

function directionLabel(dir: BridgeTransfer["direction"]) {
  return dir === "UPBIT_TO_BINANCE" ? "업비트 → 바이낸스" : "바이낸스 → 업비트";
}

// ── UpbitKeysSection ──────────────────────────────────────────────────────────

function UpbitKeysSection() {
  const { data: keysStatus, mutate: mutateKeys } = useSWR<UpbitKeysStatus>(
    "upbitKeysStatus",
    getUpbitKeysStatus,
  );

  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!accessKey.trim() || !secretKey.trim()) {
      setMsg({ type: "error", text: "Access Key와 Secret Key를 모두 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await setUpbitKeys({ access_key: accessKey.trim(), secret_key: secretKey.trim() });
      setMsg({ type: "success", text: "업비트 API 키가 저장되었습니다." });
      setAccessKey("");
      setSecretKey("");
      await mutateKeys();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : "저장 실패" });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await deleteUpbitKeys();
      setMsg({ type: "success", text: "업비트 API 키가 삭제되었습니다." });
      await mutateKeys();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : "삭제 실패" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
      <h2 className="mb-4 text-base font-semibold text-[#d1d4dc]">업비트 API 키</h2>

      {keysStatus?.configured && (
        <div className="mb-4 flex items-center gap-3 rounded bg-[#0a2a1a] px-4 py-2.5 text-sm">
          <span className="text-[#26a17b]">연결됨</span>
          <span className="font-mono text-[#868993]">{keysStatus.access_key_masked}</span>
          <button
            type="button"
            onClick={handleDelete}
            disabled={saving}
            className="ml-auto text-xs text-[#ef4444] hover:underline disabled:opacity-50"
          >
            삭제
          </button>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-[#868993]">Access Key</label>
          <input
            type="text"
            value={accessKey}
            onChange={(e) => setAccessKey(e.target.value)}
            placeholder="업비트 Open API Access Key"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4f5e] focus:border-[#2962ff] focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-[#868993]">Secret Key</label>
          <input
            type="password"
            value={secretKey}
            onChange={(e) => setSecretKey(e.target.value)}
            placeholder="업비트 Open API Secret Key"
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4f5e] focus:border-[#2962ff] focus:outline-none"
          />
        </div>
        {msg && (
          <p className={`text-xs ${msg.type === "success" ? "text-[#26a17b]" : "text-[#ef4444]"}`}>
            {msg.text}
          </p>
        )}
        <button
          type="submit"
          disabled={saving}
          className="rounded bg-[#2962ff] px-4 py-2 text-sm font-medium text-white hover:bg-[#1e4fd8] disabled:opacity-50"
        >
          {saving ? "저장 중…" : "API 키 저장"}
        </button>
      </form>
    </div>
  );
}

// ── BalanceBar ────────────────────────────────────────────────────────────────

function BalanceBar() {
  const { data: upbit, isLoading: upbitLoading } = useSWR<UpbitAccount>(
    "upbitAccount",
    getUpbitAccount,
    { refreshInterval: 30_000 },
  );

  const upbitUsdt = upbit?.balances.find((b) => b.currency === "USDT");
  const upbitKrw = upbit?.balances.find((b) => b.currency === "KRW");

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {[
        { label: "업비트 USDT", value: upbitLoading ? "…" : fmtUsdt(upbitUsdt ? upbitUsdt.balance : 0) },
        { label: "업비트 KRW", value: upbitLoading ? "…" : fmtKrw(upbitKrw ? upbitKrw.balance : 0) },
        {
          label: "KRW/USDT",
          value: upbit ? `₩${upbit.krw_usdt_price.toLocaleString("ko-KR")}` : "—",
        },
      ].map(({ label, value }) => (
        <div key={label} className="rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
          <p className="text-xs text-[#868993]">{label}</p>
          <p className="mt-1 text-sm font-semibold text-[#d1d4dc]">{value}</p>
        </div>
      ))}
    </div>
  );
}

// ── TransferForm ──────────────────────────────────────────────────────────────

function TransferForm({ onSuccess }: { onSuccess: () => void }) {
  const [direction, setDirection] = useState<"onramp" | "offramp">("onramp");
  const [usdtAmount, setUsdtAmount] = useState("");
  const [network, setNetwork] = useState("TRC20");
  const [convertKrw, setConvertKrw] = useState(false);
  const [sellKrw, setSellKrw] = useState(false);
  const [redeemEarn, setRedeemEarn] = useState(true);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const amount = parseFloat(usdtAmount);
    if (!amount || amount <= 0) {
      setMsg({ type: "error", text: "USDT 금액을 올바르게 입력하세요." });
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      if (direction === "onramp") {
        const res = await startOnramp({ usdt_amount: amount, network, convert_from_krw: convertKrw });
        setMsg({ type: "success", text: `이체 시작됨 (ID: ${res.id.slice(0, 8)}…)` });
      } else {
        const res = await startOfframp({ usdt_amount: amount, network, sell_to_krw: sellKrw, redeem_from_earn: redeemEarn });
        setMsg({ type: "success", text: `이체 시작됨 (ID: ${res.id.slice(0, 8)}…)` });
      }
      setUsdtAmount("");
      onSuccess();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : "이체 실패" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
      <h2 className="mb-4 text-base font-semibold text-[#d1d4dc]">이체 실행</h2>

      {/* Direction tabs */}
      <div className="mb-5 flex rounded-lg bg-[#131722] p-1 text-sm">
        {(["onramp", "offramp"] as const).map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => setDirection(d)}
            className={[
              "flex-1 rounded-md py-2 font-medium transition-colors",
              direction === d
                ? "bg-[#2962ff] text-white"
                : "text-[#868993] hover:text-[#d1d4dc]",
            ].join(" ")}
          >
            {d === "onramp" ? "업비트 → 바이낸스" : "바이낸스 → 업비트"}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="mb-1 block text-xs text-[#868993]">USDT 금액</label>
            <input
              type="number"
              min="1"
              step="any"
              value={usdtAmount}
              onChange={(e) => setUsdtAmount(e.target.value)}
              placeholder="100"
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4f5e] focus:border-[#2962ff] focus:outline-none"
            />
          </div>
          <div className="w-28">
            <label className="mb-1 block text-xs text-[#868993]">네트워크</label>
            <select
              value={network}
              onChange={(e) => setNetwork(e.target.value)}
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
            >
              <option value="TRC20">TRC20</option>
              <option value="ERC20">ERC20</option>
              <option value="BEP20">BEP20</option>
            </select>
          </div>
        </div>

        {direction === "onramp" && (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-[#d1d4dc]">
            <input
              type="checkbox"
              checked={convertKrw}
              onChange={(e) => setConvertKrw(e.target.checked)}
              className="h-4 w-4 accent-[#2962ff]"
            />
            KRW로 USDT 먼저 매수
          </label>
        )}

        {direction === "offramp" && (
          <div className="space-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-[#d1d4dc]">
              <input
                type="checkbox"
                checked={redeemEarn}
                onChange={(e) => setRedeemEarn(e.target.checked)}
                className="h-4 w-4 accent-[#2962ff]"
              />
              Simple Earn에서 먼저 출금
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-[#d1d4dc]">
              <input
                type="checkbox"
                checked={sellKrw}
                onChange={(e) => setSellKrw(e.target.checked)}
                className="h-4 w-4 accent-[#2962ff]"
              />
              업비트 도착 후 KRW로 자동 매도
            </label>
          </div>
        )}

        {msg && (
          <p className={`text-xs ${msg.type === "success" ? "text-[#26a17b]" : "text-[#ef4444]"}`}>
            {msg.text}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded bg-[#2962ff] py-2.5 text-sm font-semibold text-white hover:bg-[#1e4fd8] disabled:opacity-50"
        >
          {loading ? "처리 중…" : direction === "onramp" ? "업비트 → 바이낸스 이체" : "바이낸스 → 업비트 이체"}
        </button>
      </form>
    </div>
  );
}

// ── TransferHistory ───────────────────────────────────────────────────────────

function TransferHistory() {
  const { data, mutate } = useSWR("bridgeTransfers", listBridgeTransfers, { refreshInterval: 15_000 });

  async function handleSync(id: string) {
    try {
      await syncTransferStatus(id);
      await mutate();
    } catch {
      // ignore
    }
  }

  const transfers = data?.transfers ?? [];

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d]">
      <div className="border-b border-[#2a2e39] px-6 py-4">
        <h2 className="text-base font-semibold text-[#d1d4dc]">이체 이력</h2>
      </div>

      {transfers.length === 0 ? (
        <p className="px-6 py-8 text-center text-sm text-[#868993]">이체 이력이 없습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2e39] text-xs text-[#868993]">
                <th className="px-4 py-3 text-left">날짜</th>
                <th className="px-4 py-3 text-left">방향</th>
                <th className="px-4 py-3 text-right">금액</th>
                <th className="px-4 py-3 text-left">네트워크</th>
                <th className="px-4 py-3 text-left">상태</th>
                <th className="px-4 py-3 text-left">TxID</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {transfers.map((t) => (
                <tr key={t.id} className="border-b border-[#2a2e39]/50 hover:bg-[#2a2e39]/20">
                  <td className="px-4 py-3 text-xs text-[#868993]">
                    {new Date(t.initiated_at).toLocaleString("ko-KR")}
                  </td>
                  <td className="px-4 py-3 text-[#d1d4dc]">{directionLabel(t.direction)}</td>
                  <td className="px-4 py-3 text-right font-mono text-[#d1d4dc]">
                    {fmtUsdt(t.requested_usdt)}
                    {t.krw_amount != null && (
                      <span className="ml-1 text-xs text-[#868993]">({fmtKrw(t.krw_amount)})</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[#868993]">{t.network}</td>
                  <td className="px-4 py-3">{statusBadge(t.status)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[#868993]">
                    {t.dst_txid ? `${t.dst_txid.slice(0, 12)}…` : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {!["COMPLETED", "FAILED"].includes(t.status) && (
                      <button
                        type="button"
                        onClick={() => handleSync(t.id)}
                        className="rounded px-2 py-1 text-xs text-[#2962ff] hover:bg-[#2962ff]/10"
                      >
                        상태 갱신
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TransfersPage() {
  const { data: keysStatus } = useSWR<UpbitKeysStatus>("upbitKeysStatus", getUpbitKeysStatus);
  const upbitConfigured = keysStatus?.configured ?? false;

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-xl font-bold text-[#d1d4dc]">거래소 이체</h1>
        <p className="mt-1 text-sm text-[#868993]">업비트 ↔ 바이낸스 간 USDT를 직접 이체합니다.</p>
      </div>

      <UpbitKeysSection />

      {upbitConfigured && (
        <>
          <BalanceBar />
          <TransferForm onSuccess={() => globalMutate("bridgeTransfers")} />
        </>
      )}

      <TransferHistory />
    </main>
  );
}
