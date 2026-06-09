"use client";

import { HubHeader, StrategyCard } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

export default function ArbitrageKimpPage() {
  const { t } = useI18n();
  const k = t.hubs.arbitrage.kimp;

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={k.title} subtitle={k.subtitle} />
      <StrategyCard
        name={k.name}
        badge={k.badge}
        desc={k.desc}
        active={false}
        statusLabel={t.hubs.statusPlanned}
      >
        <p className="text-xs text-[#868993]">{k.comingSoonNote}</p>
      </StrategyCard>
    </div>
  );
}
