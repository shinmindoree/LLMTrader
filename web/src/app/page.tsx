"use client";

import Link from "next/link";

export default function LandingPage() {
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
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-[#2962ff]/30 bg-[#2962ff]/10 px-4 py-1.5 text-xs font-medium text-[#2962ff]">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#26a69a] opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-[#26a69a]" />
            </span>
            Binance Futures · Testnet & Mainnet
          </div>
          <h1 className="text-4xl font-bold tracking-tight text-[#d1d4dc] sm:text-5xl md:text-6xl">
            AI-Powered
            <span className="bg-gradient-to-r from-[#2962ff] via-[#26a69a] to-[#2962ff] bg-clip-text text-transparent">
              {" "}Crypto Trading
            </span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-[#868993] sm:text-xl">
            백테스트로 전략을 검증하고, 테스트넷에서 안전하게 검증한 뒤, 메인넷에서 라이브 트레이딩을 실행하세요.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              className="w-full rounded-lg bg-[#2962ff] px-8 py-4 text-center font-semibold text-white transition-all hover:bg-[#1e53e5] hover:shadow-lg hover:shadow-[#2962ff]/25 sm:w-auto"
              href="/dashboard"
            >
              Dashboard 시작하기
            </Link>
            <Link
              className="w-full rounded-lg border border-[#2a2e39] bg-[#1e222d] px-8 py-4 text-center font-semibold text-[#d1d4dc] transition-all hover:border-[#2962ff] hover:bg-[#252936] sm:w-auto"
              href="/auth"
            >
              로그인
            </Link>
          </div>
        </div>
      </section>

      <section id="features" className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
        <div className="mx-auto max-w-6xl">
          <h2 className="text-center text-3xl font-bold text-[#d1d4dc] md:text-4xl">
            핵심 기능
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-center text-[#868993]">
            전략 개발부터 라이브 트레이딩까지, 한 플랫폼에서 모든 것을 수행하세요
          </p>

          <div className="mt-16 grid gap-8 md:grid-cols-2 lg:grid-cols-4">
            <FeatureCard
              icon={<BacktestIcon />}
              title="백테스트"
              description="과거 데이터로 전략 성과를 검증하고 최적화하세요. 수익률, 승률, MDD 등 상세한 분석을 제공합니다."
              accent="#2962ff"
            />
            <FeatureCard
              icon={<LiveIcon />}
              title="라이브 트레이딩"
              description="테스트넷에서 안전하게 검증 후 메인넷으로 배포. 실시간 포지션 추적과 자동 주문 실행."
              accent="#26a69a"
            />
            <FeatureCard
              icon={<StrategyIcon />}
              title="전략 개발"
              description="Python 기반 전략 템플릿. RSI, MACD, EMA 등 TA-Lib 인디케이터와 커스텀 로직을 조합하세요."
              accent="#ff9800"
            />
            <FeatureCard
              icon={<RiskIcon />}
              title="리스크 관리"
              description="레버리지 제한, 일일 손실 한도, 연속 손실 보호, Stop Loss로 리스크를 체계적으로 관리하세요."
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
                  전략 → 백테스트 → 라이브
                </h3>
                <p className="mt-4 text-[#868993]">
                  하나의 전략 파일로 전체 워크플로우를 구축합니다. 인디케이터 기반 전략 템플릿을 복사해
                  나만의 시그널 로직을 추가하고, 백테스트로 검증한 뒤 테스트넷과 메인넷에서 실행하세요.
                </p>
                <ul className="mt-6 space-y-3 text-sm text-[#d1d4dc]">
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    멀티 심볼 · 멀티 타임프레임 포트폴리오 모드
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    바이낸스 선물 API 완벽 연동
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
                    모든 주문 기록 감사 로그
                  </li>
                </ul>
              </div>
              <div className="relative flex items-center justify-center p-8 md:p-12">
                <div className="relative">
                  <div className="absolute -inset-4 rounded-2xl bg-gradient-to-br from-[#2962ff]/20 to-[#26a69a]/20 blur-2xl" />
                  <div className="relative rounded-xl border border-[#2a2e39] bg-[#131722] p-6 font-mono text-xs">
                    <FlowDiagram />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-[#2a2e39] px-6 py-20 md:py-28">
        <div className="mx-auto max-w-3xl text-center">
          <h2 className="text-3xl font-bold text-[#d1d4dc] md:text-4xl">
            지금 시작하세요
          </h2>
          <p className="mt-4 text-[#868993]">
            무료로 전략을 백테스트하고 테스트넷에서 라이브 트레이딩을 경험해 보세요.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              className="w-full rounded-lg bg-[#2962ff] px-8 py-4 text-center font-semibold text-white transition-all hover:bg-[#1e53e5] sm:w-auto"
              href="/dashboard"
            >
              Dashboard 시작하기
            </Link>
            <Link
              className="w-full rounded-lg border border-[#2a2e39] px-8 py-4 text-center font-semibold text-[#d1d4dc] transition-all hover:border-[#2962ff] sm:w-auto"
              href="/auth"
            >
              로그인
            </Link>
          </div>
        </div>
      </section>

      <footer className="border-t border-[#2a2e39] px-6 py-8">
        <div className="mx-auto max-w-6xl flex flex-col items-center justify-between gap-4 sm:flex-row">
          <span className="text-sm text-[#868993]">© LLMTrader · Binance Futures Trading Platform</span>
          <div className="flex gap-6 text-sm">
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/dashboard">
              Dashboard
            </Link>
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/strategies">
              Strategies
            </Link>
            <Link className="text-[#868993] hover:text-[#d1d4dc]" href="/auth">
              Login
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
  accent,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  accent: string;
}) {
  return (
    <div className="group rounded-xl border border-[#2a2e39] bg-[#1e222d] p-6 transition-all hover:border-[#2962ff]/50 hover:shadow-lg hover:shadow-[#2962ff]/5">
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

function FlowDiagram() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#ff9800]/20 px-2 py-0.5 text-[#ff9800]">1</span>
        <span className="text-[#d1d4dc]">strategy.py</span>
        <span className="text-[#868993]">전략 작성</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-[#2962ff]">2</span>
        <span className="text-[#d1d4dc]">Backtest</span>
        <span className="text-[#868993]">과거 데이터 검증</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#26a69a]/40 bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#26a69a]/20 px-2 py-0.5 text-[#26a69a]">3</span>
        <span className="text-[#d1d4dc]">Live (Testnet)</span>
        <span className="text-[#868993]">실전 검증</span>
      </div>
      <div className="ml-6 h-4 w-px bg-[#2a2e39]" />
      <div className="flex items-center gap-3 rounded-lg border border-[#26a69a]/40 bg-[#1e222d] px-4 py-3">
        <span className="rounded bg-[#26a69a]/20 px-2 py-0.5 text-[#26a69a]">4</span>
        <span className="text-[#d1d4dc]">Live (Mainnet)</span>
        <span className="text-[#868993]">실거래</span>
      </div>
    </div>
  );
}
