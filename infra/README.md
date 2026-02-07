# Local development with Docker Compose

This repo uses a **root `.env`** for runtime configuration. For local development, we recommend
running **Postgres via Docker Compose** and running the Python/Next.js processes normally.

## Start Postgres (default)

From the repo root:

```bash
docker compose up -d
```

Postgres will be available on `localhost:5432` by default.

### Optional: pgAdmin

```bash
docker compose --profile tools up -d
```

Then open `http://localhost:5050`.

## Environment variables

Compose uses these variables (defaults are provided if missing):

- `POSTGRES_DB` (default: `llmtrader`)
- `POSTGRES_USER` (default: `llmtrader`)
- `POSTGRES_PASSWORD` (default: `llmtrader`)
- `POSTGRES_PORT_HOST` (default: `5432`)
- `PGADMIN_DEFAULT_EMAIL` (default: `admin@local`)
- `PGADMIN_DEFAULT_PASSWORD` (default: `admin`)
- `PGADMIN_PORT_HOST` (default: `5050`)

To run the full stack (Postgres + API + runner + web):

```bash
docker compose --profile full up -d
```

Web will be available on `http://localhost:3000` and API on `http://localhost:8000`.

### 로컬 개발: 볼륨 마운트 + 핫 리로드

`docker-compose.override.yml`이 있으면 `docker compose --profile full up` 시 자동으로 병합됩니다.

- **API**: `./src`가 마운트되고 uvicorn `--reload`로 실행됩니다. `src/` 코드 수정 시 재빌드 없이 자동 반영됩니다.
- **Web**: `./web`이 마운트되고 `npm run dev`로 실행됩니다. Next.js HMR로 프론트 변경 시 즉시 반영됩니다.

이미지를 한 번 빌드한 뒤에는 API/Web 코드만 수정해도 변경사항을 바로 확인할 수 있습니다.

You'll also typically set:

- `DATABASE_URL` (recommended)
- `ADMIN_TOKEN` (used when `SUPABASE_AUTH_ENABLED=false`, or as fallback if enabled)

### Production DB (Supabase)

For production, point API/runner to Supabase Postgres by setting one of:

- `DATABASE_URL=postgresql+asyncpg://...` (preferred, explicit)
- or `SUPABASE_DATABASE_URL=postgresql+asyncpg://...` (used when `DATABASE_URL` is empty)

`DATABASE_URL` has higher priority than `SUPABASE_DATABASE_URL`.

## Stop everything

```bash
docker compose down
```

## Relay (LLM proxy) on Azure Container Apps

LLM 전략 생성 프록시: Docker 빌드, 환경 변수, Container Apps 배포 절차는 [docs/relay-container-apps.md](docs/relay-container-apps.md) 참고.

**맥북 + Portal 배포 (az login 불가 환경)**: [docs/relay-azure-portal-deploy-guide.md](docs/relay-azure-portal-deploy-guide.md) 에 단계별 가이드 정리.
