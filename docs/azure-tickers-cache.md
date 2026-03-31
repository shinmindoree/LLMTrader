# Azure: 대시보드 USD-M 퓨처 시세 캐시

맥 등 **회사 Azure 계정에 로그인할 수 없는** 환경에서는 `REDIS_URL`을 비워 두면 기존처럼 앱이 바이낸스에서 직접 조회합니다.  
**윈도우 랩탑 + Azure MCP / 포털**에서 아래 리소스를 만든 뒤, 배포 앱에 환경 변수만 넣으면 됩니다.

## 1. Azure Cache for Redis

1. Azure Portal에서 **Redis Cache** 생성(최소 SKU로 시작 가능).
2. **액세스 키**와 **호스트 이름**을 확인합니다.
3. Node(`ioredis`)용 연결 문자열은 보통 다음 형태입니다.

   `rediss://:<Primary access key>@<name>.redis.cache.windows.net:6380`

4. 배포 환경(예: Azure Container Apps, App Service, GitHub Actions secret)에 다음을 설정합니다.

| 변수 | 설명 |
|------|------|
| `REDIS_URL` | 위 Redis URL(권장). 또는 `AZURE_REDIS_CONNECTION_STRING`에 동일 값. |
| `TICKER_INGEST_SECRET` | 임의의 긴 비밀값. 인제스트 API 전용. |

로컬 맥에서 Redis를 쓰지 않으면 두 변수 모두 생략해도 됩니다.

## 2. 인제스트(캐시 갱신)

바이낸스 **단일 REST 호출**로 대시보드에 쓰는 심볼만 채운 뒤 Redis에 씁니다.  
동시 접속자가 많아도 **바이낸스 호출 수는 스케줄당 1회**로 고정됩니다.

- **엔드포인트**: `POST /api/internal/ticker-ingest`
- **헤더**: `x-ticker-ingest-secret: <TICKER_INGEST_SECRET과 동일>`

Azure에서 선택 가능한 트리거 예시:

- **Azure Functions** (타이머 트리거) → `fetch`로 위 URL 호출
- **Logic Apps** → 주기적 HTTP POST
- **Container Apps Job** → cron 스타일로 동일 호출

권장 주기: **15~30초** (레이트·비용에 맞게 조정).

## 3. 사용자 요청 경로

`GET /api/binance/futures-tickers`는 (로그인 세션 필요)

