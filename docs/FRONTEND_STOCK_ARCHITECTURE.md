# Frontend Offline Stock Architecture — Android (Java) Reference

## Overview

Promoters work **fully offline** during their shift. Product catalog, opening stock, received stock,
and sales data are stored as **AES-256-GCM encrypted files** in Android app-private internal storage.
At the end of the shift, the promoter taps **Sync + Punch Out** — the app bundles all shift data,
encrypts it, and sends it to the backend in a single request.

---

## Package Structure

```
com.candorfoods.app/
├── crypto/
│   └── CryptoManager.java            ← AES-256-GCM encrypt/decrypt
├── storage/
│   └── SecureFileManager.java         ← Read/write encrypted .enc files
├── stock/
│   ├── model/
│   │   ├── Product.java               ← Product catalog POJO
│   │   ├── OpeningStockEntry.java     ← Opening stock POJO
│   │   ├── ReceivedStockEntry.java    ← Received stock POJO
│   │   ├── SaleEntry.java             ← Sale event POJO
│   │   ├── InHandStock.java           ← Computed in-hand POJO
│   │   └── StockReportItem.java       ← Final sync payload per article
│   ├── ProductCatalog.java            ← Product lookup from local catalog
│   └── StockManager.java             ← Core offline stock logic
├── sync/
│   └── SyncService.java              ← Punch-out bulk sync with retry
└── api/
    └── ApiClient.java                 ← Encrypted API calls
```

---

## Encrypted File Storage

### Location

```
/data/data/com.candorfoods.app/files/
```

Accessed via `context.getFilesDir()`. This is Android's **app-private internal storage**:
- Sandboxed by OS — no other app can access it
- No permissions required
- Survives app restarts and crashes
- Cleared only on app uninstall
- Not accessible via file manager (unless rooted)

### Files

| File | Written When | Read When | Deleted When |
|------|-------------|-----------|--------------|
| `products.enc` | Login (from backend response) | Barcode scan, product lookup | Overwritten on next login |
| `opening_stock.enc` | Stock take | Sales validation, dashboard, sync | After successful sync |
| `received_stock.enc` | New stock delivery mid-shift | Dashboard, sync | After successful sync |
| `sales.enc` | Each sale recorded | Dashboard, sync | After successful sync |

---

## Architecture Flow

```
═══════════════════════════════════════════════════════════════════════════
 PHASE 1: LOGIN (Online)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP                                                                │
 │                                                                     │
 │  1. Collect email, password, GPS (lat, lng)                         │
 │  2. Build JSON: { email, password, latitude, longitude }            │
 │  3. CryptoManager.encrypt(json) → base64 payload                   │
 │  4. POST /api/1.1  body: { "payload": "<base64>" }                 │
 │  5. Receive response: { "payload": "<base64>" }                    │
 │  6. CryptoManager.decrypt(payload) → JSON string                   │
 │  7. Parse JSON → LoginResponse object                               │
 │  8. Extract:                                                        │
 │     • access_token  → store in EncryptedSharedPreferences           │
 │     • refresh_token → store in EncryptedSharedPreferences           │
 │     • promoter_name → store in EncryptedSharedPreferences           │
 │     • products[]    → encrypt → write to products.enc               │
 │  9. Navigate to Stock Take screen                                   │
 └─────────────────────────────────────────────────────────────────────┘

 Login Response (decrypted JSON):
 {
   "status_code": 200,
   "message": "Login successful",
   "access_token": "eyJhbGci...",
   "refresh_token": "eyJhbGci...",
   "token_type": "bearer",
   "promoter_name": "Rahul Sharma",
   "punched_in": true,
   "products": [
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
 }


═══════════════════════════════════════════════════════════════════════════
 PHASE 2: STOCK TAKE (Offline)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP (No network)                                                   │
 │                                                                     │
 │  1. Promoter scans barcode (camera / manual entry)                  │
 │  2. ProductCatalog.findByEan(ean)                                   │
 │     → reads products.enc → decrypts → searches by EAN              │
 │     → if not found: show "Product not in catalog" error             │
 │  3. Promoter enters quantity on shelf                               │
 │  4. StockManager.addOpeningStock(ean, qty)                          │
 │     → reads opening_stock.enc (or creates new)                      │
 │     → if EAN already exists: update qty                             │
 │     → if EAN is new: append entry                                   │
 │     → encrypt → write back to opening_stock.enc                     │
 │  5. Display updated list of all opening stock entries               │
 └─────────────────────────────────────────────────────────────────────┘

 opening_stock.enc (decrypted):
 [
   {
     "ean": "8903363008848",
     "article_code": "100004790",
     "description": "Healthy Choice Roasted Sunflower Seeds 100 g",
     "opening_qty": 20,
     "timestamp": "2026-02-14T09:15:30"
   }
 ]


═══════════════════════════════════════════════════════════════════════════
 PHASE 3: RECEIVED STOCK — mid-shift delivery (Offline)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP (No network)                                                   │
 │                                                                     │
 │  1. New stock delivery arrives at the store                         │
 │  2. Promoter scans barcode                                          │
 │  3. ProductCatalog.findByEan(ean) → validate EAN exists             │
 │  4. Promoter enters received quantity                               │
 │  5. StockManager.addReceivedStock(ean, qty)                         │
 │     → reads received_stock.enc (or creates new)                     │
 │     → ALWAYS appends (multiple deliveries per EAN allowed)          │
 │     → encrypt → write back to received_stock.enc                    │
 │  6. Dashboard auto-refreshes — in_hand_qty increases                │
 └─────────────────────────────────────────────────────────────────────┘

 received_stock.enc (decrypted):
 [
   { "ean": "8903363008848", "qty_received": 10, "timestamp": "2026-02-14T13:30:00" },
   { "ean": "8903363008848", "qty_received": 5,  "timestamp": "2026-02-14T15:45:00" }
 ]

 NOTE: Two entries for same EAN = two separate deliveries. SUM them.


═══════════════════════════════════════════════════════════════════════════
 PHASE 4: SALES RECORDING (Offline)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP (No network)                                                   │
 │                                                                     │
 │  1. Promoter records a sale (scans barcode, enters qty)             │
 │  2. VALIDATE FIRST:                                                 │
 │     in_hand = opening + SUM(received) - SUM(sold_so_far)            │
 │     if (qty_to_sell > in_hand) → ERROR: "Insufficient stock"        │
 │  3. StockManager.addSale(ean, qty)                                  │
 │     → reads sales.enc (or creates new)                              │
 │     → append sale entry                                             │
 │     → encrypt → write back to sales.enc                             │
 │  4. Dashboard refreshes — in_hand_qty decreases                     │
 └─────────────────────────────────────────────────────────────────────┘

 sales.enc (decrypted):
 [
   { "ean": "8903363008848", "qty_sold": 2, "timestamp": "2026-02-14T11:30:00" },
   { "ean": "8903363008848", "qty_sold": 1, "timestamp": "2026-02-14T14:20:00" }
 ]


═══════════════════════════════════════════════════════════════════════════
 PHASE 5: LIVE DASHBOARD — in-hand stock (Offline, Computed)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP (No network)                                                   │
 │                                                                     │
 │  StockManager.getAllInHandStock()                                   │
 │    → Read opening_stock.enc → decrypt → List<OpeningStockEntry>     │
 │    → Read received_stock.enc → decrypt → List<ReceivedStockEntry>   │
 │    → Read sales.enc → decrypt → List<SaleEntry>                     │
 │                                                                     │
 │    For each EAN in opening stock:                                   │
 │      total_received = SUM(qty_received) WHERE ean matches           │
 │      total_sold     = SUM(qty_sold) WHERE ean matches               │
 │      in_hand_qty    = opening_qty + total_received - total_sold     │
 │                                                                     │
 │  NEVER STORED. Computed on-the-fly every time dashboard opens.      │
 └─────────────────────────────────────────────────────────────────────┘

 Dashboard UI:
 ┌──────────────────────────────────┬─────────┬──────────┬──────┬─────────┐
 │ Product                          │ Opening │ Received │ Sold │ In Hand │
 ├──────────────────────────────────┼─────────┼──────────┼──────┼─────────┤
 │ Sunflower Seeds 100g             │   20    │    15    │   8  │   27    │
 │ Pumpkin Seeds 100g               │   10    │     0    │   3  │    7    │
 │ Dried Cranberry 100g             │   15    │     5    │   5  │   15    │
 └──────────────────────────────────┴─────────┴──────────┴──────┴─────────┘

 Example calculation for Sunflower Seeds:
   opening  = 20
   received = 10 + 5 = 15
   sold     = 2 + 1 + 5 = 8
   in_hand  = 20 + 15 - 8 = 27


═══════════════════════════════════════════════════════════════════════════
 PHASE 6: SYNC + PUNCH OUT (Online)
═══════════════════════════════════════════════════════════════════════════

 ┌─────────────────────────────────────────────────────────────────────┐
 │  APP                                                                │
 │                                                                     │
 │  1. Promoter taps "Sync + Punch Out"                                │
 │  2. Capture current GPS (lat, lng)                                  │
 │  3. StockManager.buildStockReport()                                 │
 │     → For each EAN:                                                 │
 │       closing_qty = opening_qty + received_qty - sold_qty           │
 │     → Returns List<StockReportItem>                                 │
 │  4. Build PunchOutPayload:                                          │
 │     { latitude, longitude, attendance_id, stock_report[] }          │
 │  5. CryptoManager.encrypt(payload) → base64                        │
 │  6. POST /api/1.4                                                   │
 │     Headers: Authorization: Bearer <access_token>                   │
 │     Body: { "payload": "<base64>" }                                 │
 │  7. On 200 OK:                                                      │
 │     → SecureFileManager.clearShiftData()                            │
 │       (deletes opening_stock.enc, received_stock.enc, sales.enc)    │
 │     → Show "Shift ended" → navigate to Login                       │
 │  8. On failure:                                                     │
 │     → Retry with exponential backoff (5s, 15s, 45s, 2min, 5min)    │
 │     → .enc files are NOT deleted — data is safe                     │
 │     → After 5 retries: show manual "Retry" button                  │
 └───────────────────────────┬─────────────────────────────────────────┘
                             │ HTTPS
                             ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │  BACKEND                                                            │
 │                                                                     │
 │  1. Verify Bearer token → extract promoter_id                       │
 │  2. CryptoManager.decrypt(payload) → PunchOutPayload                │
 │  3. Validate:                                                       │
 │     • attendance_id belongs to this promoter                        │
 │     • shift not already synced (punch_out_timestamp is NULL)        │
 │     • each EAN exists in products table                             │
 │     • closing_qty == opening_qty + received_qty - sold_qty          │
 │     • sold_qty <= opening_qty + received_qty                        │
 │  4. Single DB transaction:                                          │
 │     • INSERT rows into stock_summary                                │
 │     • UPDATE attendance (punch_out_timestamp, lat, lng, store)      │
 │  5. Publish geocoding task to Kafka (resolve store name)            │
 │  6. Return encrypted 200 OK                                         │
 └─────────────────────────────────────────────────────────────────────┘

 Sync Payload (decrypted):
 {
   "latitude": 28.613939,
   "longitude": 77.209021,
   "attendance_id": "a3b8f7e2-...",
   "stock_report": [
     {
       "ean": "8903363008848",
       "article_code": "100004790",
       "description": "Healthy Choice Roasted Sunflower Seeds 100 g",
       "opening_qty": 20,
       "received_qty": 15,
       "sold_qty": 8,
       "closing_qty": 27
     }
   ]
 }
```

---

## Class-by-Class Architecture

### 1. CryptoManager

```
CryptoManager
├── Constructor(hexKey: String)
│   └── Converts 64-char hex → 32-byte SecretKeySpec
│   └── Store key in Android Keystore, NEVER in SharedPreferences
│
├── encrypt(plaintext: String) → String (base64)
│   ├── Generate 12-byte random nonce (SecureRandom)
│   ├── AES-256-GCM encrypt with nonce
│   ├── Concat: nonce (12) + ciphertext (N) + authTag (16)
│   └── Base64 encode → return
│
├── decrypt(base64Payload: String) → String
│   ├── Base64 decode → byte[]
│   ├── Split: nonce = [0:12], ciphertext+tag = [12:]
│   ├── AES-256-GCM decrypt
│   └── Return plaintext string
│
├── encryptJson(JSONObject/JSONArray) → String (base64)
│   └── JSON.toString() → encrypt()
│
└── decryptJson(base64Payload: String) → JSONObject
    └── decrypt() → new JSONObject(plaintext)
```

**Android APIs used**: `javax.crypto.Cipher`, `javax.crypto.spec.GCMParameterSpec`, `javax.crypto.spec.SecretKeySpec`, `java.security.SecureRandom`, `android.util.Base64`

### 2. SecureFileManager

```
SecureFileManager
├── Constructor(context: Context, crypto: CryptoManager)
│   └── baseDir = context.getFilesDir()
│
├── write(filename: String, data: JSONArray) → void
│   ├── crypto.encryptJson(data) → base64 string
│   └── FileOutputStream → write base64 to file
│
├── read(filename: String) → JSONArray | null
│   ├── Check file.exists()
│   ├── FileInputStream → read base64 string
│   └── crypto.decryptJson(base64) → JSONArray
│
├── delete(filename: String) → void
│   └── file.delete()
│
├── exists(filename: String) → boolean
│   └── file.exists()
│
└── clearShiftData() → void
    ├── delete("opening_stock.enc")
    ├── delete("received_stock.enc")
    └── delete("sales.enc")
    // products.enc is NOT deleted
```

### 3. ProductCatalog

```
ProductCatalog
├── Constructor(fileManager: SecureFileManager)
│   └── cache: List<Product> = null  (in-memory)
│
├── save(products: JSONArray) → void
│   ├── fileManager.write("products.enc", products)
│   └── Populate in-memory cache
│
├── load() → List<Product>
│   ├── If cache != null → return cache
│   └── fileManager.read("products.enc") → parse → cache → return
│
├── findByEan(ean: String) → Product | null
│   └── load() → iterate → match ean → return
│
├── findByArticle(articleCode: String) → Product | null
│   └── load() → iterate → match article_code → return
│
├── getAll() → List<Product>
│   └── load()
│
└── clearCache() → void
    └── cache = null  (call on logout)
```

### 4. StockManager (Core)

```
StockManager
├── Constructor(fileManager: SecureFileManager, catalog: ProductCatalog)
│
│ ── OPENING STOCK ──
├── addOpeningStock(ean: String, qty: int) → void
│   ├── catalog.findByEan(ean) → validate exists
│   ├── Read opening_stock.enc → JSONArray
│   ├── If EAN exists in array → update qty
│   ├── If EAN not in array → append new entry
│   └── Write back to opening_stock.enc
│
├── getOpeningStock() → List<OpeningStockEntry>
│   └── Read + decrypt opening_stock.enc
│
│ ── RECEIVED STOCK ──
├── addReceivedStock(ean: String, qty: int) → void
│   ├── catalog.findByEan(ean) → validate exists
│   ├── Read received_stock.enc → JSONArray
│   ├── ALWAYS append (multiple deliveries allowed)
│   └── Write back to received_stock.enc
│
├── getTotalReceived(ean: String) → int
│   └── Read received_stock.enc → filter by ean → SUM(qty_received)
│
│ ── SALES ──
├── addSale(ean: String, qty: int) → void
│   ├── catalog.findByEan(ean) → validate exists
│   ├── getInHandForEan(ean) → validate qty <= in_hand
│   │   └── If qty > in_hand → throw "Insufficient stock"
│   ├── Read sales.enc → JSONArray
│   ├── Append sale entry
│   └── Write back to sales.enc
│
├── getTotalSold(ean: String) → int
│   └── Read sales.enc → filter by ean → SUM(qty_sold)
│
│ ── IN-HAND (computed, NEVER stored) ──
├── getInHandForEan(ean: String) → int
│   ├── opening = getOpeningStock() → find ean → opening_qty
│   ├── received = getTotalReceived(ean)
│   ├── sold = getTotalSold(ean)
│   └── return opening + received - sold
│
├── getAllInHandStock() → List<InHandStock>
│   ├── Read all 3 .enc files
│   ├── For each EAN in opening stock:
│   │   ├── total_received = SUM(received WHERE ean)
│   │   ├── total_sold = SUM(sold WHERE ean)
│   │   └── in_hand = opening + received - sold
│   └── Return list of InHandStock objects
│
│ ── SYNC PAYLOAD ──
├── buildStockReport() → List<StockReportItem>
│   ├── getAllInHandStock()
│   └── Map each → StockReportItem with closing_qty = in_hand_qty
│
│ ── SHIFT STATE ──
└── hasActiveShift() → boolean
    └── fileManager.exists("opening_stock.enc")
```

### 5. SyncService

```
SyncService
├── Constructor(crypto, fileManager, stockManager)
│
└── syncAndPunchOut(lat, lng, attendanceId, accessToken, callback) → SyncResult
    │
    ├── Step 1: stockManager.buildStockReport() → List<StockReportItem>
    │   └── If empty → return error "No stock data"
    │
    ├── Step 2: Build PunchOutPayload JSON
    │   { latitude, longitude, attendance_id, stock_report[] }
    │
    ├── Step 3: crypto.encryptJson(payload) → base64
    │
    ├── Step 4: POST /api/1.4 with retries
    │   ├── Headers: Content-Type: application/json
    │   │            Authorization: Bearer <accessToken>
    │   ├── Body: { "payload": "<base64>" }
    │   │
    │   ├── 200 OK → SUCCESS
    │   │   └── fileManager.clearShiftData()
    │   │       (delete opening_stock.enc, received_stock.enc, sales.enc)
    │   │
    │   ├── 409 Conflict → ALREADY SYNCED (treat as success)
    │   │   └── fileManager.clearShiftData()
    │   │
    │   ├── 401 Unauthorized → TOKEN EXPIRED
    │   │   └── Return error (caller should refresh token)
    │   │
    │   └── Other error → RETRY
    │       ├── Retry 1: wait 5 seconds
    │       ├── Retry 2: wait 15 seconds
    │       ├── Retry 3: wait 45 seconds
    │       ├── Retry 4: wait 2 minutes
    │       └── Retry 5: wait 5 minutes
    │
    └── All retries failed:
        ├── .enc files are NOT deleted (data is safe)
        └── Return error "Sync failed. Try again later."
```

### 6. ApiClient

```
ApiClient
├── Constructor(crypto: CryptoManager)
│
└── login(email, password, lat, lng, catalog) → LoginResponse
    │
    ├── Step 1: Build request JSON
    │   { "email": "...", "password": "...", "latitude": ..., "longitude": ... }
    │
    ├── Step 2: crypto.encryptJson(request) → base64
    │
    ├── Step 3: POST /api/1.1
    │   Body: { "payload": "<base64>" }
    │
    ├── Step 4: Receive { "payload": "<base64>" }
    │
    ├── Step 5: crypto.decryptJson(response.payload) → JSONObject
    │   Contains: tokens + promoter_name + products[]
    │
    ├── Step 6: Extract products array → catalog.save(products)
    │   Writes products.enc to local storage
    │
    └── Step 7: Return LoginResponse
        (caller stores tokens in EncryptedSharedPreferences)
```

---

## App Lifecycle Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  APP LAUNCH                                                       │
│                                                                   │
│  StockManager.hasActiveShift()?                                   │
│       │                                                           │
│       ├── YES (opening_stock.enc exists)                          │
│       │   └── Previous shift was interrupted (crash/restart)      │
│       │       → Resume: Go to Stock Dashboard                     │
│       │       → All .enc files are intact                         │
│       │                                                           │
│       └── NO                                                      │
│           └── Fresh start → Go to Login Screen                    │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  LOGIN SCREEN                                                     │
│                                                                   │
│  Email + Password + GPS auto-capture                              │
│  ApiClient.login() → tokens + products                            │
│  Save tokens → EncryptedSharedPreferences                         │
│  Save products → products.enc                                     │
│  Navigate → Stock Take                                            │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  STOCK TAKE SCREEN                                                │
│                                                                   │
│  Scan barcode → ProductCatalog.findByEan()                        │
│  Enter qty → StockManager.addOpeningStock()                       │
│  When done → Navigate to Dashboard                                │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  STOCK DASHBOARD (main screen during shift)                       │
│                                                                   │
│  StockManager.getAllInHandStock() → populate RecyclerView          │
│                                                                   │
│  [+ Add Sale]        → record sale, refresh dashboard             │
│  [+ Received Stock]  → record delivery, refresh dashboard         │
│  [Sync + Punch Out]  → go to sync flow                            │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│  SYNC + PUNCH OUT                                                 │
│                                                                   │
│  Show loading spinner                                             │
│  SyncService.syncAndPunchOut()                                    │
│    ├── Success → delete .enc files → navigate to Login            │
│    └── Failure → show error + "Retry" button                      │
│        (.enc files preserved — no data loss)                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## Token & Key Storage

| Data | Storage | Android API |
|------|---------|-------------|
| AES key (64-char hex) | Android Keystore | `KeyStore`, `KeyGenParameterSpec` |
| Access token (JWT) | EncryptedSharedPreferences | `androidx.security.crypto.EncryptedSharedPreferences` |
| Refresh token (JWT) | EncryptedSharedPreferences | same |
| Attendance ID | EncryptedSharedPreferences | same |
| Promoter name | EncryptedSharedPreferences | same |
| Product catalog | Encrypted file (products.enc) | `context.getFilesDir()` + `CryptoManager` |
| Shift data files | Encrypted files (.enc) | same |

**NEVER** store the AES key or tokens in plain `SharedPreferences`.

---

## Security Checklist

- [ ] AES key stored in Android Keystore, never hardcoded
- [ ] All .enc files use AES-256-GCM with random 12-byte nonce per write
- [ ] Tokens stored in EncryptedSharedPreferences
- [ ] Barcode entry validated against products.enc — no free-text EAN allowed
- [ ] Sale qty validated: `qty_sold <= in_hand_qty` before write
- [ ] .enc files deleted ONLY after backend returns 200 OK
- [ ] App-private storage (`getFilesDir()`) — no external storage used
- [ ] HTTPS for all API calls
- [ ] ProGuard/R8 obfuscation enabled for release builds
