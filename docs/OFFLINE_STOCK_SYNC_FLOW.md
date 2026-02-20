# Offline Stock Management & Sync Flow — Architecture Reference

## Overview

Promoters work **entirely offline** during their shift. Product catalog, opening stock, and sales data are stored as **AES-256-GCM encrypted files** in Android app-private storage. At the end of the shift, the promoter taps **Sync + Punch Out** — the app bundles all shift data, encrypts it, and sends it to the backend in a single request. The backend decrypts, validates, and persists everything in one atomic transaction.

---

## Why Offline-First?

- Promoters work in retail stores with **unreliable network** (basement shelves, rural areas)
- Stock takes and sales happen **dozens of times per shift** — can't depend on live API calls
- Reduces backend load to **one bulk request per shift** instead of per-sale calls
- Encrypted local storage prevents **data tampering** on rooted/compromised devices

---

## Architecture Diagram

```
═══════════════════════════════════════════════════════════════════════════════════
  PHASE 1: LOGIN (Online)
═══════════════════════════════════════════════════════════════════════════════════

  ┌───────────────────────────────────────────────────────────────────────────┐
  │  APP                                                                      │
  │                                                                           │
  │  1. Promoter logs in (email + password + GPS)                             │
  │  2. POST /api/1.1 → encrypted login request                              │
  │  3. Receives:                                                             │
  │     a. access_token + refresh_token                                       │
  │     b. AES-encrypted product catalog JSON                                 │
  │  4. Decrypt product catalog using session key                             │
  │  5. Store decrypted catalog in app-private storage (re-encrypted locally) │
  │  6. App is now FULLY OFFLINE CAPABLE                                      │
  └───────────────────────────┬───────────────────────────────────────────────┘
                              │ HTTPS
                              ▼
  ┌───────────────────────────────────────────────────────────────────────────┐
  │  BACKEND                                                                  │
  │                                                                           │
  │  1. Decrypt + verify credentials                                          │
  │  2. Generate JWT tokens                                                   │
  │  3. Create attendance row (punch-in)                                      │
  │  4. Publish geocoding task to Kafka                                       │
  │  5. Query all products from DB → serialize to JSON                        │
  │  6. Encrypt response: { tokens + products_json }                          │
  │  7. Return encrypted response                                             │
  └───────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════
  PHASE 2: DURING SHIFT (Fully Offline)
═══════════════════════════════════════════════════════════════════════════════════

  ┌───────────────────────────────────────────────────────────────────────────┐
  │  APP (No network required)                                                │
  │                                                                           │
  │  Android/data/com.candorfoods.app/files/                                  │
  │  ├── products.enc          ← Product catalog (encrypted JSON)             │
  │  ├── opening_stock.enc     ← Opening stock entries (encrypted JSON)       │
  │  └── sales.enc             ← Sales log (encrypted JSON)                   │
  │                                                                           │
  │  STOCK TAKE:                                                              │
  │    Promoter scans barcode (EAN) → app looks up from products.enc          │
  │    → enters quantity on shelf → appended to opening_stock.enc             │
  │                                                                           │
  │  SALES:                                                                   │
  │    Promoter records a sale → scans EAN → enters qty sold                  │
  │    → appended to sales.enc                                                │
  │                                                                           │
  │  IN-HAND STOCK (computed, never stored):                                  │
  │    in_hand_qty = opening_qty - SUM(sold_qty for that EAN)                 │
  │    Calculated on-the-fly from opening_stock.enc + sales.enc               │
  └───────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════
  PHASE 3: SYNC + PUNCH OUT (Online)
═══════════════════════════════════════════════════════════════════════════════════

  ┌───────────────────────────────────────────────────────────────────────────┐
  │  APP                                                                      │
  │                                                                           │
  │  1. Promoter taps "Sync + Punch Out"                                      │
  │  2. App reads opening_stock.enc + sales.enc                               │
  │  3. Computes final stock_report:                                          │
  │     [{ ean, opening_qty, sold_qty, in_hand_qty }, ...]                    │
  │  4. Bundles: { latitude, longitude, stock_report }                        │
  │  5. AES-256-GCM encrypts the bundle                                      │
  │  6. POST /api/1.4 with Authorization: Bearer <access_token>              │
  │     body: { "payload": "<base64>" }                                       │
  │  7. On 200 OK:                                                            │
  │     → Delete opening_stock.enc, sales.enc                                 │
  │     → Show "Shift ended" confirmation                                     │
  │  8. On failure:                                                           │
  │     → Mark as "pending sync"                                              │
  │     → Retry with exponential backoff                                      │
  │     → Files are NOT deleted until sync succeeds                           │
  └───────────────────────────┬───────────────────────────────────────────────┘
                              │ HTTPS
                              ▼
  ┌───────────────────────────────────────────────────────────────────────────┐
  │  BACKEND                                                                  │
  │                                                                           │
  │  1. Verify Bearer token → extract promoter_id                             │
  │  2. Decrypt AES payload                                                   │
  │  3. Validate stock_report array                                           │
  │  4. In a SINGLE DB transaction:                                           │
  │     a. Insert all stock_report rows into stock_reports table              │
  │     b. Update attendance row (punch_out_timestamp, lat/lng, store)        │
  │  5. Publish punch-out geocoding task to Kafka                             │
  │  6. Return encrypted success response                                     │
  └───────────────────────────────────────────────────────────────────────────┘
```

---

## Encrypted File Format

All local files use the same encryption scheme as the API transport layer.

### Encryption

```
1. plaintext  = JSON.stringify(data)
2. nonce      = 12 random bytes (crypto-secure)
3. ciphertext = AES-256-GCM.encrypt(session_key, nonce, plaintext, aad = null)
4. file_bytes = nonce (12 bytes) + ciphertext
5. Write file_bytes to <filename>.enc
```

### Decryption

```
1. file_bytes = read <filename>.enc
2. nonce      = file_bytes[0:12]
3. ciphertext = file_bytes[12:]
4. plaintext  = AES-256-GCM.decrypt(session_key, nonce, ciphertext, aad = null)
5. data       = JSON.parse(plaintext)
```

### Key: AES-256 (32 bytes / 64 hex chars)

The encryption key for local files is a **per-session derived key**, NOT the raw master `AES_SECRET_KEY`. See [Encryption Key Strategy](#encryption-key-strategy) below.

---

## Local File Storage

### Location

```
Android/data/com.candorfoods.app/files/
```

This is Android's **app-private internal storage**:
- No other app can read/write here (enforced by OS sandbox)
- No special permissions required
- Cleared when app is uninstalled
- Survives app restarts and crashes
- NOT accessible via file manager (unless device is rooted)

### File Descriptions

#### `products.enc` — Product Catalog

Written once on login. Read-only during the shift.

```json
[
  {
    "sr_no": 1,
    "ean": "8903363008848",
    "article_code": "100004790",
    "description": "Healthy Choice Roasted Sunflower Seeds 100 g",
    "mrp": 50,
    "size_kg": 0.1,
    "gst_rate": 0.05
  },
  ...
]
```

#### `opening_stock.enc` — Opening Stock Take

Created when promoter starts stock take. One entry per product scanned.

```json
[
  {
    "ean": "8903363008848",
    "article_code": "100004790",
    "description": "Healthy Choice Roasted Sunflower Seeds 100 g",
    "opening_qty": 20,
    "timestamp": "2026-02-14T09:15:30"
  },
  {
    "ean": "8906064917501",
    "article_code": "100004671",
    "description": "Dried Cranberry Whole 100g",
    "opening_qty": 15,
    "timestamp": "2026-02-14T09:16:05"
  }
]
```

#### `sales.enc` — Sales Log

Appended throughout the day. One entry per sale event.

```json
[
  {
    "ean": "8903363008848",
    "qty_sold": 2,
    "timestamp": "2026-02-14T11:30:00"
  },
  {
    "ean": "8903363008848",
    "qty_sold": 1,
    "timestamp": "2026-02-14T14:20:00"
  },
  {
    "ean": "8906064917501",
    "qty_sold": 5,
    "timestamp": "2026-02-14T13:45:00"
  }
]
```

### In-Hand Stock Calculation (Computed On-the-Fly)

**Never stored as a separate file.** The app computes it from the other two files:

```
For each EAN in opening_stock.enc:
    total_sold   = SUM(qty_sold) from sales.enc WHERE ean matches
    in_hand_qty  = opening_qty - total_sold
```

Example for EAN `8903363008848`:
```
opening_qty = 20
total_sold  = 2 + 1 = 3
in_hand_qty = 20 - 3 = 17
```

This is displayed in the UI but only finalized into the sync payload at punch-out time.

---

## Encryption Key Strategy

### Recommended: Per-Session Derived Key (HKDF)

The master `AES_SECRET_KEY` is **never embedded in the app or stored on disk**. Instead, both the app and backend independently derive the same session key.

```
session_key = HKDF-SHA256(
    ikm  = AES_SECRET_KEY,             // master key (only backend knows this)
    salt = SHA256(promoter_id),         // user-specific
    info = session_id (attendance_id),  // session-specific
    len  = 32 bytes
)
```

### Flow

```
1. LOGIN
   Backend generates attendance_id (UUID) for this shift.
   Backend derives: session_key = HKDF(master_key, promoter_id, attendance_id)
   Backend encrypts product catalog with session_key.
   Backend sends: { tokens, attendance_id, encrypted_products }

2. APP
   App receives attendance_id.
   App derives: session_key = HKDF(???)

   PROBLEM: App doesn't have the master key.
```

### Practical Alternative: Backend-Issued Session Key

Since the app cannot hold the master key, the backend generates a random session key and sends it securely during login (inside the already AES-encrypted login response):

```
LOGIN RESPONSE (decrypted):
{
  "status_code": 200,
  "access_token": "...",
  "refresh_token": "...",
  "attendance_id": "uuid",
  "session_key": "a1b2c3...64-hex-chars",     ← random 256-bit key for this shift
  "products": [ ... ]
}
```

- `session_key` is a **random 256-bit key** generated per login session
- It's transmitted inside the AES-encrypted login response (protected by `AES_SECRET_KEY` over HTTPS)
- The app uses it to encrypt/decrypt ALL local files during the shift
- The backend stores it (mapped to `attendance_id`) to decrypt the sync payload later
- It's **never written to disk unprotected** — the app holds it in memory (or in Android Keystore)

### Why This Is Secure

| Layer | Protection |
|-------|-----------|
| HTTPS | Encrypts all network traffic |
| AES-256-GCM (transport) | Encrypts request/response bodies — even if TLS is MITM'd, payload is unreadable |
| App-private storage | Android OS sandbox — no other app can access the files |
| AES-256-GCM (local files) | Even with root access, files are encrypted |
| Per-session key | Compromising one session doesn't expose other sessions' data |
| Key not on disk | Session key stored in Android Keystore or memory, not in a file |

---

## Sync + Punch Out API

### `POST /api/1.4`

Ends the shift: syncs stock data and records punch-out.

#### Headers

```
Content-Type: application/json
Authorization: Bearer <access_token>
```

#### Request Body (encrypted)

```json
{
  "payload": "<base64-encoded AES-256-GCM ciphertext>"
}
```

#### Decrypted Payload

```json
{
  "latitude": 28.613939,
  "longitude": 77.209021,
  "attendance_id": "a3b8f7e2-1234-4abc-9def-567890abcdef",
  "stock_report": [
    {
      "ean": "8903363008848",
      "article_code": "100004790",
      "description": "Healthy Choice Roasted Sunflower Seeds 100 g",
      "opening_qty": 20,
      "sold_qty": 3,
      "in_hand_qty": 17
    },
    {
      "ean": "8906064917501",
      "article_code": "100004671",
      "description": "Dried Cranberry Whole 100g",
      "opening_qty": 15,
      "sold_qty": 5,
      "in_hand_qty": 10
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `latitude` | float | yes | GPS latitude at punch-out |
| `longitude` | float | yes | GPS longitude at punch-out |
| `attendance_id` | UUID | yes | Attendance row created at login |
| `stock_report` | array | yes | One entry per product handled during the shift |
| `stock_report[].ean` | string | yes | Product EAN barcode |
| `stock_report[].article_code` | string | yes | Product article code |
| `stock_report[].description` | string | yes | Product name |
| `stock_report[].opening_qty` | int | yes | Quantity on shelf at start of shift |
| `stock_report[].sold_qty` | int | yes | Total quantity sold during shift |
| `stock_report[].in_hand_qty` | int | yes | opening_qty - sold_qty |

#### Success Response — `200` (decrypted)

```json
{
  "status_code": 200,
  "message": "Sync successful. Shift ended."
}
```

#### Error — `409 Conflict` (duplicate sync)

```json
{
  "status_code": 409,
  "message": "This shift has already been synced"
}
```

#### Error — `401 Unauthorized`

```json
{
  "detail": "Invalid or expired token"
}
```

---

## Backend Processing (on Sync)

All operations happen in a **single database transaction** — either everything succeeds or nothing is saved.

```
BEGIN TRANSACTION;

  1. Verify attendance_id belongs to this promoter
  2. Verify attendance row has no punch_out_timestamp (not already synced)

  3. For each item in stock_report:
       INSERT INTO stock_reports (
         attendance_id, product_id, ean, article_code,
         opening_qty, sold_qty, in_hand_qty
       )

  4. UPDATE attendance SET
       punch_out_timestamp = NOW(),
       punch_out_lat = <latitude>,
       punch_out_lng = <longitude>,
       punch_out_store = 'Resolving...'

COMMIT;

  5. Publish geocoding task to Kafka (async store name resolution)
  6. Delete stored session_key (no longer needed)
```

---

## Database: `stock_reports` Table

```sql
CREATE TABLE stock_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attendance_id   UUID NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
    product_id      UUID NOT NULL REFERENCES products(id),
    ean             VARCHAR(13)  NOT NULL,
    article_code    VARCHAR(15)  NOT NULL,
    opening_qty     INTEGER NOT NULL,
    sold_qty        INTEGER NOT NULL,
    in_hand_qty     INTEGER NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Query: "all stock reports for this shift"
CREATE INDEX idx_stockreports_attendance ON stock_reports (attendance_id);

-- Query: "sales history for this product across all shifts"
CREATE INDEX idx_stockreports_product ON stock_reports (product_id);

-- Query: "find reports by EAN barcode"
CREATE INDEX idx_stockreports_ean ON stock_reports (ean);

-- Query: "reports within a date range"
CREATE INDEX idx_stockreports_created ON stock_reports (created_at);

-- Composite: "product performance for a specific attendance"
CREATE INDEX idx_stockreports_att_product ON stock_reports (attendance_id, product_id);
```

---

## Database: `session_keys` Table

Stores the per-session encryption key so the backend can decrypt the sync payload.

```sql
CREATE TABLE session_keys (
    attendance_id   UUID PRIMARY KEY REFERENCES attendance(id) ON DELETE CASCADE,
    session_key     VARCHAR(64) NOT NULL,   -- 64-char hex (256-bit key)
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
```

- One row per shift (1:1 with attendance)
- Deleted after successful sync (key is no longer needed)
- If not deleted, CASCADE on attendance deletion cleans it up

---

## Edge Cases & Error Handling

### 1. No Network at Punch-Out

```
App                                        Backend
 │                                           │
 ├─── POST /api/1.4 ──────────── TIMEOUT ───►│
 │                                           │
 │  Mark as "pending_sync"                   │
 │  Show: "Sync pending. Will retry."        │
 │                                           │
 │  Retry #1 (after 5s)                      │
 ├─── POST /api/1.4 ──────────── TIMEOUT ───►│
 │                                           │
 │  Retry #2 (after 15s)                     │
 ├─── POST /api/1.4 ──────────── 200 OK ───►│
 │                                           │
 │  Delete local .enc files                  │
 │  Show: "Shift ended"                      │
```

**Retry strategy**: Exponential backoff — 5s, 15s, 45s, 2min, 5min. After 5 retries, show manual "Retry" button. Local files are **never deleted** until the backend confirms `200 OK`.

### 2. App Crash Mid-Shift

No data loss. All `.enc` files are written to disk (not just memory). On app restart:

1. Check if `opening_stock.enc` exists → shift was in progress
2. Check if attendance has `punch_out_timestamp` → if null, shift is still active
3. Resume shift from existing files

### 3. Duplicate Sync Attempt

The backend checks if `attendance.punch_out_timestamp` is already set. If yes, it returns `409 Conflict` and the app treats it as success (data was already saved).

**Idempotency key**: `attendance_id` — each shift has a unique attendance row. The backend rejects duplicates.

### 4. Product Catalog Changed Mid-Shift

Does not matter. The promoter works with the catalog snapshot from login. If products are added/removed in the backend during the shift, the promoter gets the updated catalog on their **next login**.

### 5. Promoter Adds Stock for Unknown EAN

The app should only allow selection from `products.enc`. Free-text EAN entry should validate against the local catalog. If the EAN is not found, the app should show an error — **not** allow arbitrary entries.

### 6. Validation on Sync (Backend)

The backend validates every row in the `stock_report`:

| Check | Action on Failure |
|-------|-------------------|
| `ean` exists in `products` table | Reject entire sync with 422 |
| `in_hand_qty == opening_qty - sold_qty` | Reject entire sync with 422 |
| `opening_qty >= 0` and `sold_qty >= 0` | Reject entire sync with 422 |
| `sold_qty <= opening_qty` | Reject entire sync with 422 |
| `attendance_id` belongs to this promoter | Reject with 403 |
| Shift not already synced | Reject with 409 |

---

## File Lifecycle Summary

```
LOGIN
  ├── products.enc          CREATED (from backend response)
  ├── opening_stock.enc     NOT YET
  └── sales.enc             NOT YET

STOCK TAKE
  ├── products.enc          READ (lookup by EAN)
  ├── opening_stock.enc     CREATED / APPENDED
  └── sales.enc             NOT YET

DURING SALES
  ├── products.enc          READ (lookup by EAN)
  ├── opening_stock.enc     READ (to compute in-hand)
  └── sales.enc             CREATED / APPENDED

SYNC + PUNCH OUT
  ├── products.enc          READ (for reference in payload)
  ├── opening_stock.enc     READ → then DELETED after 200 OK
  └── sales.enc             READ → then DELETED after 200 OK

NEXT LOGIN
  ├── products.enc          OVERWRITTEN (fresh catalog)
  ├── opening_stock.enc     FRESH START
  └── sales.enc             FRESH START
```

---

## Route Map Reference

| Code  | Method | Internal Route     | Auth     | Purpose                      |
|-------|--------|--------------------|----------|------------------------------|
| `1.1` | POST   | `/api/login`       | No       | Login + Punch-in + Catalog   |
| `1.2` | POST   | `/api/register`    | No       | Registration                 |
| `1.3` | POST   | `/api/refresh`     | Refresh  | Token refresh                |
| `1.4` | POST   | `/api/punch-out`   | Bearer   | Sync + Punch-out             |
| `1.5` | PUT    | `/api/promoter-update`  | Bearer | Update profile          |
| `1.6` | DELETE | `/api/promoter-delete`  | Bearer | Delete account          |

---

## File Reference

| File | Role |
|------|------|
| `services/crypto_service/tools.py` | AES-256-GCM encrypt/decrypt functions |
| `services/auth_service/tools.py` | Login logic — will need to include product catalog + session key in response |
| `shared/models.py` | ORM models — `Product`, `StockReport`, `SessionKey` to be added |
| `shared/constants.py` | Route map (`1.4` = punch-out) |
| `worker.py` | Kafka consumer for async geocoding |
| `seed_products.py` | One-time script to populate products table from Excel |
