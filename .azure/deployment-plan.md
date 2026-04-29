# Deployment Plan: OI Ingestor Container App

Status: Approved for execution

## Request

Deploy the OI Ingestor as an independent Azure Container App so live OI strategy jobs can consume shared Redis time-series data without coupling the poller lifecycle to the job runner.

## Azure Context

- Subscription: `72283c9d-6af6-4d38-91dd-79a4919981ec` (`MCAPS-minsukshin`)
- Resource group: `fdpo-test-rg`
- Container Apps environment: `fdpo-test-cae-vnet`
- ACR: `fdpotestacr.azurecr.io`
- Redis: `fdpo-test-redis.redis.cache.windows.net:6380`
- Network: deploy into the VNet-integrated Container Apps environment so outbound traffic uses the existing NAT path that Redis allows.

## Deployment Decisions

- Workload type: long-running Azure Container App, not a Container App Job, because the process owns a continuous 5-minute polling loop and needs restart policy `Always`.
- App name: `test-oi-ingestor`
- Image repository: `oi-ingestor`
- Dockerfile: `infra/Dockerfile.oi_ingestor`
- Replicas: `min=1`, `max=1` to keep the poller singleton.
- Resources: `0.25 vCPU`, `0.5Gi` memory.
- Ingress: disabled.
- Auth: Container App system-assigned managed identity has Redis `Data Contributor` access policy assignment.
- Redis key authentication remains disabled; the app authenticates with an Entra ID token.
- Runtime environment:
  - `REDIS_HOST=fdpo-test-redis.redis.cache.windows.net`
  - `REDIS_PORT=6380`
  - `REDIS_SSL=true`
  - `REDIS_USERNAME=<container-app-principal-id>`
  - `OI_SYMBOLS=BTCUSDT`
  - `OI_POLL_SECONDS=300`
  - `OI_TRIM_HOURS=30`
  - `LOG_LEVEL=INFO`

## Execution Steps

1. Build and push `oi-ingestor:<commit>` with `az acr build`.
2. Create or update the `test-oi-ingestor` Container App.
3. Assign Redis `Data Contributor` access policy to the app managed identity.
4. Verify app provisioning state and active revision.
5. Inspect logs for Redis connection, backfill, and poll messages.
6. Verify Redis key `oi:BTCUSDT:hist` has entries.
7. Add a GitHub Actions workflow so future changes redeploy the OI ingestor consistently.

## Validation

- Azure resource exists as `Microsoft.App/containerApps/test-oi-ingestor`.
- Active revision has one replica.
- Logs show successful Redis connection and OI polling.
- Redis sorted set `oi:BTCUSDT:hist` has nonzero cardinality.