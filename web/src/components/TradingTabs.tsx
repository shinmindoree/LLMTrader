"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n } from "@/lib/i18n";

const TABS = [
  { href: "/strategies", labelKey: "strategies" as const },
  { href: "/backtest", labelKey: "backtest" as const },
  { href: "/live", labelKey: "live" as const },
];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function TradingTabs() {
  const pathname = usePathname();
  const { t } = useI18n();

  return (
    <div className="flex border-b border-[#2a2e39] bg-[#1e222d]">
      {TABS.map((tab) => {
        const active = isActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={[
              "relative px-5 py-2.5 text-sm font-medium transition-colors",
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
