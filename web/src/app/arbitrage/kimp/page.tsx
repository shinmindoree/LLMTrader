"use client";

import { useState } from "react";

import KimpFxWidget from "@/components/KimpFxWidget";
import KimpHistoryChart from "@/components/KimpHistoryChart";
import KimpScreenerTable from "@/components/KimpScreenerTable";
import { HubHeader } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

export default function ArbitrageKimpPage() {
  const { t } = useI18n();
  const k = t.hubs.arbitrage.kimp;
  const [symbol, setSymbol] = useState<string>("BTC");

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={k.title} subtitle={k.subtitle} />

      <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-4">
          <KimpScreenerTable symbol={symbol} onSelect={setSymbol} />
          <KimpHistoryChart symbol={symbol} />
        </div>
        <div className="flex flex-col gap-4">
          <KimpFxWidget />
        </div>
      </div>
    </div>
  );
}
