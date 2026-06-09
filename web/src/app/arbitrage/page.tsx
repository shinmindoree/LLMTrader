"use client";

import Link from "next/link";
import { HubHeader, StrategyCard } from "@/components/StrategyHub";
import { useI18n } from "@/lib/i18n";

type ArbitrageStrategy = {
  name: string;
  badge: string;
  desc: string;
  href: string;
  active: boolean;
};

export default function ArbitragePage() {
  const { t } = useI18n();
  const h = t.hubs.arbitrage;
  const ov = h.overview;

  const strategies: ArbitrageStrategy[] = [
    { ...h.kimp, href: "/arbitrage/kimp", active: false },
    { ...h.funding, href: "/arbitrage/funding", active: true },
    { ...h.basis, href: "/arbitrage/basis", active: false },
    { ...h.statistical, href: "/arbitrage/statistical", active: false },
    { ...h.triangular, href: "/arbitrage/triangular", active: false },
    { ...h.optionsDelta, href: "/arbitrage/options-delta", active: false },
  ];

  return (
    <div className="w-full max-w-6xl px-4 py-6">
      <HubHeader title={ov.title} subtitle={ov.subtitle} />
      <div className="space-y-4">
        {strategies.map((item) => (
          <StrategyCard
            key={item.href}
            name={item.name}
            badge={item.badge}
            desc={item.desc}
            active={item.active}
            statusLabel={item.active ? t.hubs.statusActive : t.hubs.statusPlanned}
          >
            <Link
              href={item.href}
              className="inline-flex items-center text-sm font-medium text-[#2962ff] hover:underline"
            >
              {ov.configureLink} →
            </Link>
          </StrategyCard>
        ))}
      </div>
    </div>
  );
}
