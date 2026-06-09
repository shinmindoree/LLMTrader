"use client";

import { PlannedStrategyPlaceholder } from "@/components/PlannedStrategyPlaceholder";
import { useI18n } from "@/lib/i18n";

export default function ArbitrageOptionsDeltaPage() {
  const { t } = useI18n();
  const s = t.hubs.arbitrage.optionsDelta;
  return <PlannedStrategyPlaceholder name={s.name} badge={s.badge} desc={s.desc} />;
}
