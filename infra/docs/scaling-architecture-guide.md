# Scaling Architecture Guide

LLMTrader 서비스의 유저 규모별 인프라 아키텍처 권장안.

## 서비스 구성

| 서비스 | 역할 | 특성 |
|--------|------|------|
| **web** | Next.js 프론트엔드 (SSR + static) | Stateless, I/O-bound |
| **api** | FastAPI REST API | Stateless, I/O-bound (async) |
| **relay** | Azure OpenAI LLM 프록시 | Stateless, I/O-bound |
| **runner** | Job 워커 (라이브 트레이딩 + 백테스트) | Stateful, CPU-bound |

## 핵심 설정값

- `RUNNER_LIVE_CONCURRENCY`: 한 Runner 인스턴스가 동시 실행하는 LIVE 잡 수 (기본 5)
- 전체 동시 LIVE 잡 수 = Runner replica 수 × `RUNNER_LIVE_CONCURRENCY`
- 플랜별 유저당 최대 LIVE 잡: free/pro=5, enterprise=10

## 현재 아키텍처의 병목

| 병목 | 설명 |
|------|------|
| 1 잡 = 1 WebSocket | 각 라이브 잡이 Binance WebSocket 연결을 독립 유지 |
| DB 폴링 (job claim) | 각 concurrency 루프가 `claim_next_pending_job`으로 DB 폴링 |
| DB 폴링 (stop) | 각 잡마다 0.5초 간격으로 DB 폴링하여 중지 요청 확인 |
| heartbeat write | 각 잡이 주기적으로 DB에 heartbeat 기록 |

---

## Phase 1: 유저 ~1,000명 (동시 잡 ~5,000개)

**현재 아키텍처 유지, 설정 조정만으로 대응.**

### Replica 설정

| 서비스 | Min | Max | vCPU | Memory | 근거 |
|--------|-----|-----|------|--------|------|
| web | 1 | 3 | 0.25 | 0.5Gi | SSR 가벼움, 수평 확장 대응 |
| api | 1 | 5 | 0.5 | 1Gi | 모든 요청의 진입점, async I/O |
| relay | 1 | 3 | 0.25 | 0.5Gi | 순수 프록시, LLM 연산은 Azure OpenAI에서 수행 |
| runner | 1 | 20 | 2 | 4Gi | CPU-bound, 동시 잡 처리 |

### Runner 설정

```
RUNNER_LIVE_CONCURRENCY=100
Runner replica: 10~20
→ 동시 잡: 1,000~2,000
```

### 필요 작업

- `RUNNER_LIVE_CONCURRENCY`를 50~100으로 증가
- Runner vCPU를 2~4, Memory 4Gi로 증가
- DB를 Azure PostgreSQL Flexible Server General Purpose (4 vCore) 이상
- stop poller 간격을 0.5초 → 2~3초로 완화 (DB 부하 절감)

### 예상 월 비용: $300~800

---

## Phase 2: 유저 1,000~5,000명 (동시 잡 5,000~25,000개)

**아키텍처 개선 필요.**

### 핵심 변경사항

#### 1) WebSocket 공유 (가장 큰 효과)

현재: BTCUSDT 1m을 100명이 돌리면 → 100개 WebSocket  
개선: 같은 symbol×interval은 1개 WebSocket 공유, 내부 pub/sub으로 분배

```
50,000 잡이지만 고유 symbol×interval 조합은 ~50~200개
→ WebSocket 연결: 50,000 → 200개
```

#### 2) DB 폴링 → 이벤트 기반 전환

| 현재 | 개선 |
|------|------|
| stop 요청: DB 폴링 (0.5초) | Redis Pub/Sub 또는 PostgreSQL NOTIFY/LISTEN |
| heartbeat: 개별 DB write | 배치 write (10초마다 한 번에 flush) |
| job claim: DB 폴링 | Redis Queue (Bull/BullMQ 패턴) |

#### 3) Runner 멀티 프로세스

- 현재: 1 프로세스 + asyncio (GIL 제약)
- 개선: 1 컨테이너에서 worker 프로세스 N개 (Python multiprocessing)
- CPU 코어 활용 극대화

### Replica 설정

| 서비스 | Min | Max | vCPU | Memory |
|--------|-----|-----|------|--------|
| web | 2 | 5 | 0.5 | 1Gi |
| api | 2 | 10 | 1 | 2Gi |
| relay | 1 | 5 | 0.5 | 1Gi |
| runner | 10 | 50 | 4 | 8Gi |
| Redis | 클러스터 | - | - | - |

### 예상 월 비용: $1,500~3,000

---

## Phase 3: 유저 10,000명+ (동시 잡 50,000개+)

**분산 시스템 아키텍처. Azure Container Apps → AKS 전환 권장.**

### 아키텍처 다이어그램

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  API (10+)  │────▶│  Redis/Message   │────▶│  Runner Pool    │
│             │     │  Queue           │     │  (50~100대)     │
└─────────────┘     └──────────────────┘     └─────────────────┘
                           │                         │
                    ┌──────┴──────┐           ┌──────┴──────┐
                    │ Redis       │           │ WS Gateway  │
                    │ Pub/Sub     │           │ (5~10대)    │
                    │ (stop/event)│           │ 공유 WebSocket│
                    └─────────────┘           └─────────────┘
```

### 컴포넌트 역할

| 컴포넌트 | 역할 | 인스턴스 |
|---------|------|---------|
| **WS Gateway** | symbol×interval별 WebSocket 1개 유지, 데이터를 Runner에 push | 5~10대 |
| **Runner** | 전략 로직만 실행 (WebSocket 직접 연결 안 함) | 50~100대 |
| **Redis** | Job queue + Pub/Sub (stop/event) + heartbeat 캐시 | 클러스터 |
| **DB** | 결과 저장만 (hot path에서 제거) | General Purpose 8+ vCore |

### 핵심 변경사항

1. **WebSocket Gateway 분리** — Runner가 직접 Binance에 연결하지 않음
2. **Redis Queue로 잡 분배** — DB 폴링 완전 제거
3. **Runner는 순수 연산만** — 캔들 데이터를 받아서 지표 계산 → 주문 신호 반환
4. **Container Apps → AKS 전환** — 50+ 인스턴스 오케스트레이션에 Kubernetes 필요

### Replica 설정

| 서비스 | Min | Max | vCPU | Memory |
|--------|-----|-----|------|--------|
| web | 3 | 10 | 0.5 | 1Gi |
| api | 5 | 20 | 1 | 2Gi |
| relay | 2 | 10 | 0.5 | 1Gi |
| WS Gateway | 5 | 10 | 2 | 4Gi |
| runner | 50 | 100 | 4 | 8Gi |
| Redis | 클러스터 (3+3) | - | - | - |
| DB | General Purpose | - | 8+ vCore | 32Gi+ |

### 예상 월 비용: $5,000~15,000

---

## 요약: 스케일링 우선순위

| 순서 | 작업 | 효과 | 난이도 |
|------|------|------|--------|
| 1 | min-replicas=1 설정 | 콜드 스타트 제거 | 낮음 |
| 2 | RUNNER_LIVE_CONCURRENCY 증가 + Runner 리소스 업 | 동시 잡 수 증가 | 낮음 |
| 3 | DB 업그레이드 (General Purpose) | DB 병목 해소 | 낮음 |
| 4 | WebSocket 공유 구현 | 연결 수 99% 절감 | 중간 |
| 5 | Redis 도입 (job queue + pub/sub) | DB 폴링 제거 | 중간 |
| 6 | WS Gateway 분리 | Runner 경량화 | 높음 |
| 7 | AKS 전환 | 대규모 오케스트레이션 | 높음 |

> **원칙**: 유저가 실제로 해당 규모에 도달하기 전에 미리 아키텍처를 바꾸지 않는다. 복잡성 증가로 개발 속도가 떨어지는 것이 더 큰 리스크.
