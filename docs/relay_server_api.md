# 중계 서버 API 스펙

## 서버 정보

- **기본 URL**: `http://192.168.219.122:8000`
- **API 문서**: `http://192.168.219.122:8000/docs`
- **OpenAPI 스키마**: `http://192.168.219.122:8000/openapi.json`
- **인증**: Entra ID (Azure OpenAI)

## 개요

중계 서버는 로컬 PC에서 Azure OpenAI에 접근할 수 없을 때, 중계 서버를 통해 Azure OpenAI API를 호출하는 역할을 합니다.

## 주요 엔드포인트

### POST /generate

전략 코드 생성을 위한 엔드포인트.

#### 요청 형식

**Content-Type**: `application/json`

```json
{
  "user_prompt": "RSI가 30 아래로 떨어지면 매수하고, 70 위로 올라가면 매도하는 전략을 만들어줘"
}
```

**필드 설명**:
- `user_prompt` (string, required): 사용자의 자연어 전략 설명

#### 응답 형식

**200 OK**

```json
{
  "code": "class MyRsiStrategy(Strategy):\n    ...",
  "model_used": "gpt-4o"
}
```

**422 Validation Error**

```json
{
  "detail": [
    {
      "loc": ["body", "user_prompt"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

#### 사용 예시

```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://192.168.219.122:8000/generate",
        json={
            "user_prompt": "RSI 기반 롱/숏 전략을 만들어줘"
        }
    )
    data = response.json()
    code = data["code"]
```

## 연결 테스트

연결 테스트는 다음 스크립트를 사용할 수 있습니다:

```bash
uv run python scripts/test_relay_server.py
```

## 참고사항

- 서버가 아직 완전히 구현되지 않았을 수 있습니다.
- 실제 API 스펙은 `/docs` 엔드포인트에서 확인할 수 있습니다.
- Entra ID 인증은 서버 측에서 처리됩니다 (클라이언트는 API 키 없이 호출).
