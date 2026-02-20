# Promoter API Reference

All requests and responses are **AES-256-GCM encrypted**. The wire format is always:

```json
{ "payload": "<base64-encoded ciphertext>" }
```

Decrypt/encrypt using the shared `AES_SECRET_KEY`. The examples below show the **decrypted** JSON.

---

## Endpoints

| Code   | Method   | Route              | Auth Required | Description              |
|--------|----------|--------------------|---------------|--------------------------|
| `1.1`  | POST     | `/api/1.1`         | No            | Login + Punch-in         |
| `1.2`  | POST     | `/api/1.2`         | No            | Register new promoter    |
| `1.5`  | PUT      | `/api/1.5`         | Bearer token  | Update promoter profile  |
| `1.6`  | DELETE   | `/api/1.6`         | Bearer token  | Delete promoter account  |

---

## Authentication

Protected endpoints (`1.5`, `1.6`) require the access token from login:

```
Authorization: Bearer <access_token>
```

The token identifies which promoter is being modified/deleted — no ID is sent in the body.

- **Access token expiry**: 60 minutes
- **Refresh token expiry**: 10 hours (use `POST /api/1.3` to refresh)

---

## 1. Register Promoter

### `POST /api/1.2`

Creates a new promoter account.

#### Request (decrypted)

```json
{
  "name": "Rahul Sharma",
  "email": "rahul@example.com",
  "password": "securePass123",
  "contact_number": "9876543210"
}
```

| Field            | Type   | Required | Validation              |
|------------------|--------|----------|-------------------------|
| `name`           | string | yes      | Max 100 chars           |
| `email`          | string | yes      | Valid email, unique      |
| `password`       | string | yes      | Stored as bcrypt hash    |
| `contact_number` | string | yes      | Max 15 chars             |

#### Success — `200` (decrypted)

```json
{
  "status_code": 201,
  "message": "Registration successful",
  "promoter_id": "a3b8f7e2-1234-4abc-9def-567890abcdef"
}
```

#### Error — Duplicate Email (decrypted)

```json
{
  "status_code": 409,
  "message": "Email already registered"
}
```

---

## 2. Login + Punch-In

### `POST /api/1.1`

Authenticates the promoter and records a punch-in. Geocoding of the location happens **asynchronously** — the response returns immediately.

#### Request (decrypted)

```json
{
  "email": "rahul@example.com",
  "password": "securePass123",
  "latitude": 28.613939,
  "longitude": 77.209021
}
```

| Field       | Type   | Required | Description                |
|-------------|--------|----------|----------------------------|
| `email`     | string | yes      | Registered email           |
| `password`  | string | yes      | Plaintext password         |
| `latitude`  | float  | yes      | GPS latitude at login      |
| `longitude` | float  | yes      | GPS longitude at login     |

#### Success — `200` (decrypted)

```json
{
  "status_code": 200,
  "message": "Login successful",
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

| Field           | Type   | Description                            |
|-----------------|--------|----------------------------------------|
| `access_token`  | string | JWT, expires in 60 min — use for auth  |
| `refresh_token` | string | JWT, expires in 10 hrs — use to renew  |
| `token_type`    | string | Always `"bearer"`                      |

#### Error — `401`

```json
{
  "detail": "Invalid email or password"
}
```

---

## 3. Update Promoter

### `PUT /api/1.5`

Updates the authenticated promoter's profile. Send **only the fields you want to change** — omitted fields stay unchanged.

#### Headers

```
Content-Type: application/json
Authorization: Bearer <access_token>
```

#### Request (decrypted)

```json
{
  "name": "Rahul K. Sharma",
  "contact_number": "9988776655"
}
```

All fields are optional — include only what needs to change:

| Field            | Type   | Required | Notes                          |
|------------------|--------|----------|--------------------------------|
| `name`           | string | no       | Max 100 chars                  |
| `email`          | string | no       | Must be unique, valid email    |
| `contact_number` | string | no       | Max 15 chars                   |
| `password`       | string | no       | Will be re-hashed with bcrypt  |

#### Success — `200` (decrypted)

```json
{
  "status_code": 200,
  "message": "Promoter updated successfully",
  "promoter_id": "a3b8f7e2-1234-4abc-9def-567890abcdef"
}
```

#### Error — Email Taken (decrypted)

```json
{
  "status_code": 409,
  "message": "Email already in use"
}
```

#### Error — `401`

```json
{
  "detail": "Invalid or expired token"
}
```

---

## 4. Delete Promoter

### `DELETE /api/1.6`

Permanently deletes the authenticated promoter's account and all associated data (attendance records, refresh tokens).

#### Headers

```
Authorization: Bearer <access_token>
```

#### Request Body

None required. The promoter is identified by the token.

#### Success — `200` (decrypted)

```json
{
  "status_code": 200,
  "message": "Promoter deleted successfully"
}
```

#### Error — `401`

```json
{
  "detail": "Invalid or expired token"
}
```

---

## Encryption Guide (Frontend)

### Encrypting a Request

```
1. plaintext  = JSON.stringify({ email: "...", password: "...", ... })
2. nonce      = 12 random bytes
3. ciphertext = AES-GCM.encrypt(key, nonce, plaintext, aad = null)
4. payload    = base64encode(nonce + ciphertext)
5. body       = { "payload": payload }
```

### Decrypting a Response

```
1. raw        = base64decode(response.payload)
2. nonce      = raw.slice(0, 12)
3. ciphertext = raw.slice(12)
4. plaintext  = AES-GCM.decrypt(key, nonce, ciphertext, aad = null)
5. data       = JSON.parse(plaintext)
```

**Key**: 256-bit (32 bytes), provided as 64-char hex string in `AES_SECRET_KEY`.

---

## Error Reference

| HTTP Status | Meaning                              | When                                      |
|-------------|--------------------------------------|-------------------------------------------|
| `200`       | Success                              | All successful operations                 |
| `400`       | Bad Request                          | Payload can't be decrypted or parsed      |
| `401`       | Unauthorized                         | Wrong credentials or invalid/expired token|
| `409`       | Conflict (in decrypted status_code)  | Duplicate email on register or update     |
| `422`       | Validation Error                     | Missing or invalid fields in payload      |

---

## Promoter Data Model

```
┌──────────────────────────────────────┐
│  promoters                           │
├──────────────────────────────────────┤
│  id              UUID (PK)           │
│  name            VARCHAR(100)        │
│  email           VARCHAR(255) UNIQUE │
│  password_hash   VARCHAR(255)        │
│  contact_number  VARCHAR(15)         │
│  created_at      TIMESTAMP           │
├──────────────────────────────────────┤
│  ↓ CASCADE DELETE                    │
│  → attendance (punch in/out records) │
│  → refresh_tokens (JWT revocation)   │
└──────────────────────────────────────┘
```
