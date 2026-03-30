"use client";

import { BinanceAccountPanel } from "@/components/BinanceAccountPanel";
import { useI18n } from "@/lib/i18n";
import type { BinanceKeysStatus } from "@/lib/types";

export function AssetOverviewPanel({ keysStatus }: { keysStatus: BinanceKeysStatus | null }) {
  const { t } = useI18n();
  const binanceConnected = !!keysStatus?.configured;

  return (
    <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-[#d1d4dc]">{t.assetOverview.title}</h2>
        <span className="rounded bg-[#F0B90B]/15 px-2 py-0.5 text-[11px] font-semibold text-[#F0B90B]">
          USDⓈ-M
        </span>
        <span
          className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-medium ${
            binanceConnected
              ? "bg-[#26a69a]/15 text-[#26a69a]"
              : "bg-[#ef5350]/15 text-[#ef5350]"
          }`}
        >
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              binanceConnected ? "bg-[#26a69a]" : "bg-[#ef5350]"
            }`}
          />
          {binanceConnected ? t.dashboard.statusConnected : t.dashboard.statusNotConnected}
        </span>
      </div>
      <p className="mt-1 text-xs text-[#868993]">{t.assetOverview.subtitle}</p>

      <div className="mt-4">
        <BinanceAccountPanel embedded />
      </div>
    </section>
  );
}
