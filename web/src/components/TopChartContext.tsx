"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import type { MarkerTrade } from "@/components/BacktestExecutionChart";

type BacktestChartPayload = {
  symbol?: string;
  interval?: string;
  candles?: Array<{
    open_time: number;
    close_time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  indicator_config?: Record<string, unknown>;
  indicator_series?: Array<{
    id: string;
    indicator: string;
    output: string | null;
    label: string;
    pane: "overlay" | "oscillator";
    values: Array<number | null>;
  }>;
};

type TopChartData = {
  chart: BacktestChartPayload;
  trades: MarkerTrade[];
};

type TopChartContextValue = {
  backtestChart: TopChartData | null;
  setBacktestChart: (data: TopChartData | null) => void;
};

const TopChartContext = createContext<TopChartContextValue>({
  backtestChart: null,
  setBacktestChart: () => {},
});

export function TopChartProvider({ children }: { children: ReactNode }) {
  const [backtestChart, setBacktestChartState] = useState<TopChartData | null>(null);
  const setBacktestChart = useCallback((data: TopChartData | null) => {
    setBacktestChartState(data);
  }, []);

  return (
    <TopChartContext.Provider value={{ backtestChart, setBacktestChart }}>
      {children}
    </TopChartContext.Provider>
  );
}

export function useTopChart() {
  return useContext(TopChartContext);
}
