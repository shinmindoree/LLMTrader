"use client";

import { useEffect, useRef, useMemo, useCallback, useState } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";
import { formatBinanceTime, useTimezone } from "@/lib/timeFormat";

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

type TooltipState = {
  x: number;
  y: number;
  trades: MarkerTrade[];
};

type SnappedTrade = { trade: MarkerTrade; snappedSec: number };

// Pick the index of the candle whose ``close_time`` (seconds) is nearest
// to ``tSec``. ``candleSecs`` MUST be sorted ascending. Returns -1 when
// the input is empty so callers can fall back gracefully.
function nearestCandleIdx(candleSecs: number[], tSec: number): number {
  if (candleSecs.length === 0) return -1;
  let lo = 0;
  let hi = candleSecs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (candleSecs[mid] < tSec) lo = mid + 1;
    else hi = mid;
  }
  // ``lo`` is the smallest index with ``candleSecs[lo] >= tSec`` (or the
  // final index if all entries are < tSec). Compare with the previous
  // candle to pick the truly nearest neighbour.
  if (lo > 0) {
    const prevDist = tSec - candleSecs[lo - 1];
    const currDist = candleSecs[lo] - tSec;
    if (prevDist <= currDist) return lo - 1;
  }
  return lo;
}

// Classify a marker trade as entry vs exit. ``pnl`` alone is unreliable
// because Binance reports ``realized_pnl == 0`` (numeric) on every entry
// fill, so ``pnl !== null`` would flag every live entry as an exit. The
// runner only fills ``exit_reason`` when the strategy closes a position,
// so it's the canonical signal. A non-zero pnl is kept as a fallback
// exit indicator for legacy trades that pre-date the ``exit_reason``
// plumbing.
function isExitTrade(t: MarkerTrade): boolean {
  return t.exitReason != null || (t.pnl != null && t.pnl !== 0);
}

export function BacktestExecutionChart({
  chart: chartPayload,
  trades,
  height,
}: {
  chart: BacktestChartPayload;
  trades: MarkerTrade[];
  height?: number;
}) {
  const mainRef = useRef<HTMLDivElement>(null);
  const oscRef = useRef<HTMLDivElement>(null);
  const chartWrapperRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const { tz } = useTimezone();

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

  // Snap each trade to the close_time of its nearest candle. Live trade
  // timestamps are Binance fill times that fall mid-candle (or even
  // outside the visible window for the first/last entries), which used
  // to break tooltip hover because the crosshair always reports the
  // candle-snapped time. Snapping fixes three things at once:
  //   1. markers always render on a real data point
  //   2. the crosshair's ``param.time`` exact-matches ``tradesByTime``
  //   3. the effective hover zone becomes the full candle width
  // Backtests already align trades to candle close_time so snapping is a
  // no-op for them.
  const snappedTrades = useMemo<SnappedTrade[]>(() => {
    if (candles.length === 0) return [];
    const candleSecs = candles.map((c) => Math.floor(c.close_time / 1000));
    const result: SnappedTrade[] = [];
    for (const trade of trades) {
      if (trade.timestamp == null) continue;
      const tSec = trade.timestamp / 1000;
      const idx = nearestCandleIdx(candleSecs, tSec);
      if (idx < 0) continue;
      result.push({ trade, snappedSec: candleSecs[idx] });
    }
    return result;
  }, [trades, candles]);

  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    const result: SeriesMarker<Time>[] = [];
    for (const { trade, snappedSec } of snappedTrades) {
      if (!trade.price) continue;
      const time = snappedSec as Time;
      const isExit = isExitTrade(trade);
      const side = trade.side ?? "";

      if (!isExit && side === "BUY") {
        result.push({
          time,
          position: "belowBar",
          color: "#00e676",
          shape: "arrowUp",
          text: "LE",
        });
      } else if (!isExit && side === "SELL") {
        result.push({
          time,
          position: "aboveBar",
          color: "#ff5252",
          shape: "arrowDown",
          text: "SE",
        });
      } else if (isExit && side === "SELL") {
        result.push({
          time,
          position: "aboveBar",
          color: "#40c4ff",
          shape: "arrowDown",
          text: "Exit",
        });
      } else if (isExit && side === "BUY") {
        result.push({
          time,
          position: "belowBar",
          color: "#ffb74d",
          shape: "arrowUp",
          text: "Exit",
        });
      }
    }
    result.sort((a, b) => (a.time as number) - (b.time as number));
    return result;
  }, [snappedTrades]);

  const tradesByTime = useMemo(() => {
    const map = new Map<number, MarkerTrade[]>();
    for (const { trade, snappedSec } of snappedTrades) {
      const list = map.get(snappedSec) ?? [];
      list.push(trade);
      map.set(snappedSec, list);
    }
    return map;
  }, [snappedTrades]);

  const tradesByTimeRef = useRef(tradesByTime);
  useEffect(() => {
    tradesByTimeRef.current = tradesByTime;
  }, [tradesByTime]);

  const chartDataKey = useMemo(() => {
    if (!candles.length) return "";
    const first = candles[0]?.close_time ?? 0;
    const last = candles[candles.length - 1]?.close_time ?? 0;
    const overlayKey = overlayIndicators.map((s) => s.id).join(",");
    const oscKey = oscillatorIndicators.map((s) => s.id).join(",");
    return `${candles.length}-${first}-${last}-${hasOscillator}-${overlayKey}-${oscKey}`;
  }, [candles, hasOscillator, overlayIndicators, oscillatorIndicators]);

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

    const oscHeight = 160;
    const mainHeight = height
      ? (hasOscillator ? height - oscHeight - 4 : height)
      : (hasOscillator ? 400 : 560);
    const mainChart = buildChart(mainContainer, Math.max(mainHeight, 200));

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

      oscChart = buildChart(oscContainer, oscHeight);

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

    const crosshairHandler = (param: { point?: { x: number; y: number }; time?: unknown }) => {
      if (!param.point) {
        setTooltip(null);
        return;
      }
      const timeSec = typeof param.time === "number" ? param.time : undefined;
      if (timeSec === undefined) {
        setTooltip(null);
        return;
      }
      // The crosshair always snaps ``param.time`` to a candle's
      // ``close_time`` (in seconds), which is exactly how we key
      // ``tradesByTime`` after snapping in ``snappedTrades``. Exact
      // lookup is therefore sufficient — no fuzzy fallback needed.
      const found = tradesByTimeRef.current.get(timeSec) ?? [];
      if (found.length) {
        setTooltip({ x: param.point.x, y: param.point.y, trades: found });
      } else {
        setTooltip(null);
      }
    };
    mainChart.subscribeCrosshairMove(crosshairHandler);

    return () => {
      mainChart.unsubscribeCrosshairMove(crosshairHandler);
      window.removeEventListener("resize", handleResize);
      mainChart.remove();
      oscChart?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartDataKey, buildChart, height]);

  if (!candles.length) {
    return (
      <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-xs text-[#868993]">
        백테스트 차트 데이터가 없습니다.
      </div>
    );
  }

  const isEmbedded = height !== undefined;

  return (
    <section className={isEmbedded ? "bg-[#131722] px-2 py-1" : "mb-4 rounded border border-[#2a2e39] bg-[#131722] p-4"}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
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
      <div ref={chartWrapperRef} className="relative">
        <div ref={mainRef} />
        {tooltip && (
          <div
            className="pointer-events-none absolute z-10 min-w-[200px] rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-xs shadow-lg"
            style={{
              left: tooltip.x,
              top: tooltip.y - 8,
              transform: "translate(-50%, -100%)",
            }}
          >
            {tooltip.trades.map((t, i) => {
              const isExit = isExitTrade(t);
              return (
              <div key={i} className={i > 0 ? "mt-2 border-t border-[#2a2e39] pt-2" : ""}>
                <div className="space-y-1 text-[#d1d4dc]">
                  <div className="font-medium">
                    {isExit ? "Exit" : t.side === "BUY" ? "Long Entry" : "Short Entry"}
                  </div>
                  <div className="text-[#868993]">
                    Time: {t.timestamp != null ? formatBinanceTime(t.timestamp, tz) : "-"}
                  </div>
                  <div>Side: {t.side ?? "-"}</div>
                  <div>Price: {t.price != null ? t.price.toFixed(2) : "-"}</div>
                  {isExit && t.pnl != null && (
                    <div className={t.pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}>
                      Realized Profit: {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)} USDT
                    </div>
                  )}
                  {t.reason && <div className="text-[#868993]">Reason: {t.reason}</div>}
                  {t.exitReason && <div className="text-[#868993]">Exit Reason: {t.exitReason}</div>}
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>
      {hasOscillator && <div ref={oscRef} className="mt-1" />}
    </section>
  );
}
