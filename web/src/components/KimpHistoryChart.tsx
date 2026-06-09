"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  ColorType,
  CrosshairMode,
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { useI18n } from "@/lib/i18n";
import { getKimpHistory } from "@/lib/api";
import type { KimpHistoryRange } from "@/lib/types";

const RANGES: KimpHistoryRange[] = ["1H", "1D", "7D", "30D"];

function fmtPct(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

type Props = {
  symbol: string;
};

export default function KimpHistoryChart({ symbol }: Props) {
  const { t } = useI18n();
  const h = t.hubs.arbitrage.kimp.history;
  const [range, setRange] = useState<KimpHistoryRange>("1D");

  const { data, isLoading, error } = useSWR(
    symbol ? ["kimp:history", symbol, range] : null,
    () => getKimpHistory(symbol, range),
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const meanRef = useRef<ISeriesApi<"Line"> | null>(null);
  const upperRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lowerRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#13141a" },
        textColor: "#868993",
        fontFamily: "ui-sans-serif, system-ui",
      },
      grid: {
        vertLines: { color: "#1f2027" },
        horzLines: { color: "#1f2027" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { borderColor: "#26272d", timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#26272d" },
    });
    chartRef.current = chart;
    lineRef.current = chart.addLineSeries({
      color: "#60a5fa",
      lineWidth: 2,
      priceFormat: { type: "percent", precision: 3, minMove: 0.0001 },
    });
    meanRef.current = chart.addLineSeries({
      color: "#868993",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "percent", precision: 3, minMove: 0.0001 },
    });
    upperRef.current = chart.addLineSeries({
      color: "#fbbf24",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "percent", precision: 3, minMove: 0.0001 },
    });
    lowerRef.current = chart.addLineSeries({
      color: "#fbbf24",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "percent", precision: 3, minMove: 0.0001 },
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
      meanRef.current = null;
      upperRef.current = null;
      lowerRef.current = null;
    };
  }, []);

  // lightweight-charts는 시간을 초 단위(epoch sec) 또는 BusinessDay 로 받는다.
  // 동일 ts 가 두 번 나오면 add 가 거부되므로 dedupe + 정렬한다.
  const seriesData = useMemo(() => {
    const map = new Map<number, number>();
    for (const p of data?.series ?? []) {
      const tsSec = Math.floor(p.t / 1000);
      map.set(tsSec, p.p * 100); // percent series로 변환
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([ts, value]) => ({ time: ts as Time, value }));
  }, [data]);

  useEffect(() => {
    if (!lineRef.current) return;
    lineRef.current.setData(seriesData);

    const meanPct = data?.mean_pct ?? null;
    const stdPct = data?.std_pct ?? null;
    if (meanRef.current && upperRef.current && lowerRef.current && seriesData.length > 0) {
      if (meanPct != null) {
        const meanValue = meanPct * 100;
        const band = stdPct != null ? stdPct * 100 : 0;
        const meanArr = seriesData.map((d) => ({ time: d.time, value: meanValue }));
        meanRef.current.setData(meanArr);
        upperRef.current.setData(
          seriesData.map((d) => ({ time: d.time, value: meanValue + band })),
        );
        lowerRef.current.setData(
          seriesData.map((d) => ({ time: d.time, value: meanValue - band })),
        );
      } else {
        meanRef.current.setData([]);
        upperRef.current.setData([]);
        lowerRef.current.setData([]);
      }
    }

    if (chartRef.current && seriesData.length > 0) {
      chartRef.current.timeScale().fitContent();
    }
  }, [seriesData, data?.mean_pct, data?.std_pct]);

  const isEmpty = !isLoading && (data?.series?.length ?? 0) === 0;

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#26272d] px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-white">
            {h.title} · <span className="text-[#60a5fa]">{symbol}</span>
          </div>
          <div className="text-xs text-[#868993]">{h.subtitle}</div>
        </div>
        <div className="flex gap-1">
          {RANGES.map((r) => {
            const labelMap: Record<KimpHistoryRange, string> = {
              "1H": h.range1H,
              "1D": h.range1D,
              "7D": h.range7D,
              "30D": h.range30D,
            };
            return (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                className={`rounded-md border px-2 py-1 text-[11px] ${
                  r === range
                    ? "border-[#3a3b44] bg-[#22232b] text-white"
                    : "border-[#26272d] bg-[#1a1b22] text-[#c3c5cc] hover:bg-[#22232b]"
                }`}
              >
                {labelMap[r]}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 border-b border-[#26272d] px-4 py-2 text-[11px] text-[#868993]">
        <div>
          {h.mean}:{" "}
          <span className="text-[#c3c5cc] tabular-nums">{fmtPct(data?.mean_pct)}</span>
        </div>
        <div>
          {h.std}:{" "}
          <span className="text-[#c3c5cc] tabular-nums">{fmtPct(data?.std_pct)}</span>
        </div>
        <div>
          {h.samples}:{" "}
          <span className="text-[#c3c5cc] tabular-nums">{data?.n_samples ?? 0}</span>
        </div>
      </div>

      <div className="relative h-[320px] w-full">
        <div ref={containerRef} className="absolute inset-0" />
        {isEmpty ? (
          <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-xs text-[#868993]">
            {h.empty}
          </div>
        ) : null}
        {error ? (
          <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-xs text-rose-400">
            {t.hubs.arbitrage.kimp.common.loadFailed}
          </div>
        ) : null}
      </div>
    </div>
  );
}
