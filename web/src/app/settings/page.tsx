"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import {
  getUserProfile,
  getBinanceKeysStatus,
  setBinanceKeys,
  deleteBinanceKeys,
  testLlmEndpoint,
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

  const [llmInput, setLlmInput] = useState("Hello");
  const [llmOutput, setLlmOutput] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getUserProfile(), getBinanceKeysStatus()])
      .then(([p, k]) => {
        setProfile(p);
        setKeysStatus(k);
        if (k.base_url) setBaseUrl(k.base_url);
      })
      .catch(() => setMessage({ type: "error", text: "Failed to load settings" }))
      .finally(() => setLoading(false));
  }, []);

  async function handleSaveKeys(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMessage({ type: "error", text: "API Key and Secret are required" });
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
      setMessage({ type: "success", text: "Binance API keys saved and verified successfully" });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save keys";
      setMessage({ type: "error", text: msg });
    } finally {
      setFormState("idle");
    }
  }

  async function handleTestLlm() {
    setLlmLoading(true);
    setLlmOutput(null);
    setLlmError(null);
    try {
      const res = await testLlmEndpoint(llmInput);
      setLlmOutput(res.output);
    } catch (err: unknown) {
      setLlmError(err instanceof Error ? err.message : t.settings.llmTestFailed);
    } finally {
      setLlmLoading(false);
    }
  }

  async function handleDeleteKeys() {
    setFormState("deleting");
    setMessage(null);
    try {
      await deleteBinanceKeys();
      setKeysStatus({ configured: false });
      setShowDeleteConfirm(false);
      setMessage({ type: "success", text: "Binance API keys deleted" });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete keys";
      setMessage({ type: "error", text: msg });
    } finally {
      setFormState("idle");
    }
  }

  if (loading) {
    return (
      <main className="w-full px-6 py-10">
        <div className="text-[#868993]">Loading settings...</div>
      </main>
    );
  }

  return (
    <main className="w-full max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">Settings</h1>
      <p className="mt-2 text-sm text-[#868993]">Manage your account and API configuration</p>

      {/* Profile Info */}
      {profile && (
        <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <h2 className="text-lg font-semibold text-[#d1d4dc] mb-4">Account</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[#868993]">Email</span>
              <span className="text-[#d1d4dc]">{profile.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">Plan</span>
              <span className={
                profile.plan === "enterprise" ? "text-[#ff9800] font-semibold uppercase" :
                profile.plan === "pro" ? "text-[#2962ff] font-semibold uppercase" :
                "text-[#868993] uppercase"
              }>
                {profile.plan}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">User ID</span>
              <span className="text-[#868993] font-mono text-xs">{profile.user_id.slice(0, 12)}...</span>
            </div>
          </div>
        </section>
      )}

      {/* Binance API Keys */}
      <section className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-2">Binance API Keys</h2>
        <p className="text-xs text-[#868993] mb-4">
          Your keys are encrypted and stored securely. We test the connection before saving.
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
                  <span className="text-sm text-[#d1d4dc]">Keys Configured</span>
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
                    {formState === "deleting" ? "Deleting..." : "Confirm Delete"}
                  </button>
                  <button
                    className="rounded px-3 py-1.5 text-xs text-[#868993] hover:text-[#d1d4dc] transition-colors"
                    onClick={() => setShowDeleteConfirm(false)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  className="rounded px-3 py-1.5 text-xs text-[#ef5350] border border-[#ef5350]/30 hover:bg-[#ef5350]/10 transition-colors"
                  onClick={() => setShowDeleteConfirm(true)}
                >
                  Delete Keys
                </button>
              )}
            </div>
          </div>
        )}

        <form className="space-y-4" onSubmit={handleSaveKeys}>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="api-key">
              API Key
            </label>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="api-key"
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter your Binance API key"
              type="password"
              value={apiKey}
            />
          </div>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="api-secret">
              API Secret
            </label>
            <input
              autoComplete="off"
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="api-secret"
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder="Enter your Binance API secret"
              type="password"
              value={apiSecret}
            />
          </div>
          <div>
            <label className="block text-sm text-[#868993] mb-1" htmlFor="base-url">
              Base URL
            </label>
            <select
              className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
              id="base-url"
              onChange={(e) => setBaseUrl(e.target.value)}
              value={baseUrl}
            >
              <option value="https://testnet.binancefuture.com">Testnet (testnet.binancefuture.com)</option>
              <option value="https://fapi.binance.com">Mainnet (fapi.binance.com)</option>
            </select>
            <p className="mt-1 text-xs text-[#868993]">
              Start with testnet. Switch to mainnet only after thorough testing.
            </p>
          </div>
          <button
            className="w-full rounded-lg bg-[#2962ff] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#2962ff]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={formState === "saving" || !apiKey.trim() || !apiSecret.trim()}
            type="submit"
          >
            {formState === "saving" ? "Verifying & Saving..." : keysStatus?.configured ? "Update Keys" : "Save Keys"}
          </button>
        </form>

        <div className="mt-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
          <h3 className="text-xs font-semibold text-[#868993] uppercase mb-2">Security Info</h3>
          <ul className="text-xs text-[#868993] space-y-1">
            <li>• Keys are encrypted with AES-256 before storage</li>
            <li>• We verify the connection before saving</li>
            <li>• Plain-text keys are never logged or stored</li>
            <li>• Only you can access your keys through your account</li>
          </ul>
        </div>
      </section>

      <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
        <h2 className="text-lg font-semibold text-[#d1d4dc] mb-2">{t.settings.llmTest}</h2>
        <p className="text-xs text-[#868993] mb-4">
          {t.settings.llmTestDesc}
        </p>
        <div className="space-y-3">
          <input
            className="w-full rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-2.5 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none transition-colors"
            onChange={(e) => setLlmInput(e.target.value)}
            placeholder={t.settings.llmTestPlaceholder}
            value={llmInput}
          />
          <button
            className="rounded-lg bg-[#2962ff] px-4 py-2 text-sm font-medium text-white hover:bg-[#2962ff]/80 transition-colors disabled:opacity-50"
            disabled={llmLoading}
            onClick={handleTestLlm}
          >
            {llmLoading ? t.settings.llmTesting : t.settings.llmTestSend}
          </button>
          {llmError && (
            <div className="rounded-lg px-4 py-3 text-sm bg-[#ef5350]/10 border border-[#ef5350]/30 text-[#ef5350]">
              {llmError}
            </div>
          )}
          {llmOutput !== null && !llmError && (
            <div className="rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc] whitespace-pre-wrap">
              {llmOutput}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
