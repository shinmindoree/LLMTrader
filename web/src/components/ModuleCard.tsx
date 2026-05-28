"use client";

import { useState, type ReactNode } from "react";

export type ModuleCategory = "alpha" | "arbitrage" | "yield" | "social";
export type ModuleStatus = "running" | "idle" | "error" | "soon";

export const CATEGORY_COLORS: Record<ModuleCategory, string> = {
  alpha: "#ef5350",
  arbitrage: "#26a69a",
  yield: "#f0b90b",
  social: "#2962ff",
};

const CATEGORY_BG: Record<ModuleCategory, string> = {
  alpha: "bg-[#ef5350]/10 text-[#ef5350]",
  arbitrage: "bg-[#26a69a]/10 text-[#26a69a]",
  yield: "bg-[#f0b90b]/10 text-[#f0b90b]",
  social: "bg-[#2962ff]/10 text-[#5b8cff]",
};

const STATUS_DOT: Record<ModuleStatus, string> = {
  running: "bg-[#26a69a]",
  idle: "bg-[#868993]",
  error: "bg-[#ef5350]",
  soon: "bg-[#555]",
};

const STATUS_LABEL: Record<ModuleStatus, string> = {
  running: "Running",
  idle: "Idle",
  error: "Error",
  soon: "Soon",
};

interface ModuleCardProps {
  title: string;
  badge: string;
  category: ModuleCategory;
  status: ModuleStatus;
  desc?: string;
  allocatedUsdt?: number;
  pnl?: number | null;
  annualizedPct?: number | null;
  incomeUsdt?: number | null;
  configureLabel?: string;
  collapseLabel?: string;
  children?: ReactNode;
  defaultExpanded?: boolean;
}

export function ModuleCard({
  title,
  badge,
  category,
  status,
  desc,
  allocatedUsdt,
  pnl,
  annualizedPct,
  incomeUsdt,
  configureLabel = "Configure",
  collapseLabel = "Collapse",
  children,
  defaultExpanded = false,
}: ModuleCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const color = CATEGORY_COLORS[category];
  const fmt = (v: number) =>
    v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

  return (
    <div
      className="rounded-lg border border-[#2a2e39] bg-[#1e222d] transition-colors"
      style={{ borderLeftWidth: 3, borderLeftColor: color }}
    >
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${CATEGORY_BG[category]}`}
            >
              {badge}
            </span>
            <h3 className="text-sm font-semibold text-[#d1d4dc]">{title}</h3>
            <span className="flex items-center gap-1 text-[11px] text-[#868993]">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${STATUS_DOT[status]}`} />
              {STATUS_LABEL[status]}
            </span>
          </div>
          {desc && <p className="mt-1 text-xs text-[#868993]">{desc}</p>}

          {(allocatedUsdt !== undefined || pnl !== null || annualizedPct !== null || incomeUsdt !== null) && (
            <div className="mt-2 flex flex-wrap gap-3">
              {allocatedUsdt !== undefined && (
                <Stat label="Allocated" value={`$${fmt(allocatedUsdt)}`} />
              )}
              {annualizedPct !== null && annualizedPct !== undefined && (
                <Stat
                  label="Ann. Rate"
                  value={fmtPct(annualizedPct)}
                  color={annualizedPct >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}
                />
              )}
              {pnl !== null && pnl !== undefined && (
                <Stat
                  label="PnL"
                  value={`${pnl >= 0 ? "+" : ""}$${fmt(Math.abs(pnl))}`}
                  color={pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}
                />
              )}
              {incomeUsdt !== null && incomeUsdt !== undefined && (
                <Stat label="Income" value={`+$${fmt(incomeUsdt)}`} color="text-[#f0b90b]" />
              )}
            </div>
          )}
        </div>

        {children && status !== "soon" && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 rounded border border-[#2a2e39] px-2.5 py-1 text-xs font-medium text-[#868993] transition-colors hover:border-[#2962ff] hover:text-[#d1d4dc]"
          >
            {expanded ? collapseLabel : configureLabel}
          </button>
        )}
      </div>

      {expanded && children && (
        <div className="border-t border-[#2a2e39] px-4 py-4">{children}</div>
      )}
    </div>
  );
}

function Stat({ label, value, color = "text-[#d1d4dc]" }: { label: string; value: string; color?: string }) {
  return (
    <span className="flex items-center gap-1 text-xs">
      <span className="text-[#555]">{label}</span>
      <span className={`font-mono font-medium ${color}`}>{value}</span>
    </span>
  );
}
