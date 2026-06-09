"use client";

import Link from "next/link";
import { useI18n } from "@/lib/i18n";
import { HubHeader } from "@/components/StrategyHub";

export function PlannedStrategyPlaceholder({
  name,
  badge,
  desc,
}: {
  name: string;
  badge: string;
  desc: string;
}) {
  const { t } = useI18n();
  const p = t.hubs.arbitrage.plannedPage;

  return (
    <div className="w-full max-w-4xl px-4 py-6">
      <HubHeader title={name} subtitle={desc} />
      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <div className="mb-3 flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-[#2962ff]/15 px-2.5 py-0.5 text-[11px] font-medium text-[#2962ff]">
            {badge}
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#868993]/15 px-2.5 py-0.5 text-[11px] font-medium text-[#868993]">
            <span className="h-1.5 w-1.5 rounded-full bg-[#868993]" />
            {p.title}
          </span>
        </div>
        <h2 className="mb-2 text-base font-semibold text-[#d1d4dc]">
          {p.title}
        </h2>
        <p className="mb-4 text-sm text-[#868993]">{p.description}</p>
        <Link
          href="/arbitrage"
          className="inline-flex items-center text-sm font-medium text-[#2962ff] hover:underline"
        >
          ← {p.backToOverview}
        </Link>
      </div>
    </div>
  );
}
