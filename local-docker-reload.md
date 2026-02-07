# 로컬 Docker: 코드 수정 후 반영 방법

`docker compose --profile full up` 시 **docker-compose.override.yml** 이 자동으로 병합됩니다.

---

## override 사용 시 (로컬 기본): 핫 리로드

override 에서 **api**·**web** 은 볼륨 마운트 + 핫 리로드가 켜져 있어서, **명령어 실행 없이** 저장만 하면 반영됩니다.

| 수정한 것 | 반영 방법 |
|-----------|------------|
| **Python 백엔드** (`src/`) — api | **자동** (uvicorn `--reload`) |
| **웹 프론트** (`web/`) | **자동** (`npm run dev`) |
| **전략 파일** (`scripts/strategies/*.py`) | api는 `--reload-exclude scripts/strategies/*` 로 제외됨 → **api, runner 재시작** 필요 |
| **Python 백엔드** (`src/`) — runner | **runner만 재시작** (worker는 reload 옵션 없음) |

### 명령어가 필요할 때

- **src/ 수정 후 runner만 반영**: `docker compose --profile full restart runner`
- **전략 파일 수정 후 api·runner 반영**: `docker compose --profile full restart api runner`

---

## override 없이 실행한 경우 (빌드 이미지만 사용)

override 없이 `docker compose --profile full up` 한 경우, 또는 CI/배포 환경처럼 override 가 없으면 아래처럼 **재빌드/재시작**이 필요합니다.

| 수정한 것 | 명령어 |
|-----------|--------|
| 전략만 | `docker compose --profile full restart api runner` |
| 백엔드 (`src/`) | `docker compose --profile full up -d --build api runner` |
| 웹 (`web/`) | `docker compose --profile full up -d --build web` |
| 전체 | `docker compose --profile full up -d --build` |

---

## 참고

- **postgres**: 앱 코드 없음.
- **pgadmin**: `profiles: ["tools"]` → `--profile full --profile tools` 로 기동 시 포함.
- **로그**: `docker compose --profile full logs -f api` (서비스명: api, runner, web 등)

---

## Supabase DB 사용(상용 권장)

- `.env`에서 `DATABASE_URL` 또는 `SUPABASE_DATABASE_URL`을 Supabase Postgres DSN으로 설정하면 API/Runner가 해당 DB를 사용합니다.
- 우선순위는 `DATABASE_URL` > `SUPABASE_DATABASE_URL` 입니다.
- 변경 후 반영: `docker compose --profile full up -d --build api runner`

---

## Supabase 가입/인증 활성화

아래 환경변수를 `.env`에 설정한 뒤 재기동하면 `web`에 `/auth` (회원가입/로그인) 화면이 활성화됩니다.

```env
SUPABASE_AUTH_ENABLED=true
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<supabase-anon-key>
AUTH_ALLOW_ADMIN_TOKEN_FALLBACK=false

NEXT_PUBLIC_SUPABASE_AUTH_ENABLED=true
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<supabase-anon-key>
```

- `SUPABASE_AUTH_ENABLED=true`이면 FastAPI는 `Authorization: Bearer <token>`을 Supabase `/auth/v1/user`로 검증합니다.
- `AUTH_ALLOW_ADMIN_TOKEN_FALLBACK=false` 권장 (운영에서 `x-admin-token` 우회 차단).
- 반영 명령어: `docker compose --profile full up -d --build api web`
