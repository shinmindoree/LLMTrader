# 멀티-심볼 전략 운영 절차 (MFP 기준)

MFP(Multi-Factor Portfolio) 전략을 BTCUSDT 외 심볼로 **백테스트 / 라이브
트레이딩** 하기 위한 표준 운영 절차다. 설계는 전략-비종속이므로, 동일 패턴을
다른 멀티-leg 전략에도 그대로 적용할 수 있다.

> 핵심 원칙: **구조는 고정, 임계값만 심볼별 재피팅.**
> leg 구조(family / 시간프레임 / lookback / feature flag / side)는 BTC 검증
> baseline 그대로 두고, 변동성에 민감한 임계값(`tp_pct` / `sl_pct` /
> `max_hold_h`)만 심볼별로 OOS 검증을 거쳐 다시 맞춘다.

---

## 1. 심볼 상태 모델

| 상태 | 조건 | 동작 |
|------|------|------|
| **baseline** | `BTCUSDT` | 즉시 사용 가능. `resolve_legs`가 코드 내 `ALL_LEGS`를 identity로 반환 → **바이트 동일, 회귀 0**. 아티팩트 불필요. |
| **promoted** | 5개 parquet 피드 + OOS 게이트 통과 + `promoted` 아티팩트 | 백테스트/라이브 사용 가능. `resolve_legs`가 아티팩트의 `leg_overrides`를 baseline에 적용. |
| **validated** | OOS 게이트는 통과했으나 아직 promote 안 함 | 결과 검토용. **라이브 차단** (`_symbol_supported` False). |
| **미지원** | 데이터/아티팩트 없음 | `_symbol_supported` False → 실행 시 명확한 에러로 자동 차단. |

코드상 가드(`if symbol != "BTCUSDT": raise`)는 제거됐지만, 아무 심볼이나
무검증으로 돌지 않는다. **OOS 검증을 통과해 promote된 심볼만** 열린다.

---

## 2. 표준 운영 절차 (신규 심볼 온보딩)

예시는 `ETHUSDT`. 모든 명령은 repo 루트(`c:\dev\llmtrader`)에서 실행한다.

### 사전 준비 (PowerShell)

```powershell
cd c:\dev\llmtrader
$env:PYTHONPATH = "$PWD/src"
```

### ① 데이터 확보 — 5개 parquet 피드 누적

대상 심볼은 아래 5개 피드가 `data/perp_meta/` 에 있어야 한다:
`<SYMBOL>_15m_klines.parquet`, `_oi_5m`, `_funding`, `_taker_5m`, `_lsr_5m`.

```powershell
.\.venv\Scripts\python.exe scripts\ingest_perp_meta.py `
    --symbol ETHUSDT `
    --start 2023-04-01 --end 2026-04-29 `
    --metrics funding,oi,lsr,taker `
    --period 5m
```

> ⚠️ **데이터 제약**: Binance는 OI / taker / LSR 을 **최근 ~30일**만 제공한다
> (funding은 전체 이력 제공). 따라서 장기 백테스트용 과거 데이터는 인제스터를
> **상시 가동하며 누적**해야 한다. 과거 구간이 비어 있으면 그만큼 OOS 윈도우가
> 짧아진다. 운영 인제스터의 `MFP_SYMBOLS` / `OI_SYMBOLS` 환경변수에 신규 심볼을
> 콤마로 추가해 두면 자동 누적된다.
> (배포: `infra/docs/perp-meta-ingestor-deployment.md`, `oi-ingestor-deployment.md`)

15m klines는 별도 백필이 필요할 수 있다 (BTC와 동일 방식).

### ② 재최적화 + OOS 검증 (먼저 `validated`로)

`--promote` 없이 실행해 결과를 **먼저 눈으로 검토**하는 것을 권장한다.

```powershell
.\.venv\Scripts\python.exe scripts\discover_mfp_params.py --symbol ETHUSDT
```

이 드라이버가 하는 일:

1. 코드 baseline의 17개 leg 구조를 로드.
2. leg별로 `tp_pct / sl_pct / max_hold_h` 그리드를 **TRAIN 윈도우**에서 sweep
   (신호는 MFP 자신의 `_SIG_FUNCS`로 생성 → 라이브와 동일 로직, donchian 포함).
3. **TEST 윈도우(OOS)** 로 채점해 leg별 OOS-robust 최적 임계값 선택.
4. 조합 포트폴리오를 **수용 게이트**로 검증
   (통과 leg 비율 ≥ 50%, 평균 TEST 수익 > 0 등).
5. 통과 시 `validated` 아티팩트 저장. **미통과 시 저장 안 함 → 라이브 차단 유지.**

저장 위치:
`data/strategy_params/multi_factor_portfolio/ETHUSDT.json`

기본 윈도우 (필요 시 `--train-start/-end`, `--test-start/-end`로 조정):
- TRAIN: `2023-04-01 ~ 2025-04-30`
- TEST(OOS): `2025-05-01 ~ 2026-04-29`

출력에서 각 leg의 `TRAIN/TEST ret%`, `pf`, 그리고 마지막 줄의
`portfolio gate: passed=...` 를 확인한다.

### ③ 검토 후 promote (라이브 자격 부여)

검토 결과가 만족스러우면 둘 중 하나로 promote:

```powershell
# 방법 A: 재실행하며 바로 promote
.\.venv\Scripts\python.exe scripts\discover_mfp_params.py --symbol ETHUSDT --promote
```

또는 이미 저장된 `validated` 아티팩트의 `status` 필드를
`"validated"` → `"promoted"` 로 직접 수정한다 (JSON 1줄 편집).

`load_promoted`는 `promoted` 상태만 반환하므로, promote 전까지는 라이브가 열리지
않는다.

### ④ 백테스트 / 라이브 실행

평소처럼 `--symbol ETHUSDT`로 실행하면 `resolve_legs`가 promoted 아티팩트를
자동 로드해 적용한다. MFP 표준 실행 플래그:

```
--candle-interval 15m --stop-loss-pct 0 --commission 0.0002 --max-position 1.0
```

(`--stop-loss-pct 0`은 전략이 leg별 intrabar SL을 직접 관리하도록 두는 설정)

---

## 3. 무엇이 바뀌고 무엇이 고정되나

| 분류 | 항목 | 처리 |
|------|------|------|
| **구조 (고정)** | family, `interval_min`, lookback, feature flag, `side` | BTC baseline 그대로. `TUNABLE_FIELDS` 화이트리스트가 강제 — 비-tunable override는 무시(경고 로그). |
| **임계값 (재피팅)** | `tp_pct`, `sl_pct`, `max_hold_h` (드라이버 sweep 대상) | 심볼별 OOS 최적값. |
| **임계값 (override 허용)** | `z_*`, `rsi_*`, `atr_*`, `taker_*`, `oi_*`, `bb_std`, `fund_*` 등 | 화이트리스트에 포함되어 아티팩트로 override 가능하나, 기본 드라이버는 self-normalizing 특성상 baseline 유지. 필요 시 수동/확장으로 조정. |

전체 화이트리스트는 `multi_factor_portfolio_strategy.py`의 `TUNABLE_FIELDS`
참조.

---

## 4. 안전장치 요약

- **BTC 불변**: `resolve_legs("BTCUSDT")`는 `ALL_LEGS`를 identity로 단락 반환.
  타 심볼 아티팩트가 BTC 동작에 절대 영향 없음.
- **구조 보호**: override가 leg 구조를 바꾸려 해도 화이트리스트가 차단.
  baseline `ALL_LEGS`는 복사본으로만 다뤄져 원본 불변.
- **OOS 게이트**: 검증 미통과 심볼은 아티팩트 자체가 저장되지 않아 라이브 불가.
- **promote 분리**: `validated`(검토용)와 `promoted`(라이브)를 분리해 무검증
  라이브 진입 방지.

---

## 5. 빠른 체크리스트

```
[ ] 5개 parquet 피드 확보 (ingest_perp_meta + klines 백필)
[ ] discover_mfp_params.py --symbol XXX  (validated)
[ ] portfolio gate passed=True 확인
[ ] leg별 TRAIN/TEST 수익·pf 검토
[ ] --promote 또는 status 수동 변경 (promoted)
[ ] 운영 인제스터에 심볼 추가 (MFP_SYMBOLS / OI_SYMBOLS)
[ ] --symbol XXX 로 백테스트 → 라이브
```

---

## 6. 관련 파일

| 경로 | 역할 |
|------|------|
| `src/strategy/param_store.py` | `(strategy_id, symbol)` 파라미터 아티팩트 스토어 (local / env / Azure Blob). |
| `scripts/discover_mfp_params.py` | 심볼별 임계값 sweep + OOS 검증 + 아티팩트 emit 드라이버. |
| `scripts/strategies/multi_factor_portfolio_strategy.py` | `resolve_legs` / `_symbol_supported` / `TUNABLE_FIELDS` / `_apply_leg_overrides`. |
| `scripts/ingest_perp_meta.py` | OI/funding/taker/LSR parquet 인제스터. |
| `data/strategy_params/<strategy_id>/<SYMBOL>.json` | 저장된 파라미터 아티팩트. |
