"use client";

import { useCallback, useEffect, useState } from "react";

import { getBinanceAccountSummary } from "@/lib/api";
import type { BinanceAccountSummary } from "@/lib/types";

const REFRESH_MS = 15_000;

const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 6,
});

function formatUsd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  return usdFormatter.format(value);
}

function formatNumber(value: number | null, digits = 6): string {
  if (value === null || !Number.isFinite(value)) return "-";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function formatSignedUsd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  const abs = usdFormatter.format(Math.abs(value));
  return value >= 0 ? `+${abs}` : `-${abs}`;
}

function modeBadge(mode: BinanceAccountSummary["mode"]): string {
  if (mode === "testnet") return "Testnet";
  if (mode === "mainnet") return "Mainnet";
  return "Custom";
}

export function BinanceAccountPanel({ embedded }: { embedded?: boolean }) {
  const [snapshot, setSnapshot] = useState<BinanceAccountSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const data = await getBinanceAccountSummary();
      setSnapshot(data);
      setRequestError(null);
    } catch (e) {
      setRequestError(String(e));
    } finally {
      if (!silent) setRefreshing(false);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
    const id = setInterval(() => {
      void refresh(true);
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const connected = snapshot?.connected === true;
  const hasConfig = snapshot?.configured === true;
  const lastUpdated = snapshot?.update_time ? new Date(snapshot.update_time).toLocaleString() : "-";

  return (
    <section className={embedded ? "" : "mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5"}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        {!embedded && (
          <div>
            <h2 className="text-lg font-semibold text-[#d1d4dc]">Binance Asset Overview</h2>
            <p className="mt-1 text-xs text-[#868993]">
              Futures account snapshot with 15s auto-refresh
            </p>
          </div>
        )}
        <div className="flex items-center gap-2">
          {snapshot ? (
            <span className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#868993]">
              {modeBadge(snapshot.mode)}
            </span>
          ) : null}
          <button
            className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-1.5 text-xs text-[#d1d4dc] hover:border-[#2962ff] hover:bg-[#252936] transition-colors disabled:opacity-60"
            disabled={refreshing}
            onClick={() => void refresh(false)}
            type="button"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className={`mt-2 text-xs text-[#868993] ${embedded ? "mt-0" : ""}`}>
        Last updated: {lastUpdated}
      </div>

      {loading && !snapshot ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-sm text-[#868993]">
          Loading Binance account snapshot...
        </div>
      ) : null}

      {requestError ? (
        <div className="mt-4 rounded border border-[#ef5350]/40 bg-[#2d1f1f] px-4 py-3 text-sm text-[#ef5350]">
          API request failed: {requestError}
        </div>
      ) : null}

      {snapshot && !hasConfig ? (
        <div className="mt-4 rounded border border-[#efb74d]/40 bg-[#2d2718] px-4 py-3 text-sm text-[#efb74d]">
          Binance API keys are not configured.{" "}
          <a className="underline hover:text-[#d1d4dc] transition-colors" href="/settings">
            Go to Settings
          </a>{" "}
          to set up your keys.
        </div>
      ) : null}

      {snapshot && hasConfig && !connected ? (
        <div className="mt-4 rounded border border-[#ef5350]/40 bg-[#2d1f1f] px-4 py-3 text-sm text-[#ef5350]">
          Failed to connect Binance account. {snapshot.error ?? ""}
        </div>
      ) : null}

      {snapshot && connected ? (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="text-xs text-[#868993]">Total Wallet</div>
              <div className="mt-1 text-lg font-semibold text-[#d1d4dc]">
                {formatUsd(snapshot.total_wallet_balance)}
              </div>
              <div className="text-xs text-[#868993]">
                {snapshot.total_wallet_balance_btc !== null
                  ? `${formatNumber(snapshot.total_wallet_balance_btc, 6)} BTC`
                  : "-"}
              </div>
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="text-xs text-[#868993]">Available Balance</div>
              <div className="mt-1 text-lg font-semibold text-[#d1d4dc]">
                {formatUsd(snapshot.available_balance)}
              </div>
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="text-xs text-[#868993]">Unrealized PnL</div>
              <div
                className={[
                  "mt-1 text-lg font-semibold",
                  (snapshot.total_unrealized_profit ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]",
                ].join(" ")}
              >
                {formatSignedUsd(snapshot.total_unrealized_profit)}
              </div>
            </div>
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="text-xs text-[#868993]">Open Positions</div>
              <div className="mt-1 text-lg font-semibold text-[#d1d4dc]">
                {snapshot.positions.length}
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="mb-2 text-sm font-medium text-[#d1d4dc]">Assets</div>
              {snapshot.assets.length === 0 ? (
                <div className="text-sm text-[#868993]">No asset balance available.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="text-[#868993]">
                      <tr>
                        <th className="pb-2 pr-4 font-medium">Asset</th>
                        <th className="pb-2 pr-4 font-medium">Wallet</th>
                        <th className="pb-2 pr-4 font-medium">Available</th>
                        <th className="pb-2 font-medium">Unrealized</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.assets.slice(0, 8).map((asset) => (
                        <tr key={asset.asset} className="border-t border-[#2a2e39] text-[#d1d4dc]">
                          <td className="py-2 pr-4">{asset.asset}</td>
                          <td className="py-2 pr-4">{numberFormatter.format(asset.wallet_balance)}</td>
                          <td className="py-2 pr-4">{numberFormatter.format(asset.available_balance)}</td>
                          <td
                            className={[
                              "py-2",
                              asset.unrealized_profit >= 0 ? "text-[#26a69a]" : "text-[#ef5350]",
                            ].join(" ")}
                          >
                            {formatNumber(asset.unrealized_profit, 4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
              <div className="mb-2 text-sm font-medium text-[#d1d4dc]">Positions</div>
              {snapshot.positions.length === 0 ? (
                <div className="text-sm text-[#868993]">No open position.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="text-[#868993]">
                      <tr>
                        <th className="pb-2 pr-4 font-medium">Symbol</th>
                        <th className="pb-2 pr-4 font-medium">Side</th>
                        <th className="pb-2 pr-4 font-medium">Qty</th>
                        <th className="pb-2 pr-4 font-medium">Entry</th>
                        <th className="pb-2 font-medium">PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.positions.slice(0, 8).map((position) => (
                        <tr
                          key={`${position.symbol}-${position.side}`}
                          className="border-t border-[#2a2e39] text-[#d1d4dc]"
                        >
                          <td className="py-2 pr-4">{position.symbol}</td>
                          <td
                            className={[
                              "py-2 pr-4 font-medium",
                              position.side === "LONG" ? "text-[#26a69a]" : "text-[#ef5350]",
                            ].join(" ")}
                          >
                            {position.side}
                          </td>
                          <td className="py-2 pr-4">{formatNumber(position.position_amt, 5)}</td>
                          <td className="py-2 pr-4">{formatNumber(position.entry_price, 2)}</td>
                          <td
                            className={[
                              "py-2",
                              position.unrealized_pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]",
                            ].join(" ")}
                          >
                            {formatSignedUsd(position.unrealized_pnl)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
