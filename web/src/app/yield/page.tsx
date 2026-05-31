"use client";

import { HubHeader, StrategyCard } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

export default function YieldPage() {
  const { t } = useI18n();
  const h = t.hubs.yield;

  return (
    <div className="w-full max-w-3xl px-4 py-6">
      <HubHeader title={h.title} subtitle={h.subtitle} />
      <div className="space-y-4">
        {[h.simpleEarn, h.airdrop].map((item) => (
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
