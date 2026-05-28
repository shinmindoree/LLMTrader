"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { listJobSummaries, getFundingArbStatus } from "@/lib/api";
import { ArbitrageConfigPanel } from "@/components/ArbitrageConfigPanel";
import { ModuleCard } from "@/components/ModuleCard";
import type { JobSummary } from "@/lib/types";

const EMPTY_JOBS: JobSummary[] = [];

function strategyName(path: string): string {
  const base = path.split("/").pop() ?? path;
  return base.replace(/\.[^.]+$/, "");
}

function fmt(v: number) {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── Alpha section ───────────────────────────────────────────
function AlphaSection() {
  const { t } = useI18n();
  const { data: jobs } = useSWR(
    ["modules", "jobs", "LIVE", "RUNNING"],
    () => listJobSummaries({ type: "LIVE", status: "RUNNING", limit: 20 }),
    { refreshInterval: 15_000 },
  );
  const running = jobs ?? EMPTY_JOBS;

  return (
    <section>
      <SectionHeader
        color="#ef5350"
        title={t.modules.alpha.name}
        badge={t.modules.alpha.badge}
        desc={t.modules.alpha.desc}
      />
      <div className="mt-3 flex flex-col gap-2">
        {running.length === 0 ? (
          <div className="flex items-center justify-between rounded-lg border border-dashed border-[#2a2e39] bg-[#131722] px-4 py-3">
            <p className="text-sm text-[#868993]">{t.modules.alpha.noJobs}</p>
            <Link
              href="/live/new"
              className="rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#d1d4dc] transition-colors hover:border-[#ef5350] hover:bg-[#ef5350]/10"
            >
              {t.modules.alpha.startNew} →
            </Link>
          </div>
        ) : (
          running.map((job) => {
            const cfg = job.config as Record<string, unknown> | null;
            const alloc = typeof cfg?.initial_balance === "number" ? cfg.initial_balance : 0;
            const pnl =
              typeof job.result_summary === "object" && job.result_summary !== null
                ? ((job.result_summary as Record<string, unknown>).net_profit as number | undefined) ?? null
                : null;
            return (
              <ModuleCard
                key={job.job_id}
                title={strategyName(job.strategy_path ?? "")}
                badge={t.modules.alpha.badge}
                category="alpha"
                status="running"
                allocatedUsdt={alloc}
                pnl={pnl}
                configureLabel={t.modules.configure}
                collapseLabel={t.modules.collapse}
              >
                <div className="text-xs text-[#868993]">
                  <p>Symbol: <span className="font-mono text-[#d1d4dc]">{cfg?.symbol as string ?? "—"}</span></p>
                  <p className="mt-1">
                    <Link href={`/live/jobs/${job.job_id}`} className="text-[#2962ff] hover:text-[#5b8cff]">
                      상세 보기 →
                    </Link>
                  </p>
                </div>
              </ModuleCard>
            );
          })
        )}
        {running.length > 0 && (
          <Link
            href="/live/new"
            className="mt-1 self-start rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#868993] transition-colors hover:border-[#ef5350] hover:text-[#d1d4dc]"
          >
            + {t.modules.alpha.startNew}
          </Link>
        )}
      </div>
    </section>
  );
}

// ── Arbitrage section ───────────────────────────────────────
function ArbitrageSection() {
  const { t } = useI18n();
  const { data: status } = useSWR("funding-arb-status", getFundingArbStatus, {
    refreshInterval: 15_000,
    shouldRetryOnError: false,
  });

  const annPct = status?.annualized_funding_pct ?? null;
  const pnl = status?.unrealized_pnl ?? null;
  const income = status?.accumulated_funding_income ?? null;

  return (
    <section>
      <SectionHeader
        color="#26a69a"
        title={t.modules.arbitrage.name}
        badge={t.modules.arbitrage.badge}
        desc={t.modules.arbitrage.desc}
      />
      <div className="mt-3">
        <ModuleCard
          title="Funding Rate Arbitrage"
          badge={t.modules.arbitrage.badge}
          category="arbitrage"
          status={status?.running ? "running" : "idle"}
          desc="현물 롱 + 선물 숏 delta-neutral 전략"
          annualizedPct={annPct}
          pnl={pnl}
          incomeUsdt={income}
          configureLabel={t.modules.configure}
          collapseLabel={t.modules.collapse}
        >
          <ArbitrageConfigPanel />
        </ModuleCard>
      </div>
    </section>
  );
}

// ── Yield section ───────────────────────────────────────────
function YieldSection() {
  const { t } = useI18n();
  const [autoEarn, setAutoEarn] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("autoSweepEnabled") === "true";
  });

  const toggle = () => {
    const next = !autoEarn;
    setAutoEarn(next);
    localStorage.setItem("autoSweepEnabled", String(next));
  };

  return (
    <section>
      <SectionHeader
        color="#f0b90b"
        title={t.modules.yield.name}
        badge={t.modules.yield.badge}
        desc={t.modules.yield.desc}
      />
      <div className="mt-3">
        <ModuleCard
          title={t.modules.yield.autoEarnLabel}
          badge={t.modules.yield.badge}
          category="yield"
          status={autoEarn ? "running" : "idle"}
          desc={t.modules.yield.autoEarnDesc}
          configureLabel={t.modules.configure}
          collapseLabel={t.modules.collapse}
        >
          <div className="flex items-center justify-between">
            <p className="text-xs text-[#868993]">{t.modules.yield.autoEarnDesc}</p>
            <button
              type="button"
              onClick={toggle}
              className={[
                "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none",
                autoEarn ? "bg-[#f0b90b]" : "bg-[#2a2e39]",
              ].join(" ")}
              role="switch"
              aria-checked={autoEarn}
            >
              <span
                className={[
                  "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform",
                  autoEarn ? "translate-x-5" : "translate-x-0",
                ].join(" ")}
              />
            </button>
          </div>
          <p className="mt-3 text-xs text-[#555]">
            <Link href="/settings" className="text-[#f0b90b] hover:text-[#ffd700]">
              {t.modules.yield.goToSettings}
            </Link>
          </p>
        </ModuleCard>
      </div>
    </section>
  );
}

// ── Social section ──────────────────────────────────────────
function SocialSection() {
  const { t } = useI18n();
  return (
    <section>
      <SectionHeader
        color="#2962ff"
        title={t.modules.social.name}
        badge={t.modules.social.badge}
        desc={t.modules.social.desc}
      />
      <div className="mt-3">
        <ModuleCard
          title="Copy Trading"
          badge={t.modules.social.badge}
          category="social"
          status="soon"
          desc={t.modules.social.comingSoonDesc}
        />
      </div>
    </section>
  );
}

// ── Section header ──────────────────────────────────────────
function SectionHeader({
  color,
  title,
  badge,
  desc,
}: {
  color: string;
  title: string;
  badge: string;
  desc: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <span
        className="inline-block h-4 w-1 rounded-full"
        style={{ background: color }}
      />
      <div>
        <span className="text-sm font-semibold text-[#d1d4dc]">{title}</span>
        <span className="ml-2 text-xs text-[#868993]">· {badge}</span>
      </div>
    </div>
  );
}

// ── Main catalog panel ──────────────────────────────────────
export function ModuleCatalogPanel() {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="text-xl font-semibold text-[#d1d4dc]">{t.modules.title}</h1>
        <p className="mt-1 text-sm text-[#868993]">{t.modules.subtitle}</p>
      </header>
      <AlphaSection />
      <ArbitrageSection />
      <YieldSection />
      <SocialSection />
    </div>
  );
}
