# FDPO Test Azure Rebuild Runbook (Azure CLI Only)

이 문서는 Azure 리소스가 0개인 상태에서, Azure CLI만으로 재구축하는 절차입니다.

## 0) 원칙

1. 기존 유출 App/SP는 재사용하지 않습니다.
2. CI/CD 인증은 Client Secret 없이 OIDC로 구성합니다.
3. 앱 런타임 인증은 Managed Identity 우선으로 구성합니다.
4. 비밀값은 Key Vault에만 저장합니다.

## 1) 사전 준비

```bash
az version
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.CognitiveServices
```

## 2) 로그인 상태 확인 (Cloud Shell 기준)

Cloud Shell에서는 이미 로그인된 경우가 많습니다. 아래를 먼저 실행해서 현재 계정/구독을 확인합니다.

```bash
az account show --query "{user:user.name, subscription:id, tenant:tenantId}" -o table
```

현재 컨텍스트가 없거나 다른 계정으로 바꿔야 할 때만 `az login`을 실행하세요.

```bash
az login
```

## 3) 복붙용 변수 자동 설정

아래 블록은 Cloud Shell에 그대로 복붙해서 실행할 수 있습니다.

```bash
# 3-1) 구독 선택 (필요 시 번호를 바꿔 선택)
az account list --query "[].{name:name,id:id,isDefault:isDefault,tenantId:tenantId}" -o table
# 예: 원하는 구독으로 변경
# az account set --subscription "<subscription-id>"

# 3-2) 현재 컨텍스트에서 subscription/tenant 자동 추출
export AZ_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
export AZ_TENANT_ID="$(az account show --query tenantId -o tsv)"

# 3-3) 사용자가 정해야 하는 값
export AZ_LOCATION="koreacentral"
export AZ_PREFIX="fdpo-test-dev"

# 3-4) 파생 이름 자동 생성
export AZ_RG="${AZ_PREFIX}-rg"
export AZ_LAW="${AZ_PREFIX}-law"
export AZ_KV="${AZ_PREFIX}-kv"
export AZ_CAE="${AZ_PREFIX}-cae"
export AZ_ACR="${AZ_PREFIX//-/}acr"  # ACR 이름은 소문자/숫자만 가능

export AZ_CA_API="${AZ_PREFIX}-api"
export AZ_CA_RUNNER="${AZ_PREFIX}-runner"
export AZ_CA_RELAY="${AZ_PREFIX}-relay"

export AZ_OAI_ACCOUNT="${AZ_PREFIX//-/}oai"
export AZ_OAI_DEPLOYMENT="gpt-5-2-chat"
export AZ_OAI_MODEL_NAME="gpt-5.2-chat"
export AZ_OAI_MODEL_VERSION="latest"
export AZ_OAI_DEPLOYMENT_SKU="GlobalStandard"

export AZ_OIDC_APP_NAME="${AZ_PREFIX}-gh-oidc"

# 3-5) GitHub 정보(직접 입력 또는 git remote에서 추출)
# 직접 입력:
export GH_OWNER="<github-org-or-user>"
export GH_REPO="<github-repo>"

# (선택) 현재 폴더가 git repo라면 자동 추출:
# REMOTE_URL="$(git remote get-url origin)"
# export GH_OWNER="$(echo "${REMOTE_URL}" | sed -E 's#.*[:/]([^/]+)/([^/.]+)(\.git)?$#\1#')"
# export GH_REPO="$(echo "${REMOTE_URL}" | sed -E 's#.*[:/]([^/]+)/([^/.]+)(\.git)?$#\2#')"

echo "AZ_SUBSCRIPTION_ID=${AZ_SUBSCRIPTION_ID}"
echo "AZ_TENANT_ID=${AZ_TENANT_ID}"
echo "GH_OWNER=${GH_OWNER}"
echo "GH_REPO=${GH_REPO}"
```

## 4) 입력값 확인 포인트

필요한 정보와 확인 위치는 아래와 같습니다.

1. `AZ_SUBSCRIPTION_ID`, `AZ_TENANT_ID`:
   - 확인 명령: `az account show -o table`
2. `AZ_LOCATION`:
   - 확인 명령: `az account list-locations --query "[].name" -o tsv | sort`
3. `GH_OWNER`, `GH_REPO`:
   - GitHub 저장소 URL (`https://github.com/<owner>/<repo>`)에서 확인
   - 또는 `git remote get-url origin`
4. `AZ_PREFIX`:
   - 직접 결정(예: `fdpo-test-dev`), 이후 리소스명은 자동 생성

```bash
az account show --query "{name:name,id:id,tenantId:tenantId}" -o table
```

```bash
# 필수 입력값 검증 (placeholder 상태면 중단)
if [ -z "${GH_OWNER}" ] || [ "${GH_OWNER}" = "<github-org-or-user>" ]; then
  echo "ERROR: GH_OWNER 값을 실제 GitHub owner로 설정하세요."
  exit 1
fi
if [ -z "${GH_REPO}" ] || [ "${GH_REPO}" = "<github-repo>" ]; then
  echo "ERROR: GH_REPO 값을 실제 GitHub repo로 설정하세요."
  exit 1
fi
```

## 5) 사고 대상 기존 App/SP 폐기 (메일 기준)

메일의 정보:
- ServicePrincipalID: `1447d291-522a-4b5a-88c9-21aaf29e0a86`
- ApplicationID: `783dbf00-089f-4be6-a68f-6b9ba969efd0`

```bash
az ad sp delete --id "1447d291-522a-4b5a-88c9-21aaf29e0a86" || true
az ad app delete --id "783dbf00-089f-4be6-a68f-6b9ba969efd0" || true
```

## 6) 기본 리소스 생성

```bash
az group create \
  --name "${AZ_RG}" \
  --location "${AZ_LOCATION}"

az monitor log-analytics workspace create \
  --resource-group "${AZ_RG}" \
  --workspace-name "${AZ_LAW}" \
  --location "${AZ_LOCATION}"

az keyvault create \
  --name "${AZ_KV}" \
  --resource-group "${AZ_RG}" \
  --location "${AZ_LOCATION}" \
  --enable-rbac-authorization true \
  --retention-days 90 \
  --enable-purge-protection true

az acr create \
  --name "${AZ_ACR}" \
  --resource-group "${AZ_RG}" \
  --location "${AZ_LOCATION}" \
  --sku Basic \
  --admin-enabled false
```

## 7) Container Apps 환경 생성

```bash
export LAW_ID="$(az monitor log-analytics workspace show -g "${AZ_RG}" -n "${AZ_LAW}" --query customerId -o tsv)"
export LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "${AZ_RG}" -n "${AZ_LAW}" --query primarySharedKey -o tsv)"

az containerapp env create \
  --name "${AZ_CAE}" \
  --resource-group "${AZ_RG}" \
  --location "${AZ_LOCATION}" \
  --logs-workspace-id "${LAW_ID}" \
  --logs-workspace-key "${LAW_KEY}"
```

## 8) Container App 3종 생성 (System Assigned MI)

```bash
# API: 외부 진입 필요, 실제 서비스 포트 8000
az containerapp create \
  --name "${AZ_CA_API}" \
  --resource-group "${AZ_RG}" \
  --environment "${AZ_CAE}" \
  --image "mcr.microsoft.com/k8se/quickstart:latest" \
  --target-port 8000 \
  --ingress external \
  --system-assigned

# RUNNER: 워커 프로세스이므로 ingress 비활성화
az containerapp create \
  --name "${AZ_CA_RUNNER}" \
  --resource-group "${AZ_RG}" \
  --environment "${AZ_CAE}" \
  --image "mcr.microsoft.com/k8se/quickstart:latest" \
  --system-assigned

# RELAY: API에서만 호출하므로 internal ingress, 실제 서비스 포트 8000
az containerapp create \
  --name "${AZ_CA_RELAY}" \
  --resource-group "${AZ_RG}" \
  --environment "${AZ_CAE}" \
  --image "mcr.microsoft.com/k8se/quickstart:latest" \
  --target-port 8000 \
  --ingress internal \
  --system-assigned
```

## 9) 앱 런타임에 ACR Pull 권한 부여

```bash
export ACR_ID="$(az acr show -g "${AZ_RG}" -n "${AZ_ACR}" --query id -o tsv)"

for APP in "${AZ_CA_API}" "${AZ_CA_RUNNER}" "${AZ_CA_RELAY}"; do
  PRINCIPAL_ID="$(az containerapp show -g "${AZ_RG}" -n "${APP}" --query identity.principalId -o tsv)"

  az role assignment create \
    --assignee-object-id "${PRINCIPAL_ID}" \
    --assignee-principal-type ServicePrincipal \
    --role "AcrPull" \
    --scope "${ACR_ID}"

  az containerapp registry set \
    --name "${APP}" \
    --resource-group "${AZ_RG}" \
    --server "${AZ_ACR}.azurecr.io" \
    --identity system
done
```

## 10) Azure OpenAI 리소스 생성 및 Relay MI 권한 부여

참고: 모델/리전/배포 SKU 가용성은 구독 정책에 따라 달라집니다.
`gpt-5.2-chat`은 `Standard`가 아닌 `GlobalStandard`만 허용되는 경우가 많습니다.

```bash
az cognitiveservices account create \
  --name "${AZ_OAI_ACCOUNT}" \
  --resource-group "${AZ_RG}" \
  --location "${AZ_LOCATION}" \
  --kind OpenAI \
  --sku S0 \
  --custom-domain "${AZ_OAI_ACCOUNT}"

az cognitiveservices account deployment create \
  --name "${AZ_OAI_ACCOUNT}" \
  --resource-group "${AZ_RG}" \
  --deployment-name "${AZ_OAI_DEPLOYMENT}" \
  --model-name "${AZ_OAI_MODEL_NAME}" \
  --model-version "${AZ_OAI_MODEL_VERSION}" \
  --model-format OpenAI \
  --sku-capacity 1 \
  --sku-name "${AZ_OAI_DEPLOYMENT_SKU}"
```

배포 실패 시(예: `SKU 'Standard' is not supported`) 아래 순서로 확인/재시도:

```bash
az cognitiveservices account list-models \
  --name "${AZ_OAI_ACCOUNT}" \
  --resource-group "${AZ_RG}" \
  --query "[?contains(name, 'gpt-5')].[name,version,format]" \
  -o table

az cognitiveservices account deployment create \
  --name "${AZ_OAI_ACCOUNT}" \
  --resource-group "${AZ_RG}" \
  --deployment-name "${AZ_OAI_DEPLOYMENT}" \
  --model-name "${AZ_OAI_MODEL_NAME}" \
  --model-version "${AZ_OAI_MODEL_VERSION}" \
  --model-format OpenAI \
  --sku-capacity 1 \
  --sku-name GlobalStandard
```

```bash
export OAI_ID="$(az cognitiveservices account show -g "${AZ_RG}" -n "${AZ_OAI_ACCOUNT}" --query id -o tsv)"
export RELAY_PRINCIPAL_ID="$(az containerapp show -g "${AZ_RG}" -n "${AZ_CA_RELAY}" --query identity.principalId -o tsv)"

az role assignment create \
  --assignee-object-id "${RELAY_PRINCIPAL_ID}" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" \
  --scope "${OAI_ID}"
```

## 11) GitHub Actions용 OIDC App/SP 생성 (Client Secret 없음)

```bash
export AZ_APP_CLIENT_ID="$(az ad app create --display-name "${AZ_OIDC_APP_NAME}" --query appId -o tsv)"
export AZ_APP_OBJECT_ID="$(az ad app show --id "${AZ_APP_CLIENT_ID}" --query id -o tsv)"

az ad sp create --id "${AZ_APP_CLIENT_ID}"
export AZ_SP_OBJECT_ID="$(az ad sp show --id "${AZ_APP_CLIENT_ID}" --query id -o tsv)"
```

```bash
az role assignment create \
  --assignee-object-id "${AZ_SP_OBJECT_ID}" \
  --assignee-principal-type ServicePrincipal \
  --role "Contributor" \
  --scope "/subscriptions/${AZ_SUBSCRIPTION_ID}/resourceGroups/${AZ_RG}"

az role assignment create \
  --assignee-object-id "${AZ_SP_OBJECT_ID}" \
  --assignee-principal-type ServicePrincipal \
  --role "AcrPush" \
  --scope "${ACR_ID}"
```

```bash
cat > federated-main.json <<EOF
{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:${GH_OWNER}/${GH_REPO}:ref:refs/heads/main",
  "description": "GitHub Actions main branch",
  "audiences": [
    "api://AzureADTokenExchange"
  ]
}
EOF

az ad app federated-credential create \
  --id "${AZ_APP_OBJECT_ID}" \
  --parameters @federated-main.json
```

## 12) GitHub Secrets/Variables 등록 (자동 권장)

### 12-1) 자동 등록 (`gh` CLI)

사전 조건:
- `gh` 설치
- `gh auth login` 완료
- `GH_OWNER`, `GH_REPO` 환경변수 설정 완료

```bash
gh --version
gh auth status
```

```bash
export GH_TARGET_REPO="${GH_OWNER}/${GH_REPO}"

# GitHub Secrets
gh secret set AZURE_CLIENT_ID_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_APP_CLIENT_ID}"
gh secret set AZURE_TENANT_ID_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_TENANT_ID}"
gh secret set AZURE_SUBSCRIPTION_ID_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_SUBSCRIPTION_ID}"

# GitHub Variables
gh variable set AZURE_RESOURCE_GROUP_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_RG}"
gh variable set AZURE_ACR_NAME_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_ACR}"
gh variable set AZURE_ACR_LOGIN_SERVER_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_ACR}.azurecr.io"
gh variable set AZURE_CONTAINER_APP_NAME_API_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_CA_API}"
gh variable set AZURE_CONTAINER_APP_NAME_RUNNER_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_CA_RUNNER}"
gh variable set AZURE_CONTAINER_APP_NAME_RELAY_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_CA_RELAY}"
gh variable set AZURE_CONTAINER_APP_NAME_MAIN_TEST --repo "${GH_TARGET_REPO}" --body "${AZ_CA_API}"
```

검증:

```bash
gh secret list --repo "${GH_TARGET_REPO}"
gh variable list --repo "${GH_TARGET_REPO}"
```

### 12-2) 수동 등록(필요 시)

```bash
echo "==== GitHub Secrets ===="
echo "AZURE_CLIENT_ID_TEST=${AZ_APP_CLIENT_ID}"
echo "AZURE_TENANT_ID_TEST=${AZ_TENANT_ID}"
echo "AZURE_SUBSCRIPTION_ID_TEST=${AZ_SUBSCRIPTION_ID}"

echo
echo "==== GitHub Variables ===="
echo "AZURE_RESOURCE_GROUP_TEST=${AZ_RG}"
echo "AZURE_ACR_NAME_TEST=${AZ_ACR}"
echo "AZURE_ACR_LOGIN_SERVER_TEST=${AZ_ACR}.azurecr.io"
echo "AZURE_CONTAINER_APP_NAME_API_TEST=${AZ_CA_API}"
echo "AZURE_CONTAINER_APP_NAME_RUNNER_TEST=${AZ_CA_RUNNER}"
echo "AZURE_CONTAINER_APP_NAME_RELAY_TEST=${AZ_CA_RELAY}"
echo "AZURE_CONTAINER_APP_NAME_MAIN_TEST=${AZ_CA_API}"
```

## 13) Key Vault 비밀값 저장 및 Container App 설정

Key Vault를 RBAC 모드로 생성했기 때문에, 먼저 현재 로그인 주체에 비밀 쓰기 권한을 부여해야 할 수 있습니다.

```bash
export KV_ID="$(az keyvault show -g "${AZ_RG}" -n "${AZ_KV}" --query id -o tsv)"
export CURRENT_USER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"

az role assignment create \
  --assignee-object-id "${CURRENT_USER_OBJECT_ID}" \
  --assignee-principal-type User \
  --role "Key Vault Secrets Officer" \
  --scope "${KV_ID}"

# RBAC 전파 대기 (보통 1~5분)
sleep 120
```

검증:

```bash
az role assignment list \
  --scope "${KV_ID}" \
  --assignee-object-id "${CURRENT_USER_OBJECT_ID}" \
  -o table
```

`ForbiddenByConnection` 에러가 나면 인증이 아니라 Key Vault 네트워크 정책 문제입니다.
Cloud Shell에서 바로 진행하려면 아래처럼 임시로 퍼블릭 접근을 열고 저장 후 다시 닫습니다.

```bash
az keyvault show -g "${AZ_RG}" -n "${AZ_KV}" \
  --query "properties.{publicNetworkAccess:publicNetworkAccess,defaultAction:networkAcls.defaultAction,bypass:networkAcls.bypass}" \
  -o table

# 임시 오픈 (저장 작업 용도)
az keyvault update -g "${AZ_RG}" -n "${AZ_KV}" \
  --public-network-access Enabled \
  --default-action Allow
```

```bash
export AZ_OAI_ENDPOINT="$(az cognitiveservices account show -g "${AZ_RG}" -n "${AZ_OAI_ACCOUNT}" --query properties.endpoint -o tsv)"

az keyvault secret set --vault-name "${AZ_KV}" --name "azure-openai-endpoint" --value "${AZ_OAI_ENDPOINT}"
az keyvault secret set --vault-name "${AZ_KV}" --name "azure-openai-model" --value "${AZ_OAI_DEPLOYMENT}"
```

```bash
# 저장 후 원복 (보안 강화)
az keyvault update -g "${AZ_RG}" -n "${AZ_KV}" \
  --default-action Deny \
  --public-network-access Disabled
```

Relay는 Managed Identity 우선 동작이므로 `AZURE_CLIENT_SECRET`는 넣지 않습니다.

```bash
az containerapp secret set \
  --name "${AZ_CA_RELAY}" \
  --resource-group "${AZ_RG}" \
  --secrets \
  azure-openai-endpoint="${AZ_OAI_ENDPOINT}" \
  azure-openai-model="${AZ_OAI_DEPLOYMENT}"

az containerapp update \
  --name "${AZ_CA_RELAY}" \
  --resource-group "${AZ_RG}" \
  --set-env-vars \
  AZURE_OPENAI_ENDPOINT=secretref:azure-openai-endpoint \
  AZURE_OPENAI_MODEL=secretref:azure-openai-model
```

## 14) 검증

```bash
az resource list -g "${AZ_RG}" -o table
az containerapp show -g "${AZ_RG}" -n "${AZ_CA_RELAY}" --query "{fqdn:properties.configuration.ingress.fqdn,image:properties.template.containers[0].image}" -o table
az containerapp logs show -g "${AZ_RG}" -n "${AZ_CA_RELAY}" --follow false --tail 200
az role assignment list --scope "${ACR_ID}" -o table
az role assignment list --scope "${OAI_ID}" -o table
```

## 15) CDI 회신 체크리스트

1. 유출 App/SP 폐기 완료
2. 신규 CI/CD 인증을 OIDC로 구성 완료
3. 앱 런타임 인증을 Managed Identity로 전환 완료
4. 비밀값 Key Vault 저장 완료
5. Sign-in/Audit 로그 검토 완료
6. 배포 및 동작 검증 완료
