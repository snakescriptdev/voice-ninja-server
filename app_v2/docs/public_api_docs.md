# Public API Analytics & Logs

Developers can monitor their public API usage and review detailed call logs through these specialized endpoints.

Both endpoints require standard API Key Authentication using `X-API-Client-ID` and `X-API-Client-Secret` headers.

---

## 1. Get API Analytics

Returns a high-level summary of your API usage for the current billing month and performance metrics over the last 24 hours.

**Endpoint:** `GET /api/v2/public/analytics`

### Request Headers
```http
X-API-Client-ID: <your_client_id>
X-API-Client-Secret: <your_client_secret>
```

### Response Example
```json
{
  "total_api_calls_this_month": 12450,
  "coins_used_via_api_this_month": 3400,
  "avg_response_time_24h_ms": 112.5
}
```

- **`total_api_calls_this_month`**: The absolute number of times your API key was used successfully or unsuccessfully this month.
- **`coins_used_via_api_this_month`**: Total coins automatically deducted for actions performed via the public API (e.g., initiating WebSockets).
- **`avg_response_time_24h_ms`**: Average time the server took to process your requests in the last rolling 24-hour window, in milliseconds.

---

## 2. Get API Call Logs

Provides a paginated list of your recent API requests containing route access, status codes, response times, and coin deductions.

**Endpoint:** `GET /api/v2/public/logs`

### Query Parameters
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | Integer | 1 | The page number to retrieve. |
| `size` | Integer | 20 | The number of logs per page. Maximum is generally capped securely by the server. |

### Request Headers
```http
X-API-Client-ID: <your_client_id>
X-API-Client-Secret: <your_client_secret>
```

### Response Example
```json
{
  "total": 12450,
  "page": 1,
  "size": 20,
  "pages": 623,
  "items": [
    {
      "id": 85934,
      "api_route": "/api/v2/public/agents",
      "status_code": 200,
      "response_time_ms": 45,
      "coins_used": 0,
      "created_at": "2023-11-09T10:25:39.123Z"
    },
    {
      "id": 85933,
      "api_route": "/api/v2/public/ws/101",
      "status_code": 101,
      "response_time_ms": 12500,
      "coins_used": 35,
      "created_at": "2023-11-09T10:20:15.890Z"
    }
  ]
}
```

### Understanding the Logs
- **`status_code`**: Standard HTTP status codes (2xx for success, 4xx/5xx for errors). For Websockets initial connections, this may reflect the switching protocols status (`101`) or a `500`/`424` if the relay failed.
- **`response_time_ms`**: Time in milliseconds for the server to yield a response or to close a continuous connection.
- **`coins_used`**: The specific amount of coins deducted for that exact request lifecycle. Most standard REST endpoints cost 0 coins.
