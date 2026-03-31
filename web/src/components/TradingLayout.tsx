"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { TradingTabs } from "@/components/TradingTabs";
const TradingViewChart = dynamic(
  () => import("@/components/TradingViewChart").then((mod) => mod.TradingViewChart),
  { ssr: false },
);
const BacktestExecutionChart = dynamic(
  () => import("@/components/BacktestExecutionChart").then((mod) => mod.BacktestExecutionChart),
  { ssr: false },
);
import { TopChartProvider, useTopChart } from "@/components/TopChartContext";

export function TradingLayout({ children }: { children: React.ReactNode }) {
  return (
    <TopChartProvider>
      <TradingLayoutInner>{children}</TradingLayoutInner>
    </TopChartProvider>
  );
}

function TradingLayoutInner({ children }: { children: React.ReactNode }) {
  const { backtestChart } = useTopChart();
  const pathname = usePathname();
  const isChartTab = pathname === "/chart" || pathname.startsWith("/chart/");

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col overflow-hidden bg-[#131722]">
      {!isChartTab ? (
        <div className="shrink-0 sm:hidden">
          <TradingTabs />
        </div>
      ) : null}
      {isChartTab ? (
        <div className="min-h-0 flex-1">
          {backtestChart ? (
            <BacktestExecutionChart
              chart={backtestChart.chart}
              trades={backtestChart.trades}
            />
          ) : (
            <TradingViewChart />
          )}
        </div>
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-x-hidden overflow-y-auto bg-[#131722]">
          {children}
        </div>
      )}
    </div>
  );
}
