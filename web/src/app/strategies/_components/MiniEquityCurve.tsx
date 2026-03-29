"use client";

import type { QuickBacktestEquityPoint } from "@/lib/types";

type Props = {
  data: QuickBacktestEquityPoint[];
  initialBalance: number;
  height?: number;
};

export default function MiniEquityCurve({ data, initialBalance, height = 120 }: Props) {
  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-xs text-zinc-500"
        style={{ height }}
      >
        거래 데이터 부족
      </div>
    );
  }

  const width = 320;
  const pad = { top: 8, right: 8, bottom: 8, left: 8 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  const balances = data.map((d) => d.balance);
  const minB = Math.min(initialBalance, ...balances);
  const maxB = Math.max(initialBalance, ...balances);
  const range = maxB - minB || 1;

  const scaleX = (i: number) => pad.left + (i / (data.length - 1)) * innerW;
  const scaleY = (b: number) => pad.top + innerH - ((b - minB) / range) * innerH;

  // Build path + split into positive/negative segments
  const baseY = scaleY(initialBalance);
  const points = data.map((d, i) => ({ x: scaleX(i), y: scaleY(d.balance) }));

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");

  // Area fill: line down to baseline, back to start
  const areaPath = `${linePath} L${points[points.length - 1].x},${baseY} L${points[0].x},${baseY} Z`;

  const finalBalance = data[data.length - 1].balance;
  const isPositive = finalBalance >= initialBalance;
  const strokeColor = isPositive ? "#22c55e" : "#ef4444";
  const fillColor = isPositive ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
      style={{ height }}
      preserveAspectRatio="none"
    >
      {/* baseline */}
      <line
        x1={pad.left}
        y1={baseY}
        x2={width - pad.right}
        y2={baseY}
        stroke="#3f3f46"
        strokeWidth={0.5}
        strokeDasharray="3 3"
      />
      {/* area */}
      <path d={areaPath} fill={fillColor} />
      {/* line */}
      <path d={linePath} fill="none" stroke={strokeColor} strokeWidth={1.5} />
    </svg>
  );
}
