"use client";

import { useMemo, useState } from "react";

import KimpBacktestPanel from "@/components/KimpBacktestPanel";
import KimpBotControl from "@/components/KimpBotControl";
import KimpHistoryChart from "@/components/KimpHistoryChart";
import KimpPaperPortfolio from "@/components/KimpPaperPortfolio";
import KimpScreenerTable from "@/components/KimpScreenerTable";
import { HubHeader } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";
import type { KimpRateMode } from "@/lib/types";
import { useKimpScreenerStream } from "@/lib/useKimpScreenerStream";

const KIMP_SUB_TABS = ["screener", "backtest", "bot"] as const;

type KimpSubTab = (typeof KIMP_SUB_TABS)[number];

export default function ArbitrageKimpPage() {
  const { t } = useI18n();
  const k = t.hubs.arbitrage.kimp;
  const [activeTab, setActiveTab] = useState<KimpSubTab>("screener");
  const [symbol, setSymbol] = useState<string>("BTC");
  const [rateMode, setRateMode] = useState<KimpRateMode>("usdt");
  const screener = useKimpScreenerStream();
  const selectedItem = useMemo(
    () => screener.data?.items.find((item) => item.symbol === symbol) ?? null,
    [screener.data?.items, symbol],
  );

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={k.title} subtitle={k.subtitle} />

      <div className="mt-6 flex gap-1 border-b border-[#2a2e39]">
        {KIMP_SUB_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            aria-pressed={activeTab === tab}
            className={`rounded-t px-4 py-2 text-sm font-medium transition ${
              activeTab === tab
                ? "border border-[#2a2e39] border-b-transparent bg-[#1e222d] text-[#d1d4dc]"
                : "text-[#868993] hover:text-[#d1d4dc]"
            }`}
          >
            {k.tabs[tab]}
          </button>
        ))}
      </div>

      <div className={activeTab === "screener" ? "mt-4 flex flex-col gap-4" : "hidden"}>
        <KimpHistoryChart
          symbol={symbol}
          latest={selectedItem}
          latestAsOf={screener.data?.as_of ?? null}
          usdtFx={screener.data?.fx ?? null}
          bankFx={screener.data?.bank_fx ?? null}
          rateMode={rateMode}
        />
        <KimpScreenerTable
          symbol={symbol}
          onSelect={setSymbol}
          data={screener.data}
          error={screener.error}
          isLoading={screener.isLoading}
          isValidating={screener.isValidating}
          status={screener.status}
          onRefresh={screener.refetch}
          rateMode={rateMode}
          onRateModeChange={setRateMode}
        />
      </div>

      <div className={activeTab === "backtest" ? "mt-4 flex flex-col gap-4" : "hidden"}>
        <KimpBacktestPanel symbol={symbol} onSelect={setSymbol} />
      </div>

      <div className={activeTab === "bot" ? "mt-4 flex flex-col gap-4" : "hidden"}>
        <KimpPaperPortfolio onSelect={setSymbol} selected={symbol} />
        <KimpBotControl symbol={symbol} />
      </div>
    </div>
  );
}
