"use client";

import { BinanceAccountPanel } from "@/components/BinanceAccountPanel";
import { useI18n } from "@/lib/i18n";

export function AssetOverviewPanel() {
  const { t } = useI18n();

  return (
    <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-[#d1d4dc]">{t.assetOverview.title}</h2>
        <span className="rounded bg-[#F0B90B]/15 px-2 py-0.5 text-[11px] font-semibold text-[#F0B90B]">
          USDⓈ-M
        </span>
      </div>
      <p className="mt-1 max-w-2xl text-xs text-[#868993]">{t.assetOverview.subtitle}</p>

      <div className="mt-4">
        <BinanceAccountPanel embedded />
      </div>
    </section>
  );
}
