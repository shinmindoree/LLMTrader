"use client";

import { ArbitrageConfigPanel } from "@/components/ArbitrageConfigPanel";
import { HubHeader, StrategyCard } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

export default function ArbitragePage() {
  const { t } = useI18n();
  const h = t.hubs.arbitrage;

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={h.title} subtitle={h.subtitle} />
      <div className="space-y-4">
        <StrategyCard
          name={h.funding.name}
          badge={h.funding.badge}
          desc={h.funding.desc}
          active
          statusLabel={t.hubs.statusActive}
        >
          <ArbitrageConfigPanel />
        </StrategyCard>

        {[h.basis, h.statistical, h.triangular, h.optionsDelta].map((item) => (
          <StrategyCard
            key={item.name}
            name={item.name}
            badge={item.badge}
            desc={item.desc}
            active={false}
            statusLabel={t.hubs.statusPlanned}
          >
            <p className="text-xs text-[#868993]">{t.hubs.plannedNote}</p>
          </StrategyCard>
        ))}
      </div>
    </div>
  );
}
