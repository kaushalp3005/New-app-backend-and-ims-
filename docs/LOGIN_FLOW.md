# Login + Punch-In Flow — Architecture & API Reference

## Overview

Login doubles as **punch-in**. A single POST authenticates the promoter, generates JWT tokens, creates an attendance record, and kicks off async reverse-geocoding via Kafka. The response returns **immediately** — geocoding happens in the background.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React Native / Web)                                         │
│                                                                         │
│  1. Collect email, password, GPS lat/lng                                │
│  2. JSON.stringify → AES-256-GCM encrypt → base64 encode               │
│  3. POST /api/1.1  body: { "payload": "<base64>" }                     │
│  4. Receive { "payload": "<base64>" } → base64 decode → AES decrypt    │
│  5. Parse JSON → access_token, refresh_token                           │
└────────────────────────────┬────────────────────────────────────────────┘
                             │  HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND                                                        │
│                                                                         │
│  ┌──────────────────────────────────┐                                   │
│  │  RouteObfuscationMiddleware      │                                   │
│  │  /api/1.1  →  /api/login         │                                   │
│  └──────────────┬───────────────────┘                                   │
│                 ▼                                                        │
│  ┌──────────────────────────────────┐                                   │
│  │  crypto_service.decrypt_request  │                                   │
│  │  base64 → AES-256-GCM decrypt   │                                   │
│  │  → { email, password, lat, lng } │                                   │
│  └──────────────┬───────────────────┘                                   │
│                 ▼                                                        │
│  ┌──────────────────────────────────┐                                   │
│  │  auth_service.tools.login()      │                                   │
│  │  1. Verify email + bcrypt hash   │                                   │
│  │  2. Create access + refresh JWT  │                                   │
│  │  3. Store refresh token in DB    │                                   │
│  │  4. Create attendance row        │                                   │
│  │     punch_in_store="Resolving…"  │                                   │
│  │  5. db.flush() (get attendance   │                                   │
│  │     ID without committing)       │                                   │
│  │  6. Publish to Kafka             │──────────────┐                    │
│  └──────────────┬───────────────────┘              │                    │
│                 ▼                                    │                    │
│  ┌──────────────────────────────────┐              │                    │
│  │  crypto_service.encrypt_response │              │                    │
│  │  → { "payload": "<base64>" }     │              │                    │
│  └──────────────┬───────────────────┘              │                    │
│                 │                                    │                    │
│          RESPONSE (fast)                            │                    │
└─────────────────────────────────────────────────────┼────────────────────┘
                                                      │
                                                      ▼
                                        ┌──────────────────────────┐
                                        │  KAFKA                   │
                                        │  Topic: geocoding-tasks  │
                                        │  Message:                │
                                        │  { attendance_id,        │
                                        │    latitude, longitude } │
                                        └────────────┬─────────────┘
                                                     │
                                                     ▼
                                        ┌──────────────────────────┐
                                        │  worker.py               │
                                        │  (geocoding-worker)      │
                                        │                          │
                                        │  1. Consume message      │
                                        │  2. GET LocationIQ API   │
                                        │  3. UPDATE attendance    │
                                        │     SET punch_in_store   │
                                        │     = resolved address   │
                                        │  4. sleep(0.5s) → next   │
                                        │                          │
                                        │  Rate: 2 req/sec         │
                                        │  On failure: "Unresolved"│
                                        └──────────────────────────┘
```

---

## API Endpoint

### `POST /api/1.1`

The frontend always calls the **obfuscated** route `/api/1.1`. The middleware internally rewrites it to `/api/login`.

---

## Encryption Layer (AES-256-GCM)

All request and response bodies are encrypted. The frontend and backend share a **256-bit AES key** (`AES_SECRET_KEY` — 64 hex chars).

### Encrypt (frontend → backend)

```
1. plaintext  = JSON.stringify({ email, password, latitude, longitude })
2. nonce      = 12 random bytes
3. ciphertext = AES-256-GCM.encrypt(key, nonce, plaintext, aad=null)
4. payload    = base64encode(nonce + ciphertext)
```

### Decrypt (backend → frontend)

Same process in reverse. The response `payload` is `base64(nonce + ciphertext)`.

---

## Request

### Headers

```
Content-Type: application/json
```

### Body (encrypted)

```json
{
  "payload": "base64-encoded-AES-256-GCM-ciphertext"
}
```

### Decrypted Payload (what the backend sees after decryption)

```json
{
  "email": "promoter@example.com",
  "password": "plaintext-password",
  "latitude": 28.613939,
  "longitude": 77.209021
}
```

| Field       | Type   | Required | Description                     |
|-------------|--------|----------|---------------------------------|
| `email`     | string | yes      | Promoter email (validated)      |
| `password`  | string | yes      | Plaintext password              |
| `latitude`  | float  | yes      | GPS latitude at login time      |
| `longitude` | float  | yes      | GPS longitude at login time     |

---

## Response

### Success — `200 OK`

```json
{
  "payload": "base64-encoded-AES-256-GCM-ciphertext"
}
```

### Decrypted Success Payload

```json
{
  "status_code": 200,
  "message": "Login successful",
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

| Field           | Type   | Description                              |
|-----------------|--------|------------------------------------------|
| `status_code`   | int    | Always `200` on success                  |
| `message`       | string | `"Login successful"`                     |
| `access_token`  | string | JWT, expires in 60 minutes               |
| `refresh_token` | string | JWT, expires in 10 hours, stored in DB   |
| `token_type`    | string | Always `"bearer"`                        |

### Error — `401 Unauthorized`

Returned when email doesn't exist or password is wrong.

```json
{
  "detail": "Invalid email or password"
}
```

### Error — `400 Bad Request`

Returned when the encrypted payload is malformed or can't be decrypted.

```json
{
  "detail": "Invalid or corrupted payload"
}
```

---

## JWT Token Details

### Access Token

```json
{
  "sub": "promoter-uuid",
  "exp": 1700000000,
  "type": "access"
}
```

- **Algorithm**: HS256
- **Expiry**: 60 minutes
- **Usage**: Pass in `Authorization: Bearer <token>` header for protected routes

### Refresh Token

```json
{
  "sub": "promoter-uuid",
  "exp": 1700036000,
  "type": "refresh",
  "jti": "unique-uuid"
}
```

- **Algorithm**: HS256
- **Expiry**: 10 hours
- **Stored in DB**: `refresh_tokens` table (supports revocation)
- **Usage**: POST to `/api/1.3` to get a new access token

---

## Background Geocoding (Async via Kafka)

The login response does **not** wait for geocoding. The attendance row is created with `punch_in_store = "Resolving..."` and updated asynchronously.

### Kafka Message (topic: `geocoding-tasks`)

```json
{
  "attendance_id": "uuid-string",
  "latitude": 28.613939,
  "longitude": 77.209021
}
```

### Worker Behavior

1. Consumes from `geocoding-tasks` (consumer group: `geocoding-worker`)
2. Calls LocationIQ reverse geocoding API
3. Updates `attendance.punch_in_store` with the resolved address
4. On API failure: sets `punch_in_store = "Unresolved"`
5. Rate limited to **2 requests/second** (`time.sleep(0.5)`)

### LocationIQ API Call (made by worker)

```
GET https://us1.locationiq.com/v1/reverse?key={key}&lat={lat}&lon={lng}&format=json
```

Response field used: `data["display_name"]`

---

## Attendance Row Lifecycle

```
 Login request received
     │
     ▼
 ┌────────────────────────────────────┐
 │ INSERT attendance                  │
 │   punch_in_store = "Resolving..."  │
 │   punch_in_lat   = 28.613939      │
 │   punch_in_lng   = 77.209021      │
 │   punch_in_timestamp = now()       │
 └─────────────────┬──────────────────┘
                   │
        Worker processes Kafka message
                   │
                   ▼
 ┌────────────────────────────────────────────────────────┐
 │ UPDATE attendance                                      │
 │   punch_in_store = "123, Main St, Delhi, India, 11001" │
 │                                                        │
 │   (or "Unresolved" on failure)                         │
 └────────────────────────────────────────────────────────┘
```

---

## Route Map Reference

| Code  | Internal Route | Purpose          |
|-------|---------------|------------------|
| `1.1` | `/api/login`  | Login + Punch-in |
| `1.2` | `/api/register` | Registration   |
| `1.3` | `/api/refresh`  | Token refresh  |
| `1.4` | `/api/punch-out`| Punch-out      |

---

## File Reference

| File | Role |
|------|------|
| `main.py` | FastAPI app, mounts middleware + router |
| `shared/middleware.py` | Rewrites `/api/1.1` → `/api/login` |
| `shared/constants.py` | Route map definitions |
| `services/crypto_service/tools.py` | AES-256-GCM encrypt/decrypt |
| `services/auth_service/server.py` | `/api/login` endpoint handler |
| `services/auth_service/models.py` | Pydantic request/response models |
| `services/auth_service/tools.py` | Login logic (auth + attendance + Kafka publish) |
| `services/auth_service/token_manager.py` | JWT creation and decoding |
| `services/auth_service/authenticator.py` | bcrypt password verification |
| `services/geocoding_service/tools.py` | LocationIQ reverse geocode |
| `shared/kafka_producer.py` | Singleton Kafka producer |
| `worker.py` | Kafka consumer — async geocoding worker |
