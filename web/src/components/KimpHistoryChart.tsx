"use client";

import { useEffect, useMemo, useRef } from "react";
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
import { getKimpPctForMode } from "@/lib/kimp";
import type {
  KimpFxRateResponse,
  KimpHistoryRange,
  KimpRateMode,
  KimpScreenerItem,
} from "@/lib/types";

const RANGE_SECONDS: Partial<Record<KimpHistoryRange, number>> = {
  "1H": 60 * 60,
  "1D": 24 * 60 * 60,
  "7D": 7 * 24 * 60 * 60,
  "30D": 30 * 24 * 60 * 60,
};

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtChartPct(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const rounded = Math.abs(v) < 0.005 ? 0 : v;
  return `${rounded.toFixed(2)}%`;
}

function fmtFundingPct(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return `${v.toFixed(4)}%`;
}

function fmtRate(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `₩${v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const chartPctPriceFormat = {
  type: "custom" as const,
  minMove: 0.01,
  formatter: fmtChartPct,
};

const KIMP_COLOR = "#60a5fa";
const FUNDING_COLOR = "#f472b6";

const fundingPriceFormat = {
  type: "custom" as const,
  minMove: 0.0001,
  formatter: fmtFundingPct,
};

type Props = {
  symbol: string;
  latest?: KimpScreenerItem | null;
  latestAsOf?: string | null;
  usdtFx?: KimpFxRateResponse | null;
  bankFx?: KimpFxRateResponse | null;
  rateMode: KimpRateMode;
};

export default function KimpHistoryChart({
  symbol,
  latest,
  latestAsOf,
  usdtFx,
  bankFx,
  rateMode,
}: Props) {
  const { t } = useI18n();
  const h = t.hubs.arbitrage.kimp.history;
  const range: KimpHistoryRange = "ALL";

  const { data, isLoading, error } = useSWR(
    symbol ? ["kimp:history", symbol, range, rateMode] : null,
    () => getKimpHistory(symbol, range, rateMode),
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const meanRef = useRef<ISeriesApi<"Line"> | null>(null);
  const upperRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lowerRef = useRef<ISeriesApi<"Line"> | null>(null);
  const fundingRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lastFitKeyRef = useRef<string | null>(null);

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
      timeScale: {
        borderColor: "#26272d",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
      },
      rightPriceScale: { borderColor: "#26272d" },
      leftPriceScale: { visible: true, borderColor: "#26272d" },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;
    lineRef.current = chart.addLineSeries({
      color: KIMP_COLOR,
      lineWidth: 2,
      priceFormat: chartPctPriceFormat,
    });
    meanRef.current = chart.addLineSeries({
      color: "#868993",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: chartPctPriceFormat,
    });
    upperRef.current = chart.addLineSeries({
      color: "#fbbf24",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: chartPctPriceFormat,
    });
    lowerRef.current = chart.addLineSeries({
      color: "#fbbf24",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: chartPctPriceFormat,
    });
    fundingRef.current = chart.addLineSeries({
      color: FUNDING_COLOR,
      lineWidth: 2,
      priceScaleId: "left",
      priceLineVisible: false,
      priceFormat: fundingPriceFormat,
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
      meanRef.current = null;
      upperRef.current = null;
      lowerRef.current = null;
      fundingRef.current = null;
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
    if (latest?.symbol === symbol && latestAsOf) {
      const liveTs = Date.parse(latestAsOf);
      const liveKimpPct = getKimpPctForMode(latest, rateMode);
      if (Number.isFinite(liveTs) && liveKimpPct != null && Number.isFinite(liveKimpPct)) {
        map.set(Math.floor(liveTs / 1000), liveKimpPct * 100);
      }
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([ts, value]) => ({ time: ts as Time, value }));
  }, [data, latest, latestAsOf, rateMode, symbol]);

  // 펀딩비 시계열(좌측 Y축). 백엔드 funding_series 는 퍼센트 단위이며 라이브
  // 스냅샷의 funding_rate_pct 도 동일 단위라 우측 끝에 현재 펀딩을 이어 붙인다.
  const fundingData = useMemo(() => {
    const map = new Map<number, number>();
    for (const f of data?.funding_series ?? []) {
      map.set(Math.floor(f.t / 1000), f.r);
    }
    if (
      latest?.symbol === symbol
      && latestAsOf
      && latest.funding_rate_pct != null
      && Number.isFinite(latest.funding_rate_pct)
    ) {
      const liveTs = Date.parse(latestAsOf);
      if (Number.isFinite(liveTs)) {
        map.set(Math.floor(liveTs / 1000), latest.funding_rate_pct);
      }
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([ts, value]) => ({ time: ts as Time, value }));
  }, [data, latest, latestAsOf, symbol]);

  const visibleTimeRange = useMemo(() => {
    const rangeSeconds = RANGE_SECONDS[range];
    if (!rangeSeconds) return null;

    const endCandidates = [
      latestAsOf ? Date.parse(latestAsOf) / 1000 : NaN,
      data?.as_of ? Date.parse(data.as_of) / 1000 : NaN,
      seriesData.length > 0 ? Number(seriesData[seriesData.length - 1].time) : NaN,
    ].filter(Number.isFinite);
    if (endCandidates.length === 0) return null;
    const to = Math.ceil(Math.max(...endCandidates));
    return {
      from: (to - rangeSeconds) as Time,
      to: to as Time,
    };
  }, [data, latestAsOf, range, seriesData]);

  const fitKey = useMemo(() => {
    const points = data?.series ?? [];
    const first = points[0]?.t ?? "";
    const last = points.length > 0 ? points[points.length - 1]?.t : "";
    return `${symbol}:${range}:${rateMode}:${points.length}:${first}:${last}`;
  }, [data?.series, range, rateMode, symbol]);

  useEffect(() => {
    if (!lineRef.current) return;
    lineRef.current.setData(seriesData);

    const meanPct = data?.mean_pct ?? null;
    const stdPct = data?.std_pct ?? null;
    if (meanRef.current && upperRef.current && lowerRef.current && seriesData.length > 0) {
      if (meanPct != null) {
        const meanValue = meanPct * 100;
        const band = stdPct != null ? stdPct * 100 : 0;
        const referenceTimes = visibleTimeRange
          ? [visibleTimeRange.from, visibleTimeRange.to]
          : seriesData.map((d) => d.time);
        const meanArr = referenceTimes.map((time) => ({ time, value: meanValue }));
        meanRef.current.setData(meanArr);
        upperRef.current.setData(
          referenceTimes.map((time) => ({ time, value: meanValue + band })),
        );
        lowerRef.current.setData(
          referenceTimes.map((time) => ({ time, value: meanValue - band })),
        );
      } else {
        meanRef.current.setData([]);
        upperRef.current.setData([]);
        lowerRef.current.setData([]);
      }
    }

    if (
      chartRef.current
      && seriesData.length > 0
      && lastFitKeyRef.current !== fitKey
    ) {
      lastFitKeyRef.current = fitKey;
      if (visibleTimeRange) {
        chartRef.current.timeScale().setVisibleRange(visibleTimeRange);
      } else {
        chartRef.current.timeScale().fitContent();
      }
    }
  }, [seriesData, data?.mean_pct, data?.std_pct, fitKey, visibleTimeRange]);

  useEffect(() => {
    if (!fundingRef.current) return;
    fundingRef.current.setData(fundingData);
  }, [fundingData]);

  const isEmpty = !isLoading && seriesData.length === 0;

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a]">
      <div className="grid gap-2 border-b border-[#26272d] p-4 sm:grid-cols-2">
        <FxSummaryCard
          active={rateMode === "usdt"}
          label={h.usdtRate}
          rate={usdtFx ?? null}
          kimpPct={latest ? getKimpPctForMode(latest, "usdt") : null}
          kimpLabel={h.currentKimp}
        />
        <FxSummaryCard
          active={rateMode === "bank"}
          label={h.bankRate}
          rate={bankFx ?? null}
          kimpPct={latest ? getKimpPctForMode(latest, "bank") : null}
          kimpLabel={h.currentKimp}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#26272d] px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-white">
            {h.title} · <span className="text-[#60a5fa]">{symbol}</span>{" "}
            <span className="text-xs font-normal text-[#868993]">
              {t.hubs.arbitrage.kimp.screener.rateModes[rateMode]}
            </span>
          </div>
          <div className="text-xs text-[#868993]">{h.subtitle}</div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#868993]">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: KIMP_COLOR }}
            />
            {h.kimpLegend}
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: FUNDING_COLOR }}
            />
            {h.fundingLegend}
          </span>
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

function FxSummaryCard({
  active,
  label,
  rate,
  kimpPct,
  kimpLabel,
}: {
  active: boolean;
  label: string;
  rate: KimpFxRateResponse | null;
  kimpPct: number | null;
  kimpLabel: string;
}) {
  const sourceClass = !rate
    ? "bg-[#1a1b22] text-[#868993]"
    : rate.stale
      ? "bg-amber-500/10 text-amber-400"
      : "bg-emerald-500/10 text-emerald-400";

  return (
    <div
      className={`rounded-xl border p-3 ${
        active ? "border-[#3a3b44] bg-[#1a1b22]" : "border-[#26272d] bg-[#0e0f14]"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="text-[11px] uppercase tracking-wider text-[#868993]">{label}</div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] ${sourceClass}`}>
          {rate?.source ?? "—"}
        </span>
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-white">
        {fmtRate(rate?.rate)}
      </div>
      <div className="mt-1 text-[11px] text-[#868993]">
        {kimpLabel}: <span className="tabular-nums text-[#c3c5cc]">{fmtPct(kimpPct)}</span>
      </div>
    </div>
  );
}
