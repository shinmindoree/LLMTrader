"use client";

import { useEffect, useRef, useMemo, useCallback } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";

type CandlePoint = {
  open_time: number;
  close_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type IndicatorSeries = {
  id: string;
  indicator: string;
  output: string | null;
  label: string;
  pane: "overlay" | "oscillator";
  values: Array<number | null>;
};

type BacktestChartPayload = {
  symbol?: string;
  interval?: string;
  candles?: CandlePoint[];
  indicator_config?: Record<string, unknown>;
  indicator_series?: IndicatorSeries[];
};

export type MarkerTrade = {
  timestamp: number | null;
  side: string | null;
  price: number | null;
  pnl: number | null;
  reason: string | null;
  exitReason: string | null;
};

const PALETTE = [
  "#42a5f5",
  "#26a69a",
  "#ab47bc",
  "#ffa726",
  "#ef5350",
  "#66bb6a",
  "#29b6f6",
  "#ec407a",
];

function colorAt(index: number): string {
  return PALETTE[index % PALETTE.length];
}

function msToSec(ms: number): Time {
  return Math.floor(ms / 1000) as Time;
}

export function BacktestExecutionChart({
  chart: chartPayload,
  trades,
}: {
  chart: BacktestChartPayload;
  trades: MarkerTrade[];
}) {
  const mainRef = useRef<HTMLDivElement>(null);
  const oscRef = useRef<HTMLDivElement>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const oscChartRef = useRef<IChartApi | null>(null);

  const candles = useMemo(() => {
    if (!Array.isArray(chartPayload.candles)) return [];
    return chartPayload.candles.filter(
      (c) =>
        c &&
        typeof c.close_time === "number" &&
        Number.isFinite(c.open) &&
        Number.isFinite(c.high) &&
        Number.isFinite(c.low) &&
        Number.isFinite(c.close),
    );
  }, [chartPayload.candles]);

  const overlayIndicators = useMemo(() => {
    if (!Array.isArray(chartPayload.indicator_series)) return [];
    return chartPayload.indicator_series.filter((s) => s.pane === "overlay");
  }, [chartPayload.indicator_series]);

  const oscillatorIndicators = useMemo(() => {
    if (!Array.isArray(chartPayload.indicator_series)) return [];
    return chartPayload.indicator_series.filter((s) => s.pane === "oscillator");
  }, [chartPayload.indicator_series]);

  const hasOscillator = oscillatorIndicators.length > 0;

  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    const result: SeriesMarker<Time>[] = [];
    for (const trade of trades) {
      if (!trade.timestamp || !trade.price) continue;
      const time = msToSec(trade.timestamp);
      const isExit = trade.pnl !== null;
      const side = trade.side ?? "";

      if (!isExit && side === "BUY") {
        result.push({
          time,
          position: "belowBar",
          color: "#00e676",
          shape: "arrowUp",
          text: `Long ${trade.reason ?? ""}`.trim(),
        });
      } else if (!isExit && side === "SELL") {
        result.push({
          time,
          position: "aboveBar",
          color: "#ff5252",
          shape: "arrowDown",
          text: `Short ${trade.reason ?? ""}`.trim(),
        });
      } else if (isExit && side === "SELL") {
        const pnlStr = trade.pnl !== null ? ` (${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)})` : "";
        result.push({
          time,
          position: "aboveBar",
          color: "#40c4ff",
          shape: "arrowDown",
          text: `Exit Long${pnlStr}`,
        });
      } else if (isExit && side === "BUY") {
        const pnlStr = trade.pnl !== null ? ` (${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)})` : "";
        result.push({
          time,
          position: "belowBar",
          color: "#ffb74d",
          shape: "arrowUp",
          text: `Exit Short${pnlStr}`,
        });
      }
    }
    result.sort((a, b) => (a.time as number) - (b.time as number));
    return result;
  }, [trades]);

  const indicatorNames = useMemo(() => {
    const config = chartPayload.indicator_config;
    if (!config || typeof config !== "object") return [] as string[];
    return Object.keys(config);
  }, [chartPayload.indicator_config]);

  const buildChart = useCallback(
    (container: HTMLDivElement, height: number) => {
      return createChart(container, {
        width: container.clientWidth,
        height,
        layout: {
          background: { type: ColorType.Solid, color: "#131722" },
          textColor: "#d1d4dc",
        },
        grid: {
          vertLines: { color: "rgba(42,46,57,0.6)" },
          horzLines: { color: "rgba(42,46,57,0.6)" },
        },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#2a2e39" },
        timeScale: {
          borderColor: "#2a2e39",
          timeVisible: true,
          secondsVisible: false,
        },
      });
    },
    [],
  );

  useEffect(() => {
    if (!mainRef.current || !candles.length) return;

    const mainContainer = mainRef.current;
    mainContainer.innerHTML = "";

    const mainChart = buildChart(mainContainer, hasOscillator ? 400 : 560);
    mainChartRef.current = mainChart;

    const candleSeries = mainChart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });

    const candleData = candles.map((c) => ({
      time: msToSec(c.close_time),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeries.setData(candleData);

    if (markers.length > 0) {
      candleSeries.setMarkers(markers);
    }

    const overlaySeries: ISeriesApi<"Line">[] = [];
    overlayIndicators.forEach((series, idx) => {
      const lineData: Array<{ time: Time; value: number }> = [];
      candles.forEach((c, i) => {
        const v = series.values?.[i];
        if (typeof v === "number" && Number.isFinite(v)) {
          lineData.push({ time: msToSec(c.close_time), value: v });
        }
      });
      if (!lineData.length) return;

      const lineSeries = mainChart.addLineSeries({
        color: colorAt(idx),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: series.label,
      });
      lineSeries.setData(lineData);
      overlaySeries.push(lineSeries);
    });

    let oscChart: IChartApi | null = null;
    const oscSeriesList: ISeriesApi<"Line" | "Histogram">[] = [];

    if (hasOscillator && oscRef.current) {
      const oscContainer = oscRef.current;
      oscContainer.innerHTML = "";

      oscChart = buildChart(oscContainer, 160);
      oscChartRef.current = oscChart;

      oscillatorIndicators.forEach((series, idx) => {
        const useHistogram =
          series.indicator.toUpperCase().includes("MACD") &&
          (series.output?.toLowerCase() === "histogram" ||
            series.output?.toLowerCase() === "hist");

        const lineData: Array<{ time: Time; value: number; color?: string }> = [];
        candles.forEach((c, i) => {
          const v = series.values?.[i];
          if (typeof v === "number" && Number.isFinite(v)) {
            const point: { time: Time; value: number; color?: string } = {
              time: msToSec(c.close_time),
              value: v,
            };
            if (useHistogram) {
              point.color = v >= 0 ? "#26a69a" : "#ef5350";
            }
            lineData.push(point);
          }
        });
        if (!lineData.length) return;

        if (useHistogram) {
          const hSeries = oscChart!.addHistogramSeries({
            priceLineVisible: false,
            lastValueVisible: false,
            title: series.label,
          });
          hSeries.setData(lineData);
          oscSeriesList.push(hSeries);
        } else {
          const lSeries = oscChart!.addLineSeries({
            color: colorAt(idx + overlayIndicators.length),
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: series.label,
          });
          lSeries.setData(lineData);
          oscSeriesList.push(lSeries);
        }
      });

      mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) oscChart?.timeScale().setVisibleLogicalRange(range);
      });
      oscChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) mainChart.timeScale().setVisibleLogicalRange(range);
      });
    }

    mainChart.timeScale().fitContent();
    oscChart?.timeScale().fitContent();

    const handleResize = () => {
      mainChart.applyOptions({ width: mainContainer.clientWidth });
      oscChart?.applyOptions({ width: oscRef.current?.clientWidth ?? mainContainer.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      mainChart.remove();
      oscChart?.remove();
      mainChartRef.current = null;
      oscChartRef.current = null;
    };
  }, [candles, markers, overlayIndicators, oscillatorIndicators, hasOscillator, buildChart]);

  if (!candles.length) {
    return (
      <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-xs text-[#868993]">
        백테스트 차트 데이터가 없습니다.
      </div>
    );
  }

  return (
    <section className="mb-4 rounded border border-[#2a2e39] bg-[#131722] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#d1d4dc]">
          {chartPayload.symbol ?? "-"}
        </span>
        <span className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#d1d4dc]">
          {chartPayload.interval ?? "-"}
        </span>
        {indicatorNames.map((name) => (
          <span key={name} className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#868993]">
            {name}
          </span>
        ))}
      </div>
      <div ref={mainRef} />
      {hasOscillator && <div ref={oscRef} className="mt-1" />}
    </section>
  );
}
