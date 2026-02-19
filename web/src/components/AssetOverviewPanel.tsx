"use client";

import { useState } from "react";
import { BinanceAccountPanel } from "@/components/BinanceAccountPanel";
import type { BinanceKeysStatus } from "@/lib/types";

const EXCHANGE_TABS = [
  { id: "binance", label: "Binance" },
  { id: "bybit", label: "Bybit" },
  { id: "okx", label: "OKX" },
  { id: "kraken", label: "Kraken" },
] as const;

export function AssetOverviewPanel({ keysStatus }: { keysStatus: BinanceKeysStatus | null }) {
  const [activeTab, setActiveTab] = useState<string>("binance");

  const binanceConnected = !!keysStatus?.configured;

  return (
    <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <h2 className="text-lg font-semibold text-[#d1d4dc]">Asset Overview</h2>
      <p className="mt-1 text-xs text-[#868993]">
        거래소별 자산 현황 (15초 자동 새로고침)
      </p>

      <div className="mt-4 flex gap-1 border-b border-[#2a2e39]">
        {EXCHANGE_TABS.map((tab) => {
          const connected = tab.id === "binance" && binanceConnected;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-t px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? "border-b-2 border-[#2962ff] text-[#2962ff] bg-[#131722]"
                  : "text-[#868993] hover:text-[#d1d4dc] hover:bg-[#252936]"
              }`}
            >
              {tab.label}
              <span
                className={`ml-1.5 inline-block h-1.5 w-1.5 rounded-full ${
                  connected ? "bg-[#26a69a]" : "bg-[#ef5350]"
                }`}
              />
            </button>
          );
        })}
      </div>

      <div className="mt-4">
        {activeTab === "binance" ? (
          <BinanceAccountPanel embedded />
        ) : (
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-8 text-center text-sm text-[#868993]">
            연동 준비중입니다. 주요 코인거래소 연동을 확장할 예정입니다.
          </div>
        )}
      </div>
    </section>
  );
}
