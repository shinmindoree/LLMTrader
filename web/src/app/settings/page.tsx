"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import {
  getUserProfile,
  getBinanceKeysStatus,
  setBinanceKeys,
  deleteBinanceKeys,
} from "@/lib/api";
import type { UserProfile, BinanceKeysStatus } from "@/lib/types";

type FormState = "idle" | "saving" | "deleting";

export default function SettingsPage() {
  const { t } = useI18n();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [formState, setFormState] = useState<FormState>("idle");

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://testnet.binancefuture.com");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  useEffect(() => {
    Promise.all([getUserProfile(), getBinanceKeysStatus()])
      .then(([p, k]) => {
        setProfile(p);
        setKeysStatus(k);
        if (k.base_url) setBaseUrl(k.base_url);
      })
      .catch(() => setMessage({ type: "error", text: t.settingsPage.loadFailed }))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSaveKeys(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMessage({ type: "error", text: t.settingsPage.keyRequired });
      return;
    }
    setFormState("saving");
    setMessage(null);
    try {
      const result = await setBinanceKeys({
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        base_url: baseUrl.trim(),
      });
      setKeysStatus({ configured: true, api_key_masked: result.api_key_masked, base_url: result.base_url });
      setApiKey("");
      setApiSecret("");
      setMessage({ type: "success", text: t.settingsPage.keysSaved });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t.settingsPage.saveFailed;
      setMessage({ type: "error", text: msg });
    } finally {
      setFormState("idle");
    }
  }

  async function handleDeleteKeys() {
    setFormState("deleting");
    setMessage(null);
    try {
      await deleteBinanceKeys();
      setKeysStatus({ configured: false });
      setShowDeleteConfirm(false);
      setMessage({ type: "success", text: t.settingsPage.keysDeleted });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t.settingsPage.deleteFailed;
      setMessage({ type: "error", text: msg });
    } finally {
      setFormState("idle");
    }
  }

  if (loading) {
    return (
      <main className="w-full px-6 py-10">
        <div className="text-[#868993]">{t.settingsPage.loading}</div>
      </main>
    );
  }

  return (
    <main className="w-full max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">{t.settingsPage.title}</h1>
      <p className="mt-2 text-sm text-[#868993]">{t.settingsPage.subtitle}</p>

      {profile && (
        <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <h2 className="text-lg font-semibold text-[#d1d4dc] mb-4">{t.settingsPage.account}</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.email}</span>
              <span className="text-[#d1d4dc]">{profile.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.plan}</span>
              <span className={
                profile.plan === "enterprise" ? "text-[#ff9800] font-semibold uppercase" :
                profile.plan === "pro" ? "text-[#2962ff] font-semibold uppercase" :
                "text-[#868993] uppercase"
              }>
                {profile.plan}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.userId}</span>
              <span className="text-[#868993] font-mono text-xs">{profile.user_id.slice(0, 12)}...</span>
            </div>
          </div>
        </section>
      )}

      <section className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-2">{t.settingsPage.binanceApiKeys}</h2>
        <p className="text-xs text-[#868993] mb-4">
          {t.settingsPage.keysSecureInfo}
        </p>

        {message && (
          <div className={`mb-4 rounded-lg px-4 py-3 text-sm ${
            message.type === "success"
              ? "bg-[#26a69a]/10 border border-[#26a69a]/30 text-[#26a69a]"
              : "bg-[#ef5350]/10 border border-[#ef5350]/30 text-[#ef5350]"
          }`}>
            {message.text}
          </div>
        )}

        {keysStatus?.configured && (
          <div className="mb-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 rounded-full bg-[#26a69a]" />
                  <span className="text-sm text-[#d1d4dc]">{t.settingsPage.keysConfigured}</span>
                </div>
                <div className="mt-1 text-xs text-[#868993] font-mono">
                  {keysStatus.api_key_masked || "****"}
                </div>
                <div className="mt-1 text-xs text-[#868993]">
                  {keysStatus.base_url}
                </div>
              </div>
              {showDeleteConfirm ? (
                <div className="flex items-center gap-2">
                  <button
                    className="rounded px-3 py-1.5 text-xs bg-[#ef5350] text-white hover:bg-[#ef5350]/80 transition-colors disabled:opacity-50"
                    disabled={formState === "deleting"}
                    onClick={handleDeleteKeys}
                  >
                    {formState === "deleting" ? t.settingsPage.deleting : t.settingsPage.confirmDelete}
                  </button>
                  <button
                    className="rounded px-3 py-1.5 text-xs text-[#868993] hover:text-[#d1d4dc] transition-colors"
                    onClick={() => setShowDeleteConfirm(false)}
                  >
                    {t.common.cancel}
                  </button>
                </div>
              ) : (
                <button
                  className="rounded px-3 py-1.5 text-xs text-[#ef5350] border border-[#ef5350]/30 hover:bg-[#ef5350]/10 transition-colors"
                  onClick={() => setShowDeleteConfirm(true)}
                >
                  {t.settingsPage.deleteKeys}
                </button>
              )}
            </div>
          </div>
        )}

        <form className="space-y-4" onSubmit={handleSaveKeys}>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="api-key">
              {t.settingsPage.apiKey}
            </label>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="api-key"
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t.settingsPage.apiKeyPlaceholder}
              type="password"
              value={apiKey}
            />
          </div>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="api-secret">
              {t.settingsPage.apiSecret}
            </label>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="api-secret"
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder={t.settingsPage.apiSecretPlaceholder}
              type="password"
              value={apiSecret}
            />
          </div>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="base-url">
              {t.settingsPage.baseUrl}
            </label>
            <select
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="base-url"
              onChange={(e) => setBaseUrl(e.target.value)}
              value={baseUrl}
            >
              <option value="https://testnet.binancefuture.com">{t.settingsPage.testnetOption}</option>
              <option value="https://fapi.binance.com">{t.settingsPage.mainnetOption}</option>
            </select>
            <p className="mt-1 text-xs text-[#868993]">
              {t.settingsPage.baseUrlHint}
            </p>
          </div>
          <button
            className="w-full rounded-lg bg-[#2962ff] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#2962ff]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={formState === "saving" || !apiKey.trim() || !apiSecret.trim()}
            type="submit"
          >
            {formState === "saving" ? t.settingsPage.verifyingAndSaving : keysStatus?.configured ? t.settingsPage.updateKeys : t.settingsPage.saveKeys}
          </button>
        </form>

        <div className="mt-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
          <h3 className="text-xs font-semibold text-[#868993] uppercase mb-2">{t.settingsPage.securityInfo}</h3>
          <ul className="text-xs text-[#868993] space-y-1">
            <li>• {t.settingsPage.securityItem1}</li>
            <li>• {t.settingsPage.securityItem2}</li>
            <li>• {t.settingsPage.securityItem3}</li>
            <li>• {t.settingsPage.securityItem4}</li>
          </ul>
        </div>
      </section>
    </main>
  );
}
