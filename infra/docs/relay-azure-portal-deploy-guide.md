# Relay Azure Container Apps 배포 가이드 (Portal + 맥북)

회사 Azure에 프록시(relay)를 새로 올리고, 맥북에서 해당 프록시로 개발할 때 참고하는 단계별 가이드입니다.

## 전제 조건

- **로컬 개발 환경**: 맥북 (Apple Silicon M1/M2/M3 또는 Intel).
- **제약**: 맥북에서는 `az login` 등 회사 Azure 계정 CLI 로그인이 불가 (회사 정책).
- **가정**: Azure Portal은 **회사 랩탑** 또는 **회사 PC + VPN** 등으로 접속 가능.

따라서 **리소스 생성·설정은 Portal(회사 PC)** 에서 하고, **이미지 빌드·푸시는 맥북**에서 ACR 사용자명/비밀번호만으로 수행합니다.

---

## 작업 역할 분리

| 작업 | 수행 위치 | 비고 |
|------|------------|------|
| 리소스 그룹, ACR, Container Apps 환경, Container App 생성 | **Portal** (회사 PC) | `az login` 불필요 |
| ACR 로그인 서버·사용자명·비밀번호 확인 | **Portal** (회사 PC) | 액세스 키에서 복사 |
| Docker 이미지 빌드 | **맥북** | `--platform linux/amd64` 필수 |
| Docker 이미지 푸시 | **맥북** | `docker login` (ACR 사용자/비밀번호만 사용, `az` 미사용) |
| 환경 변수·시크릿 설정 | **Portal** (회사 PC) | Container App 설정 |
| RELAY_SERVER_URL 설정 | **맥북** `.env` | 배포 후 FQDN 입력 |

---

## 1단계: 리소스 그룹 생성 (Portal, 회사 PC)

1. 브라우저에서 https://portal.azure.com 접속 (회사 계정으로 로그인).
2. 상단 검색창에 **리소스 그룹** 입력 후 선택.
3. **+ 만들기** 클릭.
4. 입력:
   - **구독**: 사용할 구독 선택.
   - **리소스 그룹 이름**: 예) `rg-llmtrader-relay`.
   - **지역**: 예) `한국 중부` (회사 규정에 맞게 선택).
5. **검토 + 만들기** → **만들기**.
6. 만들어진 리소스 그룹 이름과 지역을 메모 (이후 단계에서 선택).

---

## 2단계: Azure Container Registry 생성 (Portal, 회사 PC)

1. Portal 상단 검색창에 **Container Registry** 입력 후 선택.
2. **+ 만들기** 클릭.
3. **기본 사항** 탭:
   - **구독**: 1단계와 동일.
   - **리소스 그룹**: 1단계에서 만든 그룹 (예: `rg-llmtrader-relay`).
   - **레지스트리 이름**: 전역에서 유일해야 함. 예) `llmtraderrelayacr` (소문자·숫자만, 하이픈 가능).
   - **위치**: 1단계와 동일 지역.
   - **SKU**: 개발/테스트는 **Basic**, 운영은 **Standard**.
4. **검토 + 만들기** → **만들기**.
5. 배포 완료 후 **리소스로 이동**.

### 2-1. ACR 액세스 키 확인 (맥북에서 푸시할 때 사용)

1. 해당 Container Registry 리소스에서 왼쪽 메뉴 **설정** → **액세스 키**.
2. **관리 사용자**를 **사용**으로 변경 후 **저장**.
3. 아래 세 값을 메모 (맥북에서 `docker login` 시 사용):
   - **로그인 서버**: 예) `llmtraderrelayacr.azurecr.io`
   - **사용자 이름**: 레지스트리 이름과 동일 (예: `llmtraderrelayacr`).
   - **비밀번호**: **비밀번호 1** 또는 **비밀번호 2** 중 하나 (복사해 안전한 곳에 보관).

---

## 3단계: 이미지 빌드 및 ACR 푸시 (맥북)

맥북에서는 **Azure CLI(`az login`)를 사용하지 않고**, ACR 사용자명/비밀번호만으로 로그인합니다.  
또한 **Apple Silicon 맥**에서는 Azure가 사용하는 **linux/amd64** 이미지를 빌드해야 합니다.

### 3-1. 프로젝트 루트에서 이미지 빌드 (플랫폼 지정)

```bash
cd /path/to/LLMTrader

# Apple Silicon 맥: 반드시 linux/amd64 로 빌드 (Azure Container Apps는 amd64)
docker build --platform linux/amd64 -f infra/Dockerfile.relay -t llmtrader-relay .
```

- **Intel 맥**에서도 Azure에 올릴 이미지는 `--platform linux/amd64` 를 넣어 두는 것을 권장.

### 3-2. ACR 주소로 태그

2단계에서 메모한 **로그인 서버**를 사용합니다.

```bash
docker tag llmtrader-relay <로그인서버>/llmtrader-relay:latest
```

예시:

```bash
docker tag llmtrader-relay llmtraderrelayacr.azurecr.io/llmtrader-relay:latest
```

### 3-3. ACR 로그인 (사용자명/비밀번호만 사용, az 미사용)

```bash
docker login <로그인서버> -u <사용자이름> -p <비밀번호>
```

예시:

```bash
docker login llmtraderrelayacr.azurecr.io -u llmtraderrelayacr -p <2단계에서_복사한_비밀번호>
```

### 3-4. 이미지 푸시

```bash
docker push <로그인서버>/llmtrader-relay:latest
```

예시:

```bash
docker push llmtraderrelayacr.azurecr.io/llmtrader-relay:latest
```

### 3-5. 푸시 결과 확인 (선택, Portal에서)

회사 PC에서 Portal → 해당 **Container Registry** → **서비스** → **리포지토리**에서 `llmtrader-relay` 리포지토리와 `latest` 태그가 보이면 성공.

---

## 4단계: Container Apps 환경 생성 (Portal, 회사 PC)

1. Portal 상단 검색창에 **Container Apps 환경** 입력 후 선택.
2. **+ 만들기** 클릭.
3. **기본 사항** 탭:
   - **구독**: 1단계와 동일.
   - **리소스 그룹**: 1단계에서 만든 그룹.
   - **환경 이름**: 예) `cae-llmtrader-relay`.
   - **지역**: 1단계와 동일.
   - **영역 중복**: 개발용이면 **사용 안 함**.
4. **모니터링** 탭:
   - **Log Analytics 작업 영역**: **사용**.
   - **새 Log Analytics 작업 영역 만들기** 선택 후, 작업 영역 이름 입력 (예: `law-llmtrader-relay`).
5. **검토 + 만들기** → **만들기**.
6. 배포 완료 후 **리소스로 이동**. 환경 이름을 메모.

---

## 5단계: Container App(relay) 생성 (Portal, 회사 PC)

1. **Container Apps 환경** 리소스(`cae-llmtrader-relay`)로 들어간 상태에서 상단 **+ Container App 만들기** 클릭.  
   또는 Portal 검색창에 **Container Apps** 입력 → **+ 만들기** 후, **Container Apps 환경**에서 4단계에서 만든 환경을 선택해도 됨.
2. **기본 사항** 탭:
   - **Container App 이름**: 예) `relay` (최종 FQDN은 `https://relay.<환경도메인>.azurecontainerapps.io` 형태).
   - **구독** / **리소스 그룹**: 1단계와 동일.
   - **지역**: 4단계 환경과 동일.
   - **Container Apps 환경**: 4단계에서 만든 환경 선택.
3. **컨테이너** 탭:
   - **이미지 원본**: **Azure Container Registry**.
   - **레지스트리**: 2단계에서 만든 ACR 선택.
   - **이미지**: `llmtrader-relay`.
   - **태그**: `latest`.
   - **CPU 및 메모리**: 예) 0.5 CPU, 1 Gi 메모리.
   - **수평 확장**:
     - **Min replicas**: 0 (비용 절감) 또는 1.
     - **Max replicas**: 예) 3.
4. **수신** 탭:
   - **수신 사용**: **사용**.
   - **수신 트래픽**: **HTTP와 HTTPS에서 수신** 권장.
   - **외부 수신**: **사용** (외부에서 접근 가능).
   - **대상 포트**: **8000** (relay 앱이 8000 포트에서 대기).
   - **전송**: **HTTP**.
5. **검토 + 만들기** → **만들기**.
6. 배포 완료 후 **리소스로 이동**.

---

## 6단계: 환경 변수 및 시크릿 설정 (Portal, 회사 PC)

1. 5단계에서 만든 **Container App(relay)** 리소스로 이동.
2. 왼쪽 메뉴 **설정** → **환경 변수**.
3. **+ 추가** → **일반 변수**로 아래 추가 (노출되어도 되는 값):
   - `AZURE_OPENAI_ENDPOINT` = Azure OpenAI 엔드포인트 (예: `https://xxx.cognitiveservices.azure.com/`).
   - `AZURE_OPENAI_MODEL` = 배포(모델) 이름 (예: `gpt-4o`, `gpt-5.2-chat` 등).
   - (선택) `AZURE_OPENAI_API_VERSION` = `2024-08-01-preview`.
4. **권장 방식(Managed Identity)**:
   - Container App의 **ID(Identity)** 메뉴에서 **System assigned**를 켭니다.
   - 해당 Managed Identity에 Azure OpenAI 리소스 접근 권한(필요 Role)을 부여합니다.
   - `AZURE_CLIENT_SECRET`는 설정하지 않습니다.
5. (선택) User-assigned Managed Identity를 쓰는 경우에만:
   - `AZURE_CLIENT_ID` 환경 변수에 User-assigned MI의 Client ID를 설정합니다.
6. (예외) 클라이언트 시크릿 인증이 꼭 필요할 때만 시크릿 참조를 사용합니다:
   - `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
7. (선택) `RELAY_API_KEY` 사용 시: 시크릿 추가 후 환경 변수에서 해당 시크릿 참조.
8. **저장** 또는 **적용** 클릭.
9. 필요 시 **개정 관리**에서 **새 개정 만들기**를 실행해 변경 사항을 반영.

---

## 7단계: FQDN 확인 및 맥북 .env 설정

1. **Container App(relay)** 리소스에서 **개요** 또는 **앱 URL** 항목 확인.
2. 표시된 URL이 FQDN (예: `https://relay.xxxxx.azurecontainerapps.io`). 이 값을 복사.
3. **맥북**에서 프로젝트 루트의 `.env` 파일을 열고 다음 한 줄을 추가 또는 수정:

```env
RELAY_SERVER_URL=https://relay.xxxxx.azurecontainerapps.io
```

4. 브라우저에서 `https://<FQDN>/docs` 로 접속해 Swagger UI가 뜨는지 확인.
5. `POST /generate` 로 한 번 호출해 Azure OpenAI까지 정상 동작하는지 확인.

---

## 8단계: 이미지 업데이트(재배포) 절차

코드 변경 후 relay 이미지를 다시 배포할 때:

1. **맥북**에서:
   ```bash
   cd /path/to/LLMTrader
   docker build --platform linux/amd64 -f infra/Dockerfile.relay -t llmtrader-relay .
   docker tag llmtrader-relay <로그인서버>/llmtrader-relay:latest
   docker login <로그인서버> -u <사용자이름> -p <비밀번호>
   docker push <로그인서버>/llmtrader-relay:latest
   ```
2. **Portal** (회사 PC): Container App(relay) → **개정 관리** → **새 개정 만들기** (이미지는 `latest` 그대로 두면 새로 푸시한 이미지로 풀됨). 또는 **다시 시작**으로 최신 이미지를 당겨오게 할 수 있음 (환경에 따라 다름).

---

## 체크리스트 요약

| 순서 | 작업 | 위치 | 비고 |
|------|------|------|------|
| 1 | 리소스 그룹 생성 | Portal | |
| 2 | Container Registry 생성, 관리 사용자 사용, 로그인 서버/사용자/비밀번호 메모 | Portal | |
| 3 | `docker build --platform linux/amd64 ...`, tag, docker login (ACR 비밀번호), push | 맥북 | `az login` 미사용 |
| 4 | Container Apps 환경 생성 | Portal | Log Analytics 포함 |
| 5 | Container App(relay) 생성, 이미지·포트 8000·수신 사용 | Portal | |
| 6 | 환경 변수·시크릿 설정 (Azure OpenAI, Entra ID) | Portal | |
| 7 | FQDN 확인, 맥북 `.env` 에 `RELAY_SERVER_URL` 설정 | 맥북 | |

---

## 참고 문서

- Relay 앱 역할·API·환경 변수: [relay-container-apps.md](relay-container-apps.md)
- SaaS에서 프록시 접근 방식: [saas-proxy-access.md](saas-proxy-access.md)
