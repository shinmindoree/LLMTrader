# AGENTS.md — Binance API Client

## Module Context

Handles all communication with Binance Futures REST API.

**Files:**
- `client.py` — BinanceHTTPClient: signed/unsigned requests, order management
- `protocols.py` — Protocol definitions for type hinting

**API Base URLs:**
- Testnet: `https://testnet.binancefuture.com`
- Mainnet: `https://fapi.binance.com`

---

## Tech Stack & Constraints

- **httpx:** Async HTTP client with connection pooling.
- **HMAC-SHA256:** Signature required for all authenticated endpoints.
- **Timestamp:** Include `timestamp` param; use `recvWindow=5000` for clock drift.

---

## Implementation Patterns

### HMAC Signature Generation

```python
import hmac
import hashlib
from urllib.parse import urlencode

def _sign(params: dict, secret: str) -> str:
    query = urlencode(params)
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
```

### Boolean Parameter Normalization

Binance expects lowercase `"true"`/`"false"`, not Python `True`/`False`:

```python
def _normalize_params(params: dict) -> dict:
    return {
        k: ("true" if v is True else "false" if v is False else v)
        for k, v in params.items()
        if v is not None
    }
```

### Quantity as String

Always send `quantity` as string to avoid float precision issues:

```python
async def place_order(self, ..., quantity: float, ...):
    params["quantity"] = str(quantity)  # Critical for precision
```

### Error Response Extraction

Include full JSON body in exceptions for debugging:

```python
if response.status_code >= 400:
    body = response.text
    raise HTTPStatusError(f"{response.status_code}: {body}", ...)
```

---

## Key Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/fapi/v1/ticker/price` | GET | No | Real-time price |
| `/fapi/v1/klines` | GET | No | Candlestick data |
| `/fapi/v1/order` | POST | Yes | Place order |
| `/fapi/v1/order` | DELETE | Yes | Cancel order |
| `/fapi/v2/account` | GET | Yes | Account info (balance, positions) |
| `/fapi/v1/leverage` | POST | Yes | Set leverage |
| `/fapi/v1/positionRisk` | GET | Yes | Current positions |

---

## Local Golden Rules

### Do's

- Filter `None` values from params before signing.
- Use `fetch_ticker_price()` for real-time price, not `klines[-1][4]`.
- Log `api_key` length (not value) for debugging key loading issues.

### Don'ts

- Do not include `reduceOnly=` (empty string) in signed requests—filter it out.
- Do not use `urllib.parse.urlencode` on raw Python booleans—normalize first.
- Do not trust `avgPrice` from order response immediately (may be `0.00` for NEW status).

