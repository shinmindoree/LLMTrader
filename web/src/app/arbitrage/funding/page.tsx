"use client";

import { ArbitrageConfigPanel } from "@/components/ArbitrageConfigPanel";
import { HubHeader, StrategyCard } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

export default function ArbitrageFundingPage() {
  const { t } = useI18n();
  const h = t.hubs.arbitrage;

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={h.funding.name} subtitle={h.funding.desc} />
      <StrategyCard
        name={h.funding.name}
        badge={h.funding.badge}
        desc={h.funding.desc}
        active
        statusLabel={t.hubs.statusActive}
      >
        <ArbitrageConfigPanel />
      </StrategyCard>
    </div>
  );
}
