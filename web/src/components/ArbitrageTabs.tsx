"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n } from "@/lib/i18n";

export const ARBITRAGE_PATHS = [
  "/arbitrage",
  "/arbitrage/kimp",
  "/arbitrage/funding",
  "/arbitrage/basis",
  "/arbitrage/statistical",
  "/arbitrage/triangular",
  "/arbitrage/options-delta",
];

const SUB_TABS = [
  { href: "/arbitrage", labelKey: "arbitrageOverview" as const, exact: true },
  { href: "/arbitrage/kimp", labelKey: "arbitrageKimp" as const },
  { href: "/arbitrage/funding", labelKey: "arbitrageFunding" as const },
  { href: "/arbitrage/basis", labelKey: "arbitrageBasis" as const },
  { href: "/arbitrage/statistical", labelKey: "arbitrageStatistical" as const },
  { href: "/arbitrage/triangular", labelKey: "arbitrageTriangular" as const },
  { href: "/arbitrage/options-delta", labelKey: "arbitrageOptionsDelta" as const },
];

export function isArbitragePath(pathname: string): boolean {
  return pathname === "/arbitrage" || pathname.startsWith("/arbitrage/");
}

function isActive(pathname: string, href: string, exact?: boolean): boolean {
  if (exact) return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function ArbitrageTabs() {
  const pathname = usePathname();
  const { t } = useI18n();

  return (
    <div className="flex shrink-0 overflow-x-auto border-b border-[#2a2e39] bg-[#1e222d]">
      {SUB_TABS.map((tab) => {
        const active = isActive(pathname, tab.href, tab.exact);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={[
              "relative whitespace-nowrap px-5 py-2.5 text-sm font-medium transition-colors",
              active
                ? "text-[#d1d4dc]"
                : "text-[#868993] hover:text-[#d1d4dc]",
            ].join(" ")}
          >
            {t.nav[tab.labelKey]}
            {active && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#2962ff]" />
            )}
          </Link>
        );
      })}
    </div>
  );
}
