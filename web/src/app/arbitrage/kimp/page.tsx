"use client";

import { useMemo, useState } from "react";

import KimpFxWidget from "@/components/KimpFxWidget";
import KimpHistoryChart from "@/components/KimpHistoryChart";
import KimpScreenerTable from "@/components/KimpScreenerTable";
import { HubHeader } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";
import { useKimpScreenerStream } from "@/lib/useKimpScreenerStream";

export default function ArbitrageKimpPage() {
  const { t } = useI18n();
  const k = t.hubs.arbitrage.kimp;
  const [symbol, setSymbol] = useState<string>("BTC");
  const screener = useKimpScreenerStream();
  const selectedItem = useMemo(
    () => screener.data?.items.find((item) => item.symbol === symbol) ?? null,
    [screener.data?.items, symbol],
  );

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={k.title} subtitle={k.subtitle} />

      <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-4">
          <KimpScreenerTable
            symbol={symbol}
            onSelect={setSymbol}
            data={screener.data}
            error={screener.error}
            isLoading={screener.isLoading}
            isValidating={screener.isValidating}
            status={screener.status}
            onRefresh={screener.refetch}
          />
          <KimpHistoryChart
            symbol={symbol}
            latest={selectedItem}
            latestAsOf={screener.data?.as_of ?? null}
          />
        </div>
        <div className="flex flex-col gap-4">
          <KimpFxWidget rate={screener.data?.fx ?? null} onRefresh={screener.refetch} />
        </div>
      </div>
    </div>
  );
}
