"use client";

import { useEffect, useState } from "react";
import {
  getBillingStatus,
  createCheckoutSession,
  createBillingPortalSession,
} from "@/lib/api";
import type { BillingStatus } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

export default function BillingPage() {
  const { t } = useI18n();
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPlan, setActionPlan] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const PLANS = [
    {
      id: "free",
      name: t.billing.planFree,
      price: "$0",
      period: "forever",
      features: [t.billing.free_f1, t.billing.free_f2, t.billing.free_f3, t.billing.free_f4],
      color: "#868993",
      borderColor: "#2a2e39",
    },
    {
      id: "pro",
      name: t.billing.planPro,
      price: "$29",
      period: "/month",
      features: [t.billing.pro_f1, t.billing.pro_f2, t.billing.pro_f3, t.billing.pro_f4, t.billing.pro_f5],
      color: "#2962ff",
      borderColor: "#2962ff",
      popular: true,
    },
    {
      id: "enterprise",
      name: t.billing.planEnterprise,
      price: "$99",
      period: "/month",
      features: [t.billing.ent_f1, t.billing.ent_f2, t.billing.ent_f3, t.billing.ent_f4, t.billing.ent_f5, t.billing.ent_f6],
      color: "#ff9800",
      borderColor: "#ff9800",
    },
  ];

  useEffect(() => {
    getBillingStatus()
      .then(setBilling)
      .catch(() => setError(t.billing.loadFailed))
      .finally(() => setLoading(false));
  }, []);

  async function handleSubscribe(plan: string) {
    setActionPlan(plan);
    setError(null);
    try {
      const { checkout_url } = await createCheckoutSession(plan);
      window.location.assign(checkout_url);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t.billing.checkoutFailed;
      setError(msg);
      setActionPlan(null);
    }
  }

  async function handleManageBilling() {
    setError(null);
    try {
      const { portal_url } = await createBillingPortalSession();
      window.location.href = portal_url;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t.billing.portalFailed;
      setError(msg);
    }
  }

  if (loading) {
    return (
      <main className="w-full px-6 py-10">
        <div className="text-[#868993]">{t.billing.loading}</div>
      </main>
    );
  }

  const currentPlan = billing?.plan || "free";

  return (
    <main className="w-full max-w-4xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">{t.billing.title}</h1>
      <p className="mt-2 text-sm text-[#868993]">{t.billing.subtitle}</p>

      {error && (
        <div className="mt-4 rounded-lg bg-[#ef5350]/10 border border-[#ef5350]/30 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </div>
      )}

      {billing && (
        <section className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-[#868993] uppercase">{t.billing.currentPlan}</div>
              <div className={`text-xl font-bold uppercase mt-1 ${
                currentPlan === "enterprise" ? "text-[#ff9800]" :
                currentPlan === "pro" ? "text-[#2962ff]" :
                "text-[#868993]"
              }`}>
                {currentPlan}
              </div>
              {billing.plan_expires_at && (
                <div className="text-xs text-[#ef5350] mt-1">
                  {t.billing.expires} {new Date(billing.plan_expires_at).toLocaleDateString()}
                </div>
              )}
            </div>
            {currentPlan !== "free" && (
              <button
                className="rounded-lg border border-[#2a2e39] px-4 py-2 text-sm text-[#868993] hover:text-[#d1d4dc] hover:border-[#d1d4dc] transition-colors"
                onClick={handleManageBilling}
              >
                {t.billing.manageSubscription}
              </button>
            )}
          </div>
        </section>
      )}

      {billing && (
        <section className="mt-4 grid gap-4 sm:grid-cols-3">
          <UsageCard
            current={billing.usage.backtest_this_month}
            label={t.billing.backtestsThisMonth}
            limit={billing.limits.max_backtest_per_month}
          />
          <UsageCard
            current={billing.usage.llm_generate_this_month}
            label={t.billing.llmGenerationsThisMonth}
            limit={billing.limits.max_llm_generate_per_month}
          />
          <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
            <div className="text-xs text-[#868993]">{t.billing.liveSlots}</div>
            <div className="mt-1 text-xl font-semibold text-[#d1d4dc]">
              {billing.limits.max_live_jobs}
            </div>
            <div className="text-xs text-[#868993] mt-1">{t.billing.concurrent}</div>
          </div>
        </section>
      )}

      <section className="mt-8 grid gap-4 sm:grid-cols-3">
        {PLANS.map((plan) => {
          const isCurrent = plan.id === currentPlan;
          const isUpgrade = PLANS.findIndex(p => p.id === plan.id) > PLANS.findIndex(p => p.id === currentPlan);
          const isDowngrade = PLANS.findIndex(p => p.id === plan.id) < PLANS.findIndex(p => p.id === currentPlan);

          return (
            <div
              className={`relative rounded-lg border p-6 transition-colors ${
                isCurrent
                  ? `border-[${plan.borderColor}] bg-[#1e222d]`
                  : "border-[#2a2e39] bg-[#1e222d] hover:border-[#868993]"
              }`}
              key={plan.id}
              style={isCurrent ? { borderColor: plan.borderColor } : undefined}
            >
              {plan.popular && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-[#2962ff] px-3 py-0.5 text-xs font-medium text-white">
                  {t.billing.popular}
                </span>
              )}
              {isCurrent && (
                <span className="absolute -top-3 right-4 rounded-full bg-[#26a69a] px-3 py-0.5 text-xs font-medium text-white">
                  {t.billing.current}
                </span>
              )}
              <div className="text-center">
                <h3 className="text-lg font-semibold" style={{ color: plan.color }}>
                  {plan.name}
                </h3>
                <div className="mt-2">
                  <span className="text-3xl font-bold text-[#d1d4dc]">{plan.price}</span>
                  <span className="text-sm text-[#868993]">{plan.period}</span>
                </div>
              </div>
              <ul className="mt-6 space-y-2">
                {plan.features.map((f) => (
                  <li className="flex items-start gap-2 text-sm text-[#868993]" key={f}>
                    <span className="mt-0.5 text-[#26a69a]">✓</span>
                    {f}
                  </li>
                ))}
              </ul>
              <div className="mt-6">
                {isCurrent ? (
                  <div className="w-full rounded-lg border border-[#2a2e39] px-4 py-2.5 text-center text-sm text-[#868993]">
                    {t.billing.currentPlanButton}
                  </div>
                ) : plan.id === "free" ? (
                  isDowngrade ? (
                    <button
                      className="w-full rounded-lg border border-[#2a2e39] px-4 py-2.5 text-sm text-[#868993] hover:text-[#d1d4dc] hover:border-[#d1d4dc] transition-colors"
                      onClick={handleManageBilling}
                    >
                      {t.billing.downgrade}
                    </button>
                  ) : null
                ) : (
                  <button
                    className="w-full rounded-lg px-4 py-2.5 text-sm font-medium text-white transition-colors disabled:opacity-50"
                    disabled={actionPlan === plan.id}
                    onClick={() => handleSubscribe(plan.id)}
                    style={{ backgroundColor: plan.color }}
                  >
                    {actionPlan === plan.id
                      ? t.billing.redirecting
                      : isUpgrade
                        ? t.billing.upgradeTo.replace("{plan}", plan.name)
                        : t.billing.switchTo.replace("{plan}", plan.name)}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </section>

      <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-4">{t.billing.faq}</h2>
        <div className="space-y-4 text-sm">
          <div>
            <div className="text-[#d1d4dc] font-medium">{t.billing.faq1q}</div>
            <div className="text-[#868993] mt-1">{t.billing.faq1a}</div>
          </div>
          <div>
            <div className="text-[#d1d4dc] font-medium">{t.billing.faq2q}</div>
            <div className="text-[#868993] mt-1">{t.billing.faq2a}</div>
          </div>
          <div>
            <div className="text-[#d1d4dc] font-medium">{t.billing.faq3q}</div>
            <div className="text-[#868993] mt-1">{t.billing.faq3a}</div>
          </div>
        </div>
      </section>
    </main>
  );
}

function UsageCard({ label, current, limit }: { label: string; current: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (current / limit) * 100) : 0;
  const isNearLimit = pct >= 80;

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
      <div className="text-xs text-[#868993]">{label}</div>
      <div className="mt-1 text-xl font-semibold text-[#d1d4dc]">
        {current}
        <span className="text-sm text-[#868993] font-normal"> / {limit >= 9999 ? "∞" : limit}</span>
      </div>
      {limit < 9999 && (
        <div className="mt-2 h-1.5 w-full rounded-full bg-[#2a2e39] overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${isNearLimit ? "bg-[#ef5350]" : "bg-[#2962ff]"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
