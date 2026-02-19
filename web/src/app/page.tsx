"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";

export default function LandingPage() {
  const { t } = useI18n();
  const [isLoggedIn, setIsLoggedIn] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/auth/session", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) return false;
        const data = (await res.json()) as { user?: unknown };
        return !!data?.user;
      })
      .then(setIsLoggedIn)
      .catch(() => setIsLoggedIn(false));
  }, []);

  const showCTA = isLoggedIn === false;

  return (
    <div className="min-h-screen">
      <section className="relative overflow-hidden px-6 pt-16 pb-24 md:pt-24 md:pb-32">
        <div className="absolute inset-0 -z-10">
          <div className="absolute inset-0 bg-gradient-to-b from-[#2962ff]/10 via-transparent to-transparent" />
          <div className="absolute -top-40 -right-40 h-80 w-80 rounded-full bg-[#2962ff]/20 blur-3xl" />
          <div className="absolute -bottom-40 -left-40 h-80 w-80 rounded-full bg-[#26a69a]/15 blur-3xl" />
          <div className="absolute left-1/2 top-1/2 h-96 w-96 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#2962ff]/5 blur-3xl" />
        </div>
        <div className="mx-auto max-w-4xl text-center">
          <div className="mb-6 flex flex-wrap items-center justify-center gap-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-[#a855f7]/40 bg-[#a855f7]/15 px-4 py-1.5 text-xs font-semibold text-[#a855f7]">
              {t.landing.hero.badge1}
            </div>
            <div className="inline-flex items-center gap-2 rounded-full border border-[#2962ff]/30 bg-[#2962ff]/10 px-4 py-1.5 text-xs font-medium text-[#2962ff]">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#26a69a] opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-[#26a69a]" />
              </span>
              {t.landing.hero.badge2}
            </div>
          </div>
          <h1 className="text-4xl font-bold tracking-tight text-[#d1d4dc] sm:text-5xl md:text-6xl">
            Describe.
            <span className="bg-gradient-to-r from-[#a855f7] via-[#06b6d4] to-[#10b981] bg-clip-text text-transparent">
              {" "}Backtest.
            </span>
            <span className="text-[#d1d4dc]"> Trade.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-[#868993] sm:text-xl">
            {t.landing.hero.subtitle}
          </p>
          {showCTA && (
            <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
              <Link
                className="w-full rounded-lg bg-[#2962ff] px-8 py-4 text-center font-semibold text-white transition-all hover:bg-[#1e53e5] hover:shadow-lg hover:shadow-[#2962ff]/25 sm:w-auto"
                href="/dashboard"
              >
                {t.landing.hero.getStarted}
              </Link>
              <Link
                className="w-full rounded-lg border border-[#2a2e39] bg-[#1e222d] px-8 py-4 text-center font-semibold text-[#d1d4dc] transition-all hover:border-[#2962ff] hover:bg-[#252936] sm:w-auto"
                href="/auth"
              >
                {t.landing.hero.login}
              </Link>
            </div>
          )}
        </div>
      </section>

      <section id="features" className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
        <div className="mx-auto max-w-6xl">
          <h2 className="text-center text-3xl font-bold text-[#d1d4dc] md:text-4xl">
            {t.landing.features.title}
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-center text-[#868993]">
            {t.landing.features.subtitle}
          </p>

          <div className="mt-16 grid gap-8 md:grid-cols-2 lg:grid-cols-5">
            <FeatureCard
              icon={<NaturalLanguageIcon />}
              title={t.landing.features.naturalLanguage.title}
              description={t.landing.features.naturalLanguage.description}
              accent="#a855f7"
              featured
            />
            <FeatureCard
              icon={<BacktestIcon />}
              title={t.landing.features.backtest.title}
              description={t.landing.features.backtest.description}
              accent="#2962ff"
            />
            <FeatureCard
              icon={<LiveIcon />}
              title={t.landing.features.live.title}
              description={t.landing.features.live.description}
              accent="#26a69a"
            />
            <FeatureCard
              icon={<StrategyIcon />}
              title={t.landing.features.strategy.title}
              description={t.landing.features.strategy.description}
              accent="#ff9800"
            />
            <FeatureCard
              icon={<RiskIcon />}
              title={t.landing.features.risk.title}
              description={t.landing.features.risk.description}
              accent="#ef5350"
            />
          </div>
        </div>
      </section>

      <section className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
        <div className="mx-auto max-w-6xl">
          <div className="overflow-hidden rounded-2xl border border-[#2a2e39] bg-[#1e222d]">
            <div className="grid md:grid-cols-2">
              <div className="flex flex-col justify-center p-8 md:p-12">
                <h3 className="text-2xl font-bold text-[#d1d4dc] md:text-3xl">
                  {t.landing.workflow.title}
                </h3>
                <p className="mt-4 text-[#868993]">
                  {t.landing.workflow.description}
                </p>
                <ul className="mt-6 space-y-3 text-sm text-[#d1d4dc]">
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#a855f7]" />
                    {t.landing.workflow.item1}
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    {t.landing.workflow.item2}
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    {t.landing.workflow.item3}
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    {t.landing.workflow.item4}
                  </li>
                </ul>
              </div>
              <div className="relative flex items-center justify-center p-8 md:p-12">
                <div className="relative">
                  <div className="absolute -inset-4 rounded-2xl bg-gradient-to-br from-[#2962ff]/20 to-[#26a69a]/20 blur-2xl" />
                  <div className="relative rounded-xl border border-[#2a2e39] bg-[#131722] p-6 font-mono text-xs">
                    <FlowDiagram t={t} />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-center text-3xl font-bold text-[#d1d4dc] md:text-4xl">
            {t.landing.screenshots.title}
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-center text-[#868993]">
            {t.landing.screenshots.subtitle}
          </p>

          <div className="mt-16 space-y-24">
            <ScreenshotBlock
              title={t.landing.screenshots.dashboard}
              description={t.landing.screenshots.dashboardDesc}
            >
              <div className="overflow-hidden rounded-xl border border-[#2a2e39] bg-[#1e222d] shadow-2xl">
                <Image
                  src="/landing/dashboard.png"
                  alt={t.landing.screenshots.altDashboard}
                  width={960}
                  height={400}
                  className="w-full object-cover"
                  priority
                />
              </div>
            </ScreenshotBlock>

            <ScreenshotBlock
              title={t.landing.screenshots.strategies}
              description={t.landing.screenshots.strategiesDesc}
            >
              <div className="overflow-hidden rounded-xl border border-[#2a2e39] bg-[#1e222d] shadow-2xl">
                <Image
                  src="/landing/strategies.png"
                  alt={t.landing.screenshots.altStrategies}
                  width={1200}
                  height={800}
                  className="w-full object-cover"
                />
              </div>
            </ScreenshotBlock>

            <ScreenshotBlock
              title={t.landing.screenshots.backtestLive}
              description={t.landing.screenshots.backtestLiveDesc}
            >
              <div className="space-y-6">
                <div className="overflow-hidden rounded-xl border border-[#2a2e39] bg-[#1e222d] shadow-2xl">
                  <Image
                    src="/landing/backtest-live-form.png"
                    alt="Run settings - Strategy, Symbol, Interval"
                    width={1200}
                    height={600}
                    className="w-full object-cover"
                  />
                </div>
                <div className="grid gap-6 md:grid-cols-2">
                  <div className="overflow-hidden rounded-xl border border-[#2a2e39] bg-[#1e222d] shadow-2xl">
                    <Image
                      src="/landing/backtest-live-chart.png"
                      alt="Results - Key metrics and equity chart"
                      width={800}
                      height={500}
                      className="w-full object-cover"
                    />
                  </div>
                  <div className="overflow-hidden rounded-xl border border-[#2a2e39] bg-[#1e222d] shadow-2xl">
                    <Image
                      src="/landing/backtest-live-trades.png"
                      alt="Results - Trade history"
                      width={800}
                      height={500}
                      className="w-full object-cover"
                    />
                  </div>
                </div>
              </div>
            </ScreenshotBlock>
          </div>
        </div>
      </section>

      {showCTA && (
        <section className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
          <div className="mx-auto max-w-3xl text-center">
            <h2 className="text-3xl font-bold text-[#d1d4dc] md:text-4xl">
              {t.landing.cta.title}
            </h2>
            <p className="mt-4 text-[#868993]">
              {t.landing.cta.subtitle}
            </p>
            <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
              <Link
                className="w-full rounded-lg bg-[#2962ff] px-8 py-4 text-center font-semibold text-white transition-all hover:bg-[#1e53e5] sm:w-auto"
                href="/dashboard"
              >
                {t.landing.cta.getStarted}
              </Link>
              <Link
                className="w-full rounded-lg border border-[#2a2e39] px-8 py-4 text-center font-semibold text-[#d1d4dc] transition-all hover:border-[#2962ff] sm:w-auto"
                href="/auth"
              >
                {t.landing.cta.login}
              </Link>
            </div>
          </div>
        </section>
      )}

      <footer className="border-t border-[#2a2e39] px-6 py-8">
        <div className="mx-auto max-w-6xl flex flex-col items-center justify-between gap-4 sm:flex-row">
          <span className="text-sm text-[#868993]">{t.landing.footer.copyright}</span>
          <div className="flex gap-6 text-sm">
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/dashboard">
              {t.landing.footer.dashboard}
            </Link>
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/strategies">
              {t.landing.footer.strategies}
            </Link>
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/auth">
              {t.landing.footer.login}
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function NaturalLanguageIcon() {
  return (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
    </svg>
  );
}

function FeatureCard({
  icon,
  title,
  description,
  accent,
  featured,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  accent: string;
  featured?: boolean;
}) {
  return (
    <div
      className={`group rounded-xl border bg-[#1e222d] p-6 transition-all hover:shadow-lg ${
        featured
          ? "border-[#a855f7]/50 bg-gradient-to-b from-[#a855f7]/5 to-transparent hover:border-[#a855f7] hover:shadow-[#a855f7]/10"
          : "border-[#2a2e39] hover:border-[#2962ff]/50 hover:shadow-[#2962ff]/5"
      }`}
    >
      <div
        className="mb-4 inline-flex rounded-lg p-3"
        style={{ backgroundColor: `${accent}15` }}
      >
        <span style={{ color: accent }}>{icon}</span>
      </div>
      <h3 className="text-lg font-semibold text-[#d1d4dc]">{title}</h3>
      <p className="mt-2 text-sm text-[#868993] leading-relaxed">{description}</p>
    </div>
  );
}

function BacktestIcon() {
  return (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  );
}

function LiveIcon() {
  return (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
    </svg>
  );
}

function StrategyIcon() {
  return (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
    </svg>
  );
}

function RiskIcon() {
  return (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
    </svg>
  );
}

function ScreenshotBlock({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-6">
        <h3 className="text-xl font-semibold text-[#d1d4dc] md:text-2xl">{title}</h3>
        <p className="mt-2 text-[#868993]">{description}</p>
      </div>
      {children}
    </div>
  );
}

function FlowDiagram({ t }: { t: import("@/lib/i18n").TranslationKeys }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 rounded-lg border border-[#a855f7]/40 bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#a855f7]/20 px-2 py-0.5 text-[#a855f7]">1</span>
        <span className="text-[#d1d4dc]">{t.landing.flowDiagram.step1}</span>
        <span className="text-[#868993]">{t.landing.flowDiagram.step1Sub}</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-[#2962ff]">2</span>
        <span className="text-[#d1d4dc]">{t.landing.flowDiagram.step2}</span>
        <span className="text-[#868993]">{t.landing.flowDiagram.step2Sub}</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#26a69a]/40 bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#26a69a]/20 px-2 py-0.5 text-[#26a69a]">3</span>
        <span className="text-[#d1d4dc]">{t.landing.flowDiagram.step3}</span>
        <span className="text-[#868993]">{t.landing.flowDiagram.step3Sub}</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#26a69a]/40 bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#26a69a]/20 px-2 py-0.5 text-[#26a69a]">4</span>
        <span className="text-[#d1d4dc]">{t.landing.flowDiagram.step4}</span>
        <span className="text-[#868993]">{t.landing.flowDiagram.step4Sub}</span>
      </div>
    </div>
  );
}
