# Relay (LLM Proxy) on Azure Container Apps

LLM 전략 생성 프록시 서버: Entra ID로 Azure OpenAI에 접근하고, 클라이언트는 `POST /generate`로 전략 코드를 요청한다.

## 역할 및 API

- **역할**: SaaS 백엔드 또는 로컬 클라이언트가 호출하면, 프록시가 Entra ID 토큰으로 Azure OpenAI Chat Completions를 호출해 전략 Python 코드를 생성해 반환한다.
- **API 스펙**:
  - `GET /docs`: Swagger UI (헬스 확인용으로 사용 가능).
  - `POST /generate`: Body `{"user_prompt": "자연어 전략 설명"}`. 응답 `{"code": "생성된 Python 코드", "model_used": "모델명"}`.
- SaaS에서의 접근 방식: [saas-proxy-access.md](saas-proxy-access.md).

## 환경 변수 / 시크릿

| 이름 | 필수 | 설명 |
|------|------|------|
| `AZURE_TENANT_ID` | 예 | Entra ID tenant ID. |
| `AZURE_CLIENT_ID` | 예 | 앱 등록(클라이언트) ID. |
| `AZURE_CLIENT_SECRET` | 예 | 클라이언트 시크릿. |
| `AZURE_OPENAI_ENDPOINT` | 예 | Azure OpenAI 엔드포인트 (예: `https://xxx.cognitiveservices.azure.com/`). |
| `AZURE_OPENAI_MODEL` | 예 | 배포(모델) 이름 (예: `gpt-4o`). |
| `AZURE_OPENAI_API_VERSION` | 아니오 | 기본 `2024-08-01-preview`. |
| `RELAY_API_KEY` | 아니오 | 설정 시 호출 시 `X-API-Key` 또는 `Authorization: Bearer` 검증. |

Container Apps에서는 시크릿은 Secrets에 등록한 뒤 환경 변수에서 참조하도록 설정한다.

## Docker 빌드 및 로컬 실행

프로젝트 루트에서:

```bash
docker build -f infra/Dockerfile.relay -t llmtrader-relay .
docker run --rm -p 8000:8000 \
  -e AZURE_TENANT_ID=... \
  -e AZURE_CLIENT_ID=... \
  -e AZURE_CLIENT_SECRET=... \
  -e AZURE_OPENAI_ENDPOINT=https://xxx.cognitiveservices.azure.com/ \
  -e AZURE_OPENAI_MODEL=gpt-4o \
  llmtrader-relay
```

`http://localhost:8000/docs` 로 Swagger, `POST http://localhost:8000/generate` 로 전략 생성 테스트.

## Azure Container Apps 배포

1. **리소스 준비**: Resource Group, Azure Container Registry(ACR), Container Apps Environment.
2. **이미지 푸시**:
   ```bash
   az acr login --name <acr-name>
   docker tag llmtrader-relay <acr-name>.azurecr.io/llmtrader-relay:latest
   docker push <acr-name>.azurecr.io/llmtrader-relay:latest
   ```
3. **Container App 생성/업데이트**: 이미지 `acr.io/llmtrader-relay:latest`, Ingress HTTPS 외부, 포트 8000. 환경 변수에 위 표의 값 주입 (시크릿은 Container App Secrets로 등록 후 참조).
4. **클라이언트 설정**: 배포된 Container App FQDN을 `RELAY_SERVER_URL=https://<fqdn>` 으로 설정 (예: SaaS 백엔드 또는 로컬 `.env`).

## 클라이언트 호환

[src/llm/client.py](../../src/llm/client.py)의 `LLMClient`는 `RELAY_SERVER_URL`(또는 `settings.relay_server.url`)로 프록시 URL을 읽고, `GET /docs`로 헬스, `POST /generate`로 전략 생성 요청을 보낸다. 위 API 스펙과 동일하면 별도 수정 없이 동작한다.
