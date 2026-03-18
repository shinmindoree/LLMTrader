"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

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

type MarkerTrade = {
  timestamp: number | null;
  side: string | null;
  price: number | null;
  pnl: number | null;
  reason: string | null;
  exitReason: string | null;
};

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

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

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function BacktestExecutionChart({
  chart,
  trades,
}: {
  chart: BacktestChartPayload;
  trades: MarkerTrade[];
}) {
  const candles = useMemo(() => {
    if (!Array.isArray(chart.candles)) return [];
    return chart.candles.filter(
      (c) =>
        c &&
        typeof c.close_time === "number" &&
        Number.isFinite(c.open) &&
        Number.isFinite(c.high) &&
        Number.isFinite(c.low) &&
        Number.isFinite(c.close),
    );
  }, [chart.candles]);

  const hasOscillator = useMemo(
    () => Array.isArray(chart.indicator_series) && chart.indicator_series.some((s) => s.pane === "oscillator"),
    [chart.indicator_series],
  );

  const traces = useMemo(() => {
    if (!candles.length) return [] as Array<Record<string, unknown>>;

    const x = candles.map((c) => new Date(c.close_time).toISOString());
    const base: Array<Record<string, unknown>> = [
      {
        type: "candlestick",
        name: "Price",
        x,
        open: candles.map((c) => c.open),
        high: candles.map((c) => c.high),
        low: candles.map((c) => c.low),
        close: candles.map((c) => c.close),
        increasing: { line: { color: "#26a69a" } },
        decreasing: { line: { color: "#ef5350" } },
        yaxis: "y",
      },
    ];

    const indicatorSeries = Array.isArray(chart.indicator_series) ? chart.indicator_series : [];
    indicatorSeries.forEach((series, idx) => {
      const y = candles.map((_, i) => asNumber(series.values?.[i]));
      if (!y.some((v) => v !== null)) return;
      base.push({
        type: "scatter",
        mode: "lines",
        name: series.label,
        x,
        y,
        yaxis: series.pane === "oscillator" ? "y2" : "y",
        line: { color: colorAt(idx), width: 1.8 },
      });
    });

    const markerGroups: Record<string, Array<{ x: string; y: number; text: string }>> = {
      LONG_ENTRY: [],
      LONG_EXIT: [],
      SHORT_ENTRY: [],
      SHORT_EXIT: [],
    };

    for (const trade of trades) {
      if (!trade.timestamp || !trade.price) continue;
      const isExit = trade.pnl !== null;
      const side = trade.side ?? "";
      let kind: keyof typeof markerGroups | null = null;
      if (!isExit && side === "BUY") kind = "LONG_ENTRY";
      if (!isExit && side === "SELL") kind = "SHORT_ENTRY";
      if (isExit && side === "SELL") kind = "LONG_EXIT";
      if (isExit && side === "BUY") kind = "SHORT_EXIT";
      if (!kind) continue;
      markerGroups[kind].push({
        x: new Date(trade.timestamp).toISOString(),
        y: trade.price,
        text: `${kind} | ${trade.reason ?? trade.exitReason ?? "-"}`,
      });
    }

    const markerStyles: Record<
      keyof typeof markerGroups,
      { name: string; symbol: string; color: string; size: number }
    > = {
      LONG_ENTRY: { name: "Long Entry", symbol: "triangle-up", color: "#00e676", size: 10 },
      LONG_EXIT: { name: "Long Exit", symbol: "triangle-down", color: "#40c4ff", size: 10 },
      SHORT_ENTRY: { name: "Short Entry", symbol: "triangle-down", color: "#ff5252", size: 10 },
      SHORT_EXIT: { name: "Short Exit", symbol: "triangle-up", color: "#ffb74d", size: 10 },
    };

    for (const [kind, points] of Object.entries(markerGroups) as Array<
      [keyof typeof markerGroups, Array<{ x: string; y: number; text: string }>]
    >) {
      if (!points.length) continue;
      const style = markerStyles[kind];
      base.push({
        type: "scatter",
        mode: "markers",
        name: style.name,
        x: points.map((p) => p.x),
        y: points.map((p) => p.y),
        yaxis: "y",
        marker: {
          symbol: style.symbol,
          color: style.color,
          size: style.size,
          line: { width: 1, color: "#0f141f" },
        },
        text: points.map((p) => p.text),
        hovertemplate: "%{text}<br>Price: %{y:.4f}<extra></extra>",
      });
    }

    return base;
  }, [candles, chart.indicator_series, trades]);

  const indicatorNames = useMemo(() => {
    const config = chart.indicator_config;
    if (!config || typeof config !== "object") return [] as string[];
    return Object.keys(config);
  }, [chart.indicator_config]);

  if (!candles.length) {
    return (
      <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-xs text-[#868993]">
        백테스트 차트 데이터가 없습니다.
      </div>
    );
  }

  const layout: Record<string, unknown> = {
    dragmode: "pan",
    paper_bgcolor: "#131722",
    plot_bgcolor: "#0f141f",
    font: { color: "#d1d4dc" },
    margin: { l: 56, r: 24, t: 18, b: 40 },
    legend: { orientation: "h", x: 0, y: 1.02 },
    xaxis: {
      rangeslider: { visible: false },
      showgrid: true,
      gridcolor: "rgba(42, 46, 57, 0.6)",
      showticklabels: !hasOscillator,
      domain: [0, 1],
    },
    yaxis: {
      title: "Price",
      side: "right",
      showgrid: true,
      gridcolor: "rgba(42, 46, 57, 0.6)",
      domain: hasOscillator ? [0.32, 1] : [0, 1],
    },
    hovermode: "x unified",
  };

  if (hasOscillator) {
    layout.xaxis2 = {
      matches: "x",
      showgrid: true,
      gridcolor: "rgba(42, 46, 57, 0.6)",
      domain: [0, 1],
      anchor: "y2",
    };
    layout.yaxis2 = {
      title: "Oscillator",
      side: "right",
      showgrid: true,
      gridcolor: "rgba(42, 46, 57, 0.6)",
      domain: [0, 0.25],
    };
  }

  return (
    <section className="mb-4 rounded border border-[#2a2e39] bg-[#131722] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#d1d4dc]">
          {chart.symbol ?? "-"}
        </span>
        <span className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#d1d4dc]">
          {chart.interval ?? "-"}
        </span>
        {indicatorNames.map((name) => (
          <span key={name} className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-1 text-[#868993]">
            {name}
          </span>
        ))}
      </div>
      <Plot
        data={traces}
        layout={layout}
        style={{ width: "100%", height: "560px" }}
        config={{ responsive: true, displaylogo: false }}
        useResizeHandler
      />
    </section>
  );
}
