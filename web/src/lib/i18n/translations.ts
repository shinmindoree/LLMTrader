export type Locale = "en" | "ko";
// To add a new language: add to Locale union, add to LOCALES, add new const (e.g. ja) with full TranslationKeys, add to translations record

export const LOCALES: { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ko", label: "한국어" },
];

export type TranslationKeys = typeof en;

const en = {
  landing: {
    hero: {
      badge1: "✨ Natural language strategy · AI code generation",
      badge2: "Binance Futures · Testnet & Mainnet",
      subtitle:
        "Describe your strategy in plain language and AI converts it to code. Backtest, verify on testnet, then deploy to mainnet.",
      getStarted: "Get Started",
      login: "Login",
    },
    features: {
      title: "Core Features",
      subtitle: "From strategy development to live trading — everything in one platform.",
      naturalLanguage: {
        title: "Natural Language Strategy",
        description:
          "Describe strategies in plain language. AI generates Python code automatically. Just input requirements like 'Buy when RSI < 30, sell when RSI > 70'.",
      },
      backtest: {
        title: "Backtest",
        description:
          "Validate and optimize strategy performance with historical data. Detailed analytics on returns, win rate, MDD, and more.",
      },
      live: {
        title: "Live Trading",
        description:
          "Verify safely on testnet, then deploy to mainnet. Real-time position tracking and automated order execution.",
      },
      strategy: {
        title: "Strategy Development",
        description:
          "Python-based strategy templates. Combine TA-Lib indicators (RSI, MACD, EMA) with custom logic.",
      },
      risk: {
        title: "Risk Management",
        description:
          "Systematic risk control with leverage limits, daily loss caps, consecutive loss protection, and Stop Loss.",
      },
    },
    workflow: {
      title: "Natural Language → Code → Backtest → Live",
      description:
        "Describe your strategy in plain words like 'Buy when RSI drops below 30 and sell above 70' — AI generates Python code. Refine via chat, backtest to verify, then run on testnet and mainnet.",
      item1: "Natural language chat for auto strategy code generation & editing",
      item2: "Multi-symbol · Multi-timeframe portfolio mode",
      item3: "Full Binance Futures API integration",
      item4: "Complete order audit logs",
    },
    flowDiagram: {
      step1: "Describe in natural language",
      step1Sub: "AI generates code",
      step2: "Backtest",
      step2Sub: "Historical data validation",
      step3: "Live (Testnet)",
      step3Sub: "Real-world verification",
      step4: "Live (Mainnet)",
      step4Sub: "Live trading",
    },
    cta: {
      title: "Get Started Now",
      subtitle: "Backtest strategies for free and experience live trading on testnet.",
      getStarted: "Get Started",
      login: "Login",
    },
    footer: {
      copyright: "© YHLAB · Binance Futures Trading Platform",
      dashboard: "Dashboard",
      strategies: "Strategies",
      login: "Login",
    },
    screenshots: {
      title: "Feature Highlights",
      subtitle: "Explore each page and its capabilities.",
      dashboard: "Dashboard",
      dashboardDesc: "Track strategy count, backtest runs, and live trading status at a glance.",
      strategies: "Strategies",
      strategiesDesc: "Generate and edit strategies with natural language. AI writes Python code for you.",
      backtestLive: "Backtest & Live Trading",
      backtestLiveDesc:
        "Set key parameters (strategy, symbol, interval, leverage, etc.) and run with one click. View key metrics, equity/PnL charts, and full trade history.",
      altDashboard: "Dashboard - Stats and asset overview",
      altStrategies: "Strategies - Natural language strategy generation",
    },
  },
  nav: {
    features: "Features",
    dashboard: "Dashboard",
    live: "Live",
    backtest: "Backtest",
    settings: "Settings",
    strategies: "Strategies",
  },
  auth: {
    login: "Login",
    logout: "Logout",
    signup: "Sign up",
    signupSuccess: "Account created. Please verify your email and sign in.",
    authFailed: "Authentication failed",
    requestError: "An error occurred while processing your request.",
    description: "Email/password authentication via Supabase.",
    switchToSignup: "Switch to Sign up",
    switchToLogin: "Switch to Login",
    submitting: "Processing...",
  },
  authDisabled: {
    title: "Auth Disabled",
    description: "Supabase auth is disabled. Set NEXT_PUBLIC_SUPABASE_AUTH_ENABLED=true.",
  },
  dashboard: {
    title: "YHLAB Dashboard",
    subtitle: "Control center for backtesting and live trading",
    notConnected: " (Not connected)",
    strategyCount: "Strategies created",
    backtestCount: "Backtests run",
    runningLive: "Running Live",
  },
  sidebar: {
    close: "Close sidebar",
    open: "Open sidebar",
  },
  assetOverview: {
    subtitle: "Assets by exchange (auto-refresh every 15s)",
    comingSoon: "Integration coming soon. We plan to add more major exchanges.",
  },
  tradeAnalysis: {
    chart: "Chart",
    trades: "Trades",
    csvDownload: "Download CSV",
  },
  settings: {
    llmTest: "LLM Connection Test",
    llmTestDesc: "Verify the deployed LLM endpoint. Enter input and send to see the response.",
    llmTestPlaceholder: "Input (e.g. Hello)",
    llmTestSend: "Send",
    llmTesting: "Testing...",
    llmTestFailed: "LLM test failed",
  },
  strategy: {
    codeGenHint:
      "You can continue editing the strategy code in this area once it has been generated.",
    expandSummary:
      "Expand the strategy summary in more detail. Order: strategy overview → entry flow → exit flow → risk management → practical notes. Do not modify the code.",
    streamingStopped: "Stream response was interrupted. Please try again later.",
    serverDelay: "Generation server is slow to respond. Please try again later.",
    typing: "Typing...",
    codeGenerating: "Generating code...",
    previousCodeContext:
      "Below is the strategy code you were using previously. If the user's latest request is a modification or improvement, regenerate based on this code.\n\n",
  },
};

const ko: TranslationKeys = {
  landing: {
    hero: {
      badge1: "✨ 자연어로 전략 작성 · AI 코드 생성",
      badge2: "Binance Futures · Testnet & Mainnet",
      subtitle:
        "자연어로 전략을 설명하면 AI가 코드로 변환해 드립니다. 백테스트로 검증하고, 테스트넷에서 안전하게 테스트한 뒤 메인넷으로 배포하세요.",
      getStarted: "시작하기",
      login: "로그인",
    },
    features: {
      title: "핵심 기능",
      subtitle: "전략 개발부터 라이브 트레이딩까지, 한 플랫폼에서 모든 것을 수행하세요.",
      naturalLanguage: {
        title: "자연어 전략 작성",
        description:
          "코딩 없이 말하듯 전략을 설명하세요. AI가 Python 전략 코드를 자동 생성합니다. 'RSI 30에서 매수, 70에서 매도'처럼 요구사항만 입력하면 됩니다.",
      },
      backtest: {
        title: "백테스트",
        description:
          "과거 데이터로 전략 성과를 검증하고 최적화하세요. 수익률, 승률, MDD 등 상세한 분석을 제공합니다.",
      },
      live: {
        title: "라이브 트레이딩",
        description:
          "테스트넷에서 안전하게 검증 후 메인넷으로 배포. 실시간 포지션 추적과 자동 주문 실행.",
      },
      strategy: {
        title: "전략 개발",
        description:
          "Python 기반 전략 템플릿. RSI, MACD, EMA 등 TA-Lib 인디케이터와 커스텀 로직을 조합하세요.",
      },
      risk: {
        title: "리스크 관리",
        description:
          "레버리지 제한, 일일 손실 한도, 연속 손실 보호, Stop Loss로 리스크를 체계적으로 관리하세요.",
      },
    },
    workflow: {
      title: "자연어 → 코드 → 백테스트 → 라이브",
      description:
        "RSI 30 이하에서 매수하고 70 이상에서 매도해줘처럼 말로만 전략을 설명하면 AI가 Python 코드를 생성합니다. 채팅으로 수정·개선하며, 백테스트로 검증한 뒤 테스트넷과 메인넷에서 실행하세요.",
      item1: "자연어 채팅으로 전략 코드 자동 생성 & 수정",
      item2: "멀티 심볼 · 멀티 타임프레임 포트폴리오 모드",
      item3: "바이낸스 선물 API 완벽 연동",
      item4: "모든 주문 기록 감사 로그",
    },
    flowDiagram: {
      step1: "자연어로 설명",
      step1Sub: "AI가 코드 생성",
      step2: "Backtest",
      step2Sub: "과거 데이터 검증",
      step3: "Live (Testnet)",
      step3Sub: "실전 검증",
      step4: "Live (Mainnet)",
      step4Sub: "실거래",
    },
    cta: {
      title: "지금 시작하세요",
      subtitle: "무료로 전략을 백테스트하고 테스트넷에서 라이브 트레이딩을 경험해 보세요.",
      getStarted: "시작하기",
      login: "로그인",
    },
    footer: {
      copyright: "© YHLAB · Binance Futures Trading Platform",
      dashboard: "대시보드",
      strategies: "전략",
      login: "로그인",
    },
    screenshots: {
      title: "기능 화면 미리보기",
      subtitle: "각 페이지와 기능을 확인해 보세요.",
      dashboard: "대시보드",
      dashboardDesc: "생성된 전략 수, 백테스트 진행 현황, 라이브 실행 상태를 한눈에 파악합니다.",
      strategies: "전략",
      strategiesDesc: "자연어로 전략을 생성·수정합니다. AI가 Python 코드를 작성해 드립니다.",
      backtestLive: "백테스트 & 라이브 트레이딩",
      backtestLiveDesc:
        "주요 인자(전략·심볼·레버리지 등) 설정 후 버튼 클릭으로 실행. 수익률·차트·거래 내역을 한눈에 확인하세요.",
      altDashboard: "대시보드 - 통계 및 자산 요약",
      altStrategies: "전략 - 자연어로 전략 생성",
    },
  },
  nav: {
    features: "기능",
    dashboard: "대시보드",
    live: "라이브",
    backtest: "백테스트",
    settings: "설정",
    strategies: "전략",
  },
  auth: {
    login: "로그인",
    logout: "로그아웃",
    signup: "회원가입",
    signupSuccess: "회원가입 완료. 이메일 인증 후 로그인하세요.",
    authFailed: "인증에 실패했습니다.",
    requestError: "요청 처리 중 오류가 발생했습니다.",
    description: "Supabase 이메일/비밀번호 인증을 사용합니다.",
    switchToSignup: "회원가입으로 전환",
    switchToLogin: "로그인으로 전환",
    submitting: "처리 중...",
  },
  authDisabled: {
    title: "Auth 비활성화",
    description: "Supabase auth가 비활성화되어 있습니다. NEXT_PUBLIC_SUPABASE_AUTH_ENABLED=true로 설정하세요.",
  },
  dashboard: {
    title: "YHLAB Dashboard",
    subtitle: "백테스트 및 라이브 트레이딩 제어 센터",
    notConnected: " (연결안됨)",
    strategyCount: "생성된 전략 수",
    backtestCount: "진행한 백테스트 수",
    runningLive: "실행 중인 Live",
  },
  sidebar: {
    close: "사이드바 닫기",
    open: "사이드바 열기",
  },
  assetOverview: {
    subtitle: "거래소별 자산 현황 (15초 자동 새로고침)",
    comingSoon: "연동 준비중입니다. 주요 코인거래소 연동을 확장할 예정입니다.",
  },
  tradeAnalysis: {
    chart: "차트",
    trades: "거래 내역",
    csvDownload: "CSV 다운로드",
  },
  settings: {
    llmTest: "LLM 연결 테스트",
    llmTestDesc: "배포된 LLM 엔드포인트가 정상 동작하는지 확인합니다. 입력 후 전송하면 응답을 출력합니다.",
    llmTestPlaceholder: "입력 (예: 안녕)",
    llmTestSend: "전송",
    llmTesting: "테스트 중...",
    llmTestFailed: "LLM 테스트 실패",
  },
  strategy: {
    codeGenHint:
      "전략 코드가 생성되면 이 영역에서 계속 편집할 수 있습니다.",
    expandSummary:
      "방금 전략 요약을 이어서 더 자세히 설명해줘. 전략 개요 → 진입 흐름 → 청산 흐름 → 리스크 관리 → 실전 주의사항 순서로 써줘. 코드는 변경하지 마.",
    streamingStopped: "스트림 응답이 중단되었습니다. 잠시 후 다시 시도해주세요.",
    serverDelay: "생성 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.",
    typing: "답변 작성 중...",
    codeGenerating: "코드 생성 중...",
    previousCodeContext:
      "아래는 직전까지 사용 중인 전략 코드입니다. 사용자의 최신 요청이 수정/개선 지시라면 이 코드를 기반으로 재생성하세요.\n\n",
  },
};

export const translations: Record<Locale, TranslationKeys> = { en, ko };

export const DEFAULT_LOCALE: Locale = "en";
