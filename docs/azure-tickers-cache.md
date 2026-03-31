# Azure: 대시보드 USD-M 퓨처 시세 캐시

배포 앱(예: Azure Container Apps, App Service) 환경 변수와 스케줄 작업만 맞추면 됩니다.

## 1. Azure Cache for Redis

1. Azure Portal에서 **Redis Cache** 생성.
2. **액세스 키**와 **호스트 이름** 확인.
3. Node(`ioredis`)용 연결 문자열 예시:

   `rediss://:<Primary access key>@<name>.redis.cache.windows.net:6380`

4. 앱 설정:

| 변수 | 설명 |
|------|------|
| `REDIS_URL` | 위 Redis URL(권장). 또는 `AZURE_REDIS_CONNECTION_STRING`에 동일 값. |
| `TICKER_INGEST_SECRET` | 임의의 긴 비밀값. 인제스트 API 전용. |

Redis와 시크릿이 없으면 티커 API는 캐시 없이 바이낸스를 직접 조회합니다(부하·레이트 한도에 유의).

## 2. 인제스트(캐시 갱신)

바이낸스 **단일 REST 호출**로 대시보드 심볼만 채운 뒤 Redis에 씁니다.

- **엔드포인트**: `POST /api/internal/ticker-ingest`
- **헤더**: `x-ticker-ingest-secret: <TICKER_INGEST_SECRET과 동일>`

트리거 예: Azure Functions 타이머, Logic Apps, Container Apps Job. 권장 주기 **15~30초**.

## 3. 사용자 요청 경로

`GET /api/binance/futures-tickers`(로그인 세션 필요):

1. Redis에 **45초 이내** 갱신된 캐시가 있으면 우선 사용
2. 없거나 일부만 있으면 **한 번의** `fapi/v1/ticker/24hr` 목록 조회로 보충
