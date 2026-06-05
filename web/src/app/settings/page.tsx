"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import {
  deleteBinanceCredential,
  getUserProfile,
  listBinanceCredentials,
  listWalletAccounts,
  setBinanceCredential,
} from "@/lib/api";
import type {
  BinanceCredential,
  BinanceCredentialEnv,
  UserProfile,
  WalletAccount,
} from "@/lib/types";

const ENV_ORDER: BinanceCredentialEnv[] = ["mainnet", "testnet"];

const ENV_LABELS: Record<BinanceCredentialEnv, { title: string; desc: string }> = {
  mainnet: {
    title: "Mainnet",
    desc: "실제 바이낸스 (fapi.binance.com / api.binance.com). 선물·현물·Earn 등 모든 실거래 전략에서 공통으로 사용됩니다.",
  },
  testnet: {
    title: "Testnet (Demo Trading)",
    desc: "바이낸스 데모 트레이딩 (demo.binance.com 발급). 키 한 쌍으로 선물(testnet.binancefuture.com)·현물(demo-api.binance.com)을 모두 사용하므로, 디렉셔널 알파와 차익거래 테스트넷 거래에 공통으로 쓰입니다.",
  },
};

function CredentialCard({
  cred,
  onChanged,
}: {
  cred: BinanceCredential;
  onChanged: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const info = ENV_LABELS[cred.env as BinanceCredentialEnv];

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMsg({ type: "error", text: "API Key와 Secret을 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await setBinanceCredential(cred.env, {
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
      });
      setApiKey("");
      setApiSecret("");
      setMsg({ type: "success", text: "저장되었습니다." });
      onChanged();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : "저장 실패" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    setMsg(null);
    try {
      await deleteBinanceCredential(cred.env);
      setShowDeleteConfirm(false);
      setMsg({ type: "success", text: "삭제되었습니다." });
      onChanged();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : "삭제 실패" });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[#d1d4dc]">{info.title}</h3>
          <p className="mt-0.5 text-xs leading-snug text-[#868993]">{info.desc}</p>
        </div>
        {cred.configured && (
          <span className="flex shrink-0 items-center gap-1.5 text-xs text-[#26a69a]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
            연결됨
          </span>
        )}
      </div>

      {msg && (
        <div
          className={`mb-3 rounded px-3 py-2 text-xs ${
            msg.type === "success"
              ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
              : "border border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
          }`}
        >
          {msg.text}
        </div>
      )}

      {cred.configured && (
        <div className="mb-3 flex items-center justify-between rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
          <span className="font-mono text-xs text-[#868993]">{cred.api_key_masked ?? "****"}</span>
          {showDeleteConfirm ? (
            <div className="flex items-center gap-2">
              <button
                className="rounded bg-[#ef5350] px-2 py-1 text-xs text-white hover:bg-[#ef5350]/80 disabled:opacity-50"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? "삭제 중..." : "확인"}
              </button>
              <button
                className="rounded px-2 py-1 text-xs text-[#868993] hover:text-[#d1d4dc]"
                onClick={() => setShowDeleteConfirm(false)}
              >
                취소
              </button>
            </div>
          ) : (
            <button
              className="rounded border border-[#ef5350]/30 px-2 py-1 text-xs text-[#ef5350] hover:bg-[#ef5350]/10"
              onClick={() => setShowDeleteConfirm(true)}
            >
              삭제
            </button>
          )}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-3">
        <input
          type="password"
          autoComplete="off"
          placeholder="API Key"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          type="password"
          autoComplete="off"
          placeholder="API Secret"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
        />
        <button
          type="submit"
          disabled={saving || !apiKey.trim() || !apiSecret.trim()}
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "검증 중..." : cred.configured ? "업데이트" : "저장 및 연결 확인"}
        </button>
      </form>
    </div>
  );
}

export default function SettingsPage() {
  const { t } = useI18n();

  const { data: profile, isLoading: profileLoading } = useSWR<UserProfile>(
    "userProfile",
    () => getUserProfile(),
  );

  const {
    data: credentials,
    mutate: mutateCredentials,
    isLoading: credsLoading,
  } = useSWR<BinanceCredential[]>("binanceCredentials", () => listBinanceCredentials());

  const loading = profileLoading || credsLoading;

  const credMap = new Map((credentials ?? []).map((c) => [c.env, c]));
  const allCreds: BinanceCredential[] = ENV_ORDER.map(
    (env) => credMap.get(env) ?? { env, configured: false },
  );

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
          <h2 className="mb-4 text-lg font-semibold text-[#d1d4dc]">{t.settingsPage.account}</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.email}</span>
              <span className="text-[#d1d4dc]">{profile.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.plan}</span>
              <span
                className={
                  profile.plan === "enterprise"
                    ? "font-semibold uppercase text-[#ff9800]"
                    : profile.plan === "pro"
                      ? "font-semibold uppercase text-[#2962ff]"
                      : "uppercase text-[#868993]"
                }
              >
                {profile.plan}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.userId}</span>
              <span className="font-mono text-xs text-[#868993]">
                {profile.user_id.slice(0, 12)}...
              </span>
            </div>
          </div>
        </section>
      )}

      <section className="mt-6">
        <h2 className="mb-1 text-lg font-semibold text-[#d1d4dc]">
          {t.settingsPage.binanceApiKeys}
        </h2>
        <p className="mb-4 text-xs text-[#868993]">{t.settingsPage.keysSecureInfo}</p>
        <div className="space-y-4">
          {allCreds.map((cred) => (
            <CredentialCard
              key={cred.env}
              cred={cred}
              onChanged={() => void mutateCredentials()}
            />
          ))}
        </div>

        <div className="mt-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase text-[#868993]">
            {t.settingsPage.securityInfo}
          </h3>
          <ul className="space-y-1 text-xs text-[#868993]">
            <li>• {t.settingsPage.securityItem1}</li>
            <li>• {t.settingsPage.securityItem2}</li>
            <li>• {t.settingsPage.securityItem3}</li>
            <li>• {t.settingsPage.securityItem4}</li>
          </ul>
        </div>
      </section>

      <SubAccountSection />
    </main>
  );
}

// ── Sub-account topology section ─────────────────────────────────────

function SubAccountSection(): React.JSX.Element {
  const { data: wallets, isLoading } = useSWR<WalletAccount[]>(
    "walletAccounts:mainnet",
    () => listWalletAccounts("mainnet"),
  );

  const subs = (wallets ?? []).filter((w) => w.role === "sub");
  const activeSubs = subs.filter((w) => w.status === "active").length;
  const masterExists = (wallets ?? []).some((w) => w.role === "master");

  let badgeText: string;
  let badgeCls: string;
  if (isLoading) {
    badgeText = "확인 중...";
    badgeCls = "border-[#2a2e39] bg-[#131722] text-[#868993]";
  } else if (subs.length === 0) {
    badgeText = "미설정";
    badgeCls = "border-[#F0B90B]/30 bg-[#F0B90B]/10 text-[#F0B90B]";
  } else if (activeSubs === subs.length && masterExists) {
    badgeText = `${activeSubs} active`;
    badgeCls = "border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]";
  } else {
    badgeText = `${activeSubs} / ${subs.length} active`;
    badgeCls = "border-[#868993]/30 bg-[#868993]/10 text-[#868993]";
  }

  return (
    <section className="mt-6">
      <div className="mb-1 flex items-center gap-2">
        <h2 className="text-lg font-semibold text-[#d1d4dc]">
          Sub-account Wallets
        </h2>
        <span
          className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badgeCls}`}
        >
          {badgeText}
        </span>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-[#868993]">
        여러 전략(디렉셔널·차익·파생·Earn)을 병행 운영할 때 자금 충돌을
        막기 위해 각 전략을 별도의 Binance 서브 계정에 격리합니다. 마스터
        키 1개로 모든 서브를 생성·통제하며, 자금은 Capital Router가 자동으로
        마스터↔서브 사이를 이동시킵니다.
      </p>

      <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
        {subs.length === 0 ? (
          <div className="space-y-3">
            <p className="text-sm text-[#d1d4dc]">
              아직 서브 계정이 설정되지 않았습니다.
            </p>
            <p className="text-xs text-[#868993]">
              온보딩 위저드에서 마스터 키 등록 → 4개 서브 자동 생성 → 거래 키
              입력의 3단계로 5분 안에 설정할 수 있습니다.
            </p>
            <Link
              href="/onboarding/wallets"
              className="inline-flex items-center rounded bg-[#2962ff] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80"
            >
              온보딩 시작 →
            </Link>
          </div>
        ) : (
          <>
            <ul className="space-y-2">
              {subs.map((w) => (
                <li
                  key={w.id}
                  className="flex items-center justify-between rounded border border-[#2a2e39] bg-[#131722] px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[#d1d4dc]">
                        {w.alias}
                      </span>
                      <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#a0a3ad]">
                        {w.purpose}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[10px] text-[#868993]">
                      {w.sub_account_email ?? "—"}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                      w.status === "active"
                        ? "border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
                        : w.status === "key_missing"
                          ? "border-[#F0B90B]/30 bg-[#F0B90B]/10 text-[#F0B90B]"
                          : w.status === "key_invalid"
                            ? "border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
                            : "border-[#868993]/30 bg-[#868993]/10 text-[#868993]"
                    }`}
                  >
                    {w.status}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-center justify-between gap-3">
              <p className="text-[11px] text-[#868993]">
                서브 추가 생성, 키 교체, IP 화이트리스트 변경은 온보딩 위저드에서
                할 수 있습니다.
              </p>
              <Link
                href="/onboarding/wallets"
                className="shrink-0 rounded border border-[#2962ff]/40 px-3 py-1.5 text-xs font-medium text-[#2962ff] transition-colors hover:bg-[#2962ff]/10"
              >
                관리 →
              </Link>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
