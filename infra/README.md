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

You'll also typically set:

- `DATABASE_URL` (recommended)
- `ADMIN_TOKEN` (required while auth is not implemented)

## Stop everything

```bash
docker compose down
```

## Relay (LLM proxy) on Azure Container Apps

LLM 전략 생성 프록시: Docker 빌드, 환경 변수, Container Apps 배포 절차는 [docs/relay-container-apps.md](docs/relay-container-apps.md) 참고.

**맥북 + Portal 배포 (az login 불가 환경)**: [docs/relay-azure-portal-deploy-guide.md](docs/relay-azure-portal-deploy-guide.md) 에 단계별 가이드 정리.
