"use client";

import { usePathname } from "next/navigation";
import { TradingViewChart } from "@/components/TradingViewChart";
import { TradingTabs } from "@/components/TradingTabs";
import { DashboardPanel } from "@/components/DashboardPanel";

const TRADING_PATHS = ["/strategies", "/backtest", "/live"];

function isTradingPage(pathname: string): boolean {
  return TRADING_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (pathname === "/") {
    return <div className="min-w-0 flex-1">{children}</div>;
  }

  if (isTradingPage(pathname)) {
    return (
      <div className="grid h-[calc(100vh-3.5rem)] grid-cols-1 md:grid-cols-[1fr_320px]">
        <div className="flex flex-col overflow-hidden">
          <div className="h-[45vh] min-h-[250px] border-b border-[#2a2e39]">
            <TradingViewChart />
          </div>
          <TradingTabs />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
        <div className="hidden md:block">
          <DashboardPanel />
        </div>
      </div>
    );
  }

  return <div className="min-w-0 flex-1">{children}</div>;
}
