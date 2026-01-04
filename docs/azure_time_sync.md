# Azure Container 환경 시간 동기화 가이드

## 문제 상황
Binance API 호출 시 `Timestamp for this request is outside of the recvWindow` 오류가 발생할 수 있습니다.
이는 컨테이너와 Binance 서버 간의 시간 동기화 문제로 인해 발생합니다.

## 해결 방법

### 1. Dockerfile에 시간대 설정 추가 (이미 적용됨)
```dockerfile
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
```

### 2. Azure Container에서 시간 확인 방법

#### 컨테이너 내부에서 시간 확인
```bash
# 컨테이너에 접속
az container exec --resource-group <리소스그룹명> --name <컨테이너명> --exec-command "/bin/bash"

# 또는 Docker를 통해 직접 접속
docker exec -it <컨테이너ID> /bin/bash

# 시간 확인
date
date -u  # UTC 시간
timedatectl  # 시스템 시간 설정 확인 (일부 이미지에서는 사용 불가)
```

#### Python 스크립트로 시간 확인
```python
import time
from datetime import datetime

# 로컬 시간
print(f"Local time: {datetime.now()}")
print(f"Local timestamp (ms): {int(time.time() * 1000)}")

# UTC 시간
print(f"UTC time: {datetime.utcnow()}")
print(f"UTC timestamp (ms): {int(time.time() * 1000)}")
```

### 3. Binance 서버 시간과 비교

#### Binance 서버 시간 조회 스크립트
```python
import asyncio
import httpx
from datetime import datetime

async def check_binance_time():
    async with httpx.AsyncClient(base_url="https://testnet.binancefuture.com") as client:
        response = await client.get("/fapi/v1/time")
        data = response.json()
        server_time_ms = data["serverTime"]
        server_time = datetime.fromtimestamp(server_time_ms / 1000)
        
        local_time_ms = int(time.time() * 1000)
        local_time = datetime.fromtimestamp(local_time_ms / 1000)
        
        diff_ms = abs(server_time_ms - local_time_ms)
        
        print(f"Binance 서버 시간: {server_time} ({server_time_ms})")
        print(f"로컬 시간: {local_time} ({local_time_ms})")
        print(f"시간 차이: {diff_ms}ms ({diff_ms/1000:.2f}초)")
        
        if diff_ms > 5000:
            print("⚠️ 경고: 시간 차이가 5초 이상입니다!")

asyncio.run(check_binance_time())
```

### 4. Azure Container Instances 시간 동기화 확인

#### Azure CLI로 확인
```bash
# 컨테이너 로그에서 시간 관련 정보 확인
az container logs --resource-group <리소스그룹명> --name <컨테이너명>

# 컨테이너 실행 명령으로 시간 확인
az container exec --resource-group <리소스그룹명> --name <컨테이너명> --exec-command "date -u"
```

#### Azure Portal에서 확인
1. Azure Portal → Container Instances
2. 해당 컨테이너 선택
3. "Logs" 탭에서 시간 관련 로그 확인
4. "Console" 탭에서 직접 명령 실행 가능

### 5. 시간 동기화 문제 해결 방법

#### 방법 1: NTP 동기화 (권장)
Dockerfile에 NTP 클라이언트 설치 및 동기화 추가:
```dockerfile
RUN apt-get update && apt-get install -y ntpdate && \
    ntpdate -s time.nist.gov && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
```

#### 방법 2: recvWindow 증가 (이미 적용됨)
`client.py`의 `_attach_signature` 메서드에서 `recvWindow=60000` (60초) 설정

#### 방법 3: Binance 서버 시간 사용
서명 전에 Binance 서버 시간을 조회하여 사용:
```python
async def _attach_signature_with_server_time(self, params: dict[str, Any]) -> dict[str, Any]:
    # Binance 서버 시간 조회
    server_time_data = await self.fetch_server_time()
    server_timestamp = server_time_data["serverTime"]
    
    params = self._normalize_params(dict(params))
    params["timestamp"] = server_timestamp
    params.setdefault("recvWindow", 60000)
    # ... 나머지 서명 로직
```

### 6. 모니터링 스크립트

컨테이너 시작 시 시간 동기화 상태를 확인하는 스크립트:

```python
# scripts/check_time_sync.py
import asyncio
import time
import httpx
from datetime import datetime

async def check_time_sync():
    """Binance 서버 시간과 로컬 시간 동기화 상태 확인"""
    try:
        async with httpx.AsyncClient(
            base_url="https://testnet.binancefuture.com",
            timeout=10.0
        ) as client:
            response = await client.get("/fapi/v1/time")
            response.raise_for_status()
            data = response.json()
            
            server_time_ms = data["serverTime"]
            local_time_ms = int(time.time() * 1000)
            diff_ms = abs(server_time_ms - local_time_ms)
            
            server_time = datetime.fromtimestamp(server_time_ms / 1000)
            local_time = datetime.fromtimestamp(local_time_ms / 1000)
            
            print(f"✅ Binance 서버 시간: {server_time} ({server_time_ms})")
            print(f"✅ 로컬 시간: {local_time} ({local_time_ms})")
            print(f"✅ 시간 차이: {diff_ms}ms ({diff_ms/1000:.2f}초)")
            
            if diff_ms > 10000:
                print(f"⚠️ 경고: 시간 차이가 10초 이상입니다! (차이: {diff_ms}ms)")
                return False
            elif diff_ms > 5000:
                print(f"⚠️ 주의: 시간 차이가 5초 이상입니다. (차이: {diff_ms}ms)")
                return True
            else:
                print("✅ 시간 동기화 상태 양호")
                return True
                
    except Exception as e:
        print(f"❌ 시간 동기화 확인 실패: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(check_time_sync())
    exit(0 if success else 1)
```

### 7. 실행 전 체크리스트

- [ ] Dockerfile에 시간대 설정이 포함되어 있는지 확인
- [ ] 컨테이너 빌드 시 시간대가 올바르게 설정되는지 확인
- [ ] Binance 서버 시간과 로컬 시간 차이가 5초 이내인지 확인
- [ ] `recvWindow=60000` 설정이 적용되어 있는지 확인
- [ ] 정기적으로 시간 동기화 상태를 모니터링

### 8. 추가 참고사항

- Azure Container Instances는 호스트 시스템의 시간을 사용합니다
- 컨테이너 내부의 시간은 호스트와 동기화되어 있습니다
- NTP 동기화는 Azure 인프라에서 자동으로 처리됩니다
- 문제가 지속되면 Azure 지원팀에 문의하세요

