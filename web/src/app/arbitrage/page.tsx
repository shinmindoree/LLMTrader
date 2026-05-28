"use client";

import { ArbitrageConfigPanel } from "@/components/ArbitrageConfigPanel";

export default function ArbitragePage() {
  return (
    <div className="w-full max-w-3xl px-4 py-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-[#d1d4dc]">Funding Rate Arbitrage</h1>
        <p className="mt-1 text-sm text-[#868993]">
          현물 롱 + 선물 숏 delta-neutral 전략으로 펀딩비를 자동 수취합니다.
        </p>
      </header>
      <ArbitrageConfigPanel />
    </div>
  );
}
