"use client";

import type { ReactNode } from "react";

export function HubHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className="mb-6">
      <h1 className="text-xl font-semibold text-[#d1d4dc]">{title}</h1>
      <p className="mt-1 text-sm text-[#868993]">{subtitle}</p>
    </header>
  );
}

export function StatusPill({ active, label }: { active: boolean; label: string }) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium",
        active
          ? "bg-[#26a69a]/15 text-[#26a69a]"
          : "bg-[#868993]/15 text-[#868993]",
      ].join(" ")}
    >
      <span
        className={[
          "h-1.5 w-1.5 rounded-full",
          active ? "bg-[#26a69a]" : "bg-[#868993]",
        ].join(" ")}
      />
      {label}
    </span>
  );
}

export function StrategyCard({
  name,
  badge,
  desc,
  active,
  statusLabel,
  children,
}: {
  name: string;
  badge: string;
  desc: string;
  active: boolean;
  statusLabel: string;
  children?: ReactNode;
}) {
  return (
    <section
      className={[
        "rounded-lg border bg-[#1e222d] p-4 transition-colors",
        active ? "border-[#2a2e39]" : "border-[#2a2e39]/60 opacity-90",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-[#d1d4dc]">{name}</h2>
            <span className="rounded bg-[#2962ff]/15 px-1.5 py-0.5 text-[10px] font-medium text-[#5b8cff]">
              {badge}
            </span>
          </div>
          <p className="mt-1.5 text-sm text-[#868993]">{desc}</p>
        </div>
        <StatusPill active={active} label={statusLabel} />
      </div>
      {children ? <div className="mt-4 border-t border-[#2a2e39] pt-4">{children}</div> : null}
    </section>
  );
}
