# Inward Module — Frontend Integration Guide

> **Backend base URL**: `NEXT_PUBLIC_API_URL` (e.g. `http://localhost:8000`)
> **Companies**: `"CFPL"` | `"CDPL"`
> **Auth**: Bearer token in `Authorization` header

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [DB Tables (v2)](#2-db-tables-v2)
3. [API Endpoints](#3-api-endpoints)
   - [3.1 PO PDF Extraction](#31-po-pdf-extraction)
   - [3.2 SKU Lookup](#32-sku-lookup)
   - [3.3 SKU Cascading Dropdown](#33-sku-cascading-dropdown)
   - [3.4 SKU Global Search](#34-sku-global-search)
   - [3.5 SKU ID Lookup](#35-sku-id-lookup)
   - [3.6 List Inward Records](#36-list-inward-records)
   - [3.7 Create Inward Entry](#37-create-inward-entry)
   - [3.8 Get Inward Detail](#38-get-inward-detail)
   - [3.9 Update Inward Entry](#39-update-inward-entry)
   - [3.10 Approve / Reject](#310-approve--reject)
   - [3.11 Delete Inward Entry](#311-delete-inward-entry)
4. [Complete User Flow](#4-complete-user-flow)
5. [Field Reference](#5-field-reference)
6. [Old → New Migration Map](#6-old--new-migration-map)

---

## 1. Architecture Overview

The new inward module has a **two-phase workflow**:

```
Phase 1: DATA ENTRY (status = "pending")
  Upload PO PDF → Extract fields → SKU Lookup → Create Entry

Phase 2: APPROVAL (status → "approved" / "rejected")
  Approver opens pending entry → fills transport/docs/weights → Approve or Reject
```

**Key change from old backend**: Entries now have a `status` field (`pending` → `approved` | `rejected`). Only `pending` entries can be edited or approved.

---

## 2. DB Tables (v2)

Tables are prefixed by company: `cfpl_transactions_v2`, `cdpl_transactions_v2`, etc.

### `{prefix}_transactions_v2`

| Column | Type | Notes |
|--------|------|-------|
| `transaction_no` | `TEXT PK` | e.g. `TR-202602171430` |
| `entry_date` | `TIMESTAMPTZ` | Entry date |
| `status` | `TEXT` | `pending` / `approved` / `rejected` |
| `vendor_supplier_name` | `TEXT` | From PO extract |
| `customer_party_name` | `TEXT` | From PO extract |
| `source_location` | `TEXT` | Supplier address |
| `destination_location` | `TEXT` | Delivery address |
| `po_number` | `TEXT` | From PO extract |
| `purchased_by` | `TEXT` | Indentor/purchaser |
| `total_amount` | `DECIMAL(18,2)` | PO total |
| `tax_amount` | `DECIMAL(18,2)` | PO tax |
| `discount_amount` | `DECIMAL(18,2)` | PO discount |
| `po_quantity` | `DECIMAL(18,3)` | Total PO quantity in kg |
| `currency` | `TEXT` | Default `INR` |
| `vehicle_number` | `TEXT` | Filled at approval |
| `transporter_name` | `TEXT` | Filled at approval |
| `lr_number` | `TEXT` | Filled at approval |
| `challan_number` | `TEXT` | Filled at approval |
| `invoice_number` | `TEXT` | Filled at approval |
| `grn_number` | `TEXT` | Filled at approval |
| `grn_quantity` | `DECIMAL(18,3)` | Filled at approval |
| `system_grn_date` | `TIMESTAMPTZ` | Filled at approval |
| `service_invoice_number` | `TEXT` | Filled at approval |
| `dn_number` | `TEXT` | Filled at approval |
| `approval_authority` | `TEXT` | Filled at approval |
| `remark` | `TEXT` | General remark |
| `approved_by` | `TEXT` | Auto-set on approve/reject |
| `approved_at` | `TIMESTAMPTZ` | Auto-set on approve/reject |
| `rejection_remark` | `TEXT` | Required for rejection |

### `{prefix}_articles_v2`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | Auto |
| `transaction_no` | `TEXT FK` | Links to transaction |
| `item_description` | `TEXT` | From PO extract |
| `po_weight` | `DECIMAL(18,3)` | From PO extract (kg) |
| `sku_id` | `INT` | From SKU lookup |
| `material_type` | `TEXT` | From SKU lookup |
| `item_category` | `TEXT` | From SKU lookup |
| `sub_category` | `TEXT` | From SKU lookup |
| `quality_grade` | `TEXT` | Filled at approval |
| `uom` | `TEXT` | Filled at approval |
| `po_quantity` | `DECIMAL(18,3)` | Filled at approval |
| `units` | `TEXT` | Filled at approval |
| `quantity_units` | `DECIMAL(18,3)` | Filled at approval |
| `net_weight` | `DECIMAL(18,3)` | Filled at approval |
| `total_weight` | `DECIMAL(18,3)` | Filled at approval (gross) |
| `lot_number` | `TEXT` | Filled at approval |
| `manufacturing_date` | `TEXT` | Filled at approval |
| `expiry_date` | `TEXT` | Filled at approval |
| `unit_rate` | `DECIMAL(18,2)` | Filled at approval |
| `total_amount` | `DECIMAL(18,2)` | Filled at approval |
| `carton_weight` | `DECIMAL(18,3)` | Filled at approval |

> **Unique constraint**: `(transaction_no, item_description)`

### `{prefix}_boxes_v2`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PK` | Auto |
| `transaction_no` | `TEXT FK` | Links to transaction |
| `article_description` | `TEXT` | Must match an article's `item_description` |
| `box_number` | `INT` | >= 1 |
| `net_weight` | `DECIMAL(18,3)` | |
| `gross_weight` | `DECIMAL(18,3)` | |
| `lot_number` | `TEXT` | |
| `count` | `INT` | >= 0 |

> **Unique constraint**: `(transaction_no, article_description, box_number)`

---

## 3. API Endpoints

All endpoints are under `/inward`.

### 3.1 PO PDF Extraction

**Upload a PO PDF and extract structured fields via Claude Sonnet.**

```
POST /inward/extract-po
Content-Type: multipart/form-data
```

| Form Field | Type | Required |
|-----------|------|----------|
| `file` | PDF file | Yes |

**Response** `200`:
```json
{
  "supplier_name": "VENDOR NAME",
  "source_location": "CITY, STATE",
  "customer_name": "BUYER NAME",
  "destination_location": "DELIVERY ADDRESS",
  "po_number": "PO-2026-001",
  "purchased_by": "INDENTOR NAME",
  "total_amount": 125000.00,
  "tax_amount": 22500.00,
  "discount_amount": 0.00,
  "po_quantity": 5000.0,
  "currency": "INR",
  "articles": [
    { "item_description": "RAW MATERIAL A", "po_weight": 2500.0 },
    { "item_description": "RAW MATERIAL B", "po_weight": 2500.0 }
  ]
}
```

**Frontend mapping**: Use `supplier_name` → `vendor_supplier_name`, `customer_name` → `customer_party_name` when building the create payload.

---

### 3.2 SKU Lookup

**Lookup SKU details (material_type, item_category, sub_category) by item description.**

```
POST /inward/sku-lookup/{company}
Content-Type: application/json
```

**Path params**: `company` = `CFPL` | `CDPL`

**Request body**:
```json
{ "item_description": "RAW MATERIAL A" }
```

**Response** `200`:
```json
{
  "sku_id": 42,
  "item_description": "RAW MATERIAL A",
  "material_type": "RAW MATERIAL",
  "item_category": "CHEMICALS",
  "sub_category": "ACIDS"
}
```

> If the SKU is not found, `sku_id`, `material_type`, `item_category`, `sub_category` will be `null`.

---

### 3.3 SKU Cascading Dropdown

**Cascading dropdown for manual article entry. Provides hierarchical SKU selection: material_type → item_category → sub_category → item_description.**

```
GET /inward/sku/dropdown?company=CFPL
```

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `company` | `CFPL\|CDPL` | required | Company code |
| `material_type` | `string` | — | Filter by material type |
| `item_category` | `string` | — | Filter by item category (requires `material_type`) |
| `sub_category` | `string` | — | Filter by sub category (requires `material_type` + `item_category`) |
| `item_description` | `string` | — | Auto-resolve category/sub from item name |
| `search` | `string` | — | Search within item descriptions |
| `limit` | `int` | `200` | Pagination limit (max 10000) |
| `offset` | `int` | `0` | Pagination offset |

**Response** `200`:
```json
{
  "company": "CFPL",
  "selected": {
    "material_type": "RAW MATERIAL",
    "item_description": null,
    "item_category": "CHEMICALS",
    "sub_category": null
  },
  "auto_selection": {
    "resolved_from_item": {
      "material_type": null,
      "item_category": null,
      "sub_category": null
    }
  },
  "options": {
    "material_types": ["FINISHED GOODS", "PACKING MATERIAL", "RAW MATERIAL"],
    "item_categories": ["CHEMICALS", "OILS", "POLYMERS"],
    "sub_categories": [],
    "item_descriptions": [],
    "item_ids": []
  },
  "meta": {
    "total_material_types": 3,
    "total_item_descriptions": 0,
    "total_categories": 3,
    "total_sub_categories": 0,
    "limit": 200,
    "offset": 0,
    "sort": "alpha",
    "search": null
  }
}
```

**Cascading logic**:
1. No params → returns all `material_types`
2. `material_type` set → also returns `item_categories` for that material type
3. `material_type` + `item_category` set → also returns `sub_categories`
4. All three set → also returns `item_descriptions` + `item_ids`
5. `item_description` set → auto-resolves `material_type`, `item_category`, `sub_category` in `auto_selection.resolved_from_item`

---

### 3.4 SKU Global Search

**Search across ALL item descriptions regardless of hierarchy. Useful for quick item lookup.**

```
GET /inward/sku/global-search?company=CFPL&search=copper
```

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `company` | `CFPL\|CDPL` | required | Company code |
| `search` | `string` | — | Partial match on item descriptions |
| `limit` | `int` | `200` | Max results (max 10000) |
| `offset` | `int` | `0` | Pagination offset |

**Response** `200`:
```json
{
  "company": "CFPL",
  "items": [
    {
      "id": 42,
      "item_description": "COPPER SULPHATE",
      "material_type": "RAW MATERIAL",
      "group": "CHEMICALS",
      "sub_group": "SULPHATES"
    }
  ],
  "meta": {
    "total_items": 1,
    "limit": 200,
    "offset": 0,
    "search": "copper",
    "has_more": false
  }
}
```

> `group` = `item_category`, `sub_group` = `sub_category` (aliases for frontend compatibility).

---

### 3.5 SKU ID Lookup

**Get SKU ID for a specific item description (case-insensitive match).**

```
GET /inward/sku/id?company=CFPL&item_description=COPPER%20SULPHATE
```

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `company` | `CFPL\|CDPL` | required | Company code |
| `item_description` | `string` | required | Item description to look up |
| `material_type` | `string` | — | Optional filter |
| `item_category` | `string` | — | Optional filter |
| `sub_category` | `string` | — | Optional filter |

**Response** `200`:
```json
{
  "sku_id": 42,
  "id": 42,
  "item_description": "COPPER SULPHATE",
  "material_type": "RAW MATERIAL",
  "group": "CHEMICALS",
  "sub_group": "SULPHATES",
  "item_category": "CHEMICALS",
  "sub_category": "SULPHATES",
  "company": "CFPL"
}
```

> If any field is `"other"` (case-insensitive), returns `sku_id: null` and `id: null`.
> **404** if no matching SKU is found.

---

### 3.6 List Inward Records

**List inward records with search, date filtering, status filtering, and pagination.**

Two equivalent endpoints (use either):

```
GET /inward?company=CFPL&status=pending&page=1&per_page=20
GET /inward/CFPL?status=pending&page=1&per_page=20
```

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `company` | `CFPL\|CDPL` | required | Company code |
| `page` | `int` | `1` | Page number (1-based) |
| `per_page` | `int` | `20` | Items per page (max 1000) |
| `status` | `string` | all | `pending`, `approved`, `rejected` |
| `search` | `string` | — | Full-text search across all fields |
| `from_date` | `YYYY-MM-DD` | — | Filter from date |
| `to_date` | `YYYY-MM-DD` | — | Filter to date |
| `sort_by` | `string` | `entry_date` | `entry_date`, `transaction_no`, `invoice_number`, `po_number` |
| `sort_order` | `string` | `desc` | `asc` or `desc` |

**Response** `200`:
```json
{
  "records": [
    {
      "transaction_no": "TR-202602171430",
      "entry_date": "2026-02-17",
      "status": "pending",
      "invoice_number": null,
      "po_number": "PO-2026-001",
      "vendor_supplier_name": "VENDOR NAME",
      "customer_party_name": "BUYER NAME",
      "total_amount": 125000.00,
      "item_descriptions": ["RAW MATERIAL A", "RAW MATERIAL B"],
      "quantities_and_uoms": ["2500 KG", "2500 KG"]
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 20
}
```

> **IMPORTANT**: The list uses `transaction_no` (not `transaction_id`). Use this value to navigate to the detail page.

---

### 3.7 Create Inward Entry

**Create a new inward entry (status = "pending").**

```
POST /inward
Content-Type: application/json
```

**Request body**:
```json
{
  "company": "CFPL",
  "transaction": {
    "transaction_no": "TR-202602171430",
    "entry_date": "2026-02-17",
    "vendor_supplier_name": "VENDOR NAME",
    "customer_party_name": "BUYER NAME",
    "source_location": "SUPPLIER CITY",
    "destination_location": "DELIVERY CITY",
    "po_number": "PO-2026-001",
    "purchased_by": "INDENTOR",
    "total_amount": 125000.00,
    "tax_amount": 22500.00,
    "discount_amount": 0.00,
    "po_quantity": 5000.0,
    "currency": "INR"
  },
  "articles": [
    {
      "transaction_no": "TR-202602171430",
      "item_description": "RAW MATERIAL A",
      "po_weight": 2500.0,
      "sku_id": 42,
      "material_type": "RAW MATERIAL",
      "item_category": "CHEMICALS",
      "sub_category": "ACIDS"
    }
  ],
  "boxes": [
    {
      "transaction_no": "TR-202602171430",
      "article_description": "RAW MATERIAL A",
      "box_number": 1,
      "net_weight": 25.0,
      "gross_weight": 26.5,
      "lot_number": "LOT-001",
      "count": 1
    }
  ]
}
```

**Response** `201`:
```json
{
  "status": "ok",
  "transaction_no": "TR-202602171430",
  "company": "CFPL"
}
```

**Errors**: `400` (missing transaction_no, mismatched transaction_no), `409` (duplicate transaction_no)

---

### 3.8 Get Inward Detail

**Retrieve full transaction + articles + boxes.**

```
GET /inward/{company}/{transaction_no}
```

**Response** `200`:
```json
{
  "company": "CFPL",
  "transaction": {
    "transaction_no": "TR-202602171430",
    "entry_date": "2026-02-17",
    "status": "pending",
    "vendor_supplier_name": "VENDOR NAME",
    "customer_party_name": "BUYER NAME",
    "source_location": "SUPPLIER CITY",
    "destination_location": "DELIVERY CITY",
    "po_number": "PO-2026-001",
    "purchased_by": "INDENTOR",
    "total_amount": 125000.00,
    "vehicle_number": null,
    "transporter_name": null,
    "...": "all transaction fields"
  },
  "articles": [
    {
      "id": 1,
      "transaction_no": "TR-202602171430",
      "item_description": "RAW MATERIAL A",
      "po_weight": 2500.0,
      "sku_id": 42,
      "sku_material_type": "RAW MATERIAL",
      "material_type": "RAW MATERIAL",
      "item_category": "CHEMICALS",
      "sub_category": "ACIDS",
      "quality_grade": null,
      "uom": null,
      "quantity_units": null,
      "net_weight": null,
      "total_weight": null,
      "...": "all article fields"
    }
  ],
  "boxes": [
    {
      "id": 1,
      "transaction_no": "TR-202602171430",
      "article_description": "RAW MATERIAL A",
      "box_number": 1,
      "net_weight": 25.0,
      "gross_weight": 26.5,
      "lot_number": "LOT-001",
      "count": 1
    }
  ]
}
```

> Dates are returned as `YYYY-MM-DD` strings (normalized from DB timestamps).

---

### 3.9 Update Inward Entry

**Update a pending entry. Only `status = "pending"` entries can be updated.**

```
PUT /inward/{company}/{transaction_no}
Content-Type: application/json
```

**Request body**: Same shape as [Create](#34-create-inward-entry). The `transaction_no` in the payload must match the URL param.

**Response** `200`:
```json
{
  "status": "updated",
  "transaction_no": "TR-202602171430",
  "company": "CFPL",
  "articles_count": 2,
  "boxes_count": 5
}
```

**Errors**: `400` (non-pending status, mismatched transaction_no), `404` (not found)

> Update does a full replace of articles and boxes (delete old → insert new).

---

### 3.10 Approve / Reject

**Approve or reject a pending inward entry. Approver fills in transport/docs/weight fields.**

```
PUT /inward/{company}/{transaction_no}/approve
Content-Type: application/json
```

#### Approve

```json
{
  "status": "approved",
  "approved_by": "admin@company.com",
  "transaction": {
    "vehicle_number": "MH12AB1234",
    "transporter_name": "XYZ Transport",
    "lr_number": "LR-001",
    "challan_number": "CH-001",
    "invoice_number": "INV-2026-001",
    "grn_number": "GRN-001",
    "grn_quantity": 4950.0,
    "system_grn_date": "2026-02-17",
    "service_invoice_number": null,
    "dn_number": null,
    "approval_authority": "WAREHOUSE MANAGER",
    "remark": "Received in good condition"
  },
  "articles": [
    {
      "item_description": "RAW MATERIAL A",
      "quality_grade": "A",
      "uom": "KG",
      "po_quantity": 2500.0,
      "quantity_units": 2450.0,
      "net_weight": 2450.0,
      "total_weight": 2500.0,
      "lot_number": "LOT-001",
      "manufacturing_date": "2026-01-15",
      "expiry_date": "2027-01-15",
      "unit_rate": 50.0,
      "total_amount": 122500.0,
      "carton_weight": 25.0
    }
  ],
  "boxes": [
    {
      "article_description": "RAW MATERIAL A",
      "box_number": 1,
      "net_weight": 24.5,
      "gross_weight": 25.0,
      "lot_number": "LOT-001",
      "count": 1
    }
  ]
}
```

> Articles are **merged** by `item_description` (only non-null fields are updated).
> Boxes are **replaced** entirely (old deleted, new inserted).
> `transaction` fields are appended to existing transaction.

#### Reject

```json
{
  "status": "rejected",
  "approved_by": "admin@company.com",
  "rejection_remark": "Quality does not meet standards"
}
```

**Response** `200`:
```json
{
  "status": "approved",
  "transaction_no": "TR-202602171430",
  "company": "CFPL",
  "approved_by": "admin@company.com",
  "approved_at": "2026-02-17 14:30:00"
}
```

**Errors**: `400` (already approved/rejected), `404` (not found)

---

### 3.11 Delete Inward Entry

**Delete a transaction and all related articles/boxes.**

```
DELETE /inward/{company}/{transaction_no}
```

**Response** `200`:
```json
{
  "status": "deleted",
  "transaction_no": "TR-202602171430",
  "company": "CFPL",
  "deleted_counts": {
    "transaction": 1,
    "articles": 2,
    "boxes": 10
  }
}
```

---

## 4. Complete User Flow

### Phase 1: Data Entry (Gate Keeper / Store Person)

**Option A — PO Upload flow (auto-fill)**:
```
Step 1 — Upload PO PDF
  POST /inward/extract-po  (multipart/form-data with PDF)
  → Returns: vendor, customer, po_number, articles[{item_description, po_weight}]

Step 2 — SKU Lookup (per article from PO extract)
  POST /inward/sku-lookup/{company}  (body: {item_description})
  → Returns: sku_id, material_type, item_category, sub_category

Step 3 — Build form & submit
  POST /inward  (JSON payload)
  → Entry created with status = "pending"
```

**Option B — Manual entry flow (cascading dropdowns)**:
```
Step 1 — Load material types
  GET /inward/sku/dropdown?company=CFPL
  → Returns: material_types list

Step 2 — User selects material_type → load categories
  GET /inward/sku/dropdown?company=CFPL&material_type=RAW MATERIAL
  → Returns: item_categories list

Step 3 — User selects item_category → load sub-categories
  GET /inward/sku/dropdown?company=CFPL&material_type=...&item_category=...
  → Returns: sub_categories list

Step 4 — User selects sub_category → load item descriptions
  GET /inward/sku/dropdown?company=CFPL&material_type=...&item_category=...&sub_category=...
  → Returns: item_descriptions + item_ids

Step 5 — Get SKU ID for selected item
  GET /inward/sku/id?company=CFPL&item_description=...
  → Returns: sku_id, material_type, item_category, sub_category

Step 6 — Build form & submit
  POST /inward  (JSON payload)
  → Entry created with status = "pending"
```

**Option C — Quick search (bypass hierarchy)**:
```
Step 1 — Search for item
  GET /inward/sku/global-search?company=CFPL&search=copper
  → Returns: items[{id, item_description, material_type, group, sub_group}]

Step 2 — Get full SKU details
  GET /inward/sku/id?company=CFPL&item_description=COPPER SULPHATE
  → Returns: sku_id + all category fields

Step 3 — Build form & submit
  POST /inward  (JSON payload)
```

### Phase 2: Approval (Approver / Warehouse Manager)

```
Step 1 — List pending entries
  GET /inward/{company}?status=pending

Step 2 — Open detail
  GET /inward/{company}/{transaction_no}
  → Shows transaction, articles (from PO + SKU), boxes

Step 3 — Fill approval fields
  - Transaction: vehicle_number, transporter_name, lr_number, challan_number,
    invoice_number, grn_number, grn_quantity, system_grn_date, etc.
  - Articles: quality_grade, uom, quantity_units, net_weight, total_weight,
    lot_number, manufacturing_date, expiry_date, unit_rate, total_amount, carton_weight
  - Boxes: update net_weight, gross_weight, lot_number, count per box

Step 4 — Submit approval
  PUT /inward/{company}/{transaction_no}/approve
  → status becomes "approved" (or "rejected")
```

### Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     PHASE 1: DATA ENTRY                      │
│                                                              │
│  ┌──────────┐    ┌────────────┐    ┌──────────┐             │
│  │ Upload   │───>│ PO Extract │───>│ SKU      │             │
│  │ PO PDF   │    │ (Claude)   │    │ Lookup   │             │
│  └──────────┘    └────────────┘    └──────────┘             │
│                        │                 │                    │
│                        ▼                 ▼                    │
│              ┌─────────────────────────────────┐             │
│              │  Create Inward (status=pending)  │             │
│              │  transaction + articles + boxes   │             │
│              └─────────────────────────────────┘             │
│                              │                               │
└──────────────────────────────┼───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                     PHASE 2: APPROVAL                        │
│                                                              │
│  ┌────────────┐    ┌─────────────┐    ┌──────────────────┐  │
│  │ List       │───>│ View Detail │───>│ Fill transport/  │  │
│  │ Pending    │    │             │    │ docs/weights     │  │
│  └────────────┘    └─────────────┘    └──────────────────┘  │
│                                              │               │
│                                    ┌─────────┴────────┐     │
│                                    ▼                  ▼     │
│                              ┌──────────┐      ┌──────────┐ │
│                              │ Approve  │      │ Reject   │ │
│                              │(approved)│      │(rejected)│ │
│                              └──────────┘      └──────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. Field Reference

### Fields filled at CREATION time (Phase 1)

#### Transaction fields (from PO extract)
| Field | Source | Required |
|-------|--------|----------|
| `transaction_no` | Auto-generated `TR-YYYYMMDDHHmm` | Yes |
| `entry_date` | Today's date | Yes |
| `vendor_supplier_name` | PO extract `supplier_name` | No |
| `customer_party_name` | PO extract `customer_name` | No |
| `source_location` | PO extract `source_location` | No |
| `destination_location` | PO extract `destination_location` | No |
| `po_number` | PO extract `po_number` | No |
| `purchased_by` | PO extract `purchased_by` | No |
| `total_amount` | PO extract `total_amount` | No |
| `tax_amount` | PO extract `tax_amount` | No |
| `discount_amount` | PO extract `discount_amount` | No |
| `po_quantity` | PO extract `po_quantity` | No |
| `currency` | PO extract `currency` (default `INR`) | No |

#### Article fields (from PO extract + SKU lookup)
| Field | Source | Required |
|-------|--------|----------|
| `transaction_no` | Same as transaction | Yes |
| `item_description` | PO extract articles[].item_description | Yes |
| `po_weight` | PO extract articles[].po_weight | No |
| `sku_id` | SKU lookup response | No |
| `material_type` | SKU lookup response | No |
| `item_category` | SKU lookup response | No |
| `sub_category` | SKU lookup response | No |

### Fields filled at APPROVAL time (Phase 2)

#### Transaction fields (by approver)
| Field | Description |
|-------|-------------|
| `vehicle_number` | Vehicle registration |
| `transporter_name` | Transport company |
| `lr_number` | Lorry receipt number |
| `challan_number` | Delivery challan |
| `invoice_number` | Supplier invoice number |
| `grn_number` | Goods receipt note |
| `grn_quantity` | GRN quantity (decimal, 3 places) |
| `system_grn_date` | GRN date |
| `service_invoice_number` | Service invoice (if applicable) |
| `dn_number` | Delivery note number |
| `approval_authority` | Authority name |
| `remark` | General remark |

#### Article fields (by approver, matched by `item_description`)
| Field | Type | Description |
|-------|------|-------------|
| `quality_grade` | `string` | Quality grade (A, B, C) |
| `uom` | `string` | Unit of measure (KG, BOX, CARTON, BAG) |
| `po_quantity` | `decimal(18,3)` | PO ordered quantity |
| `units` | `string` | Unit label |
| `quantity_units` | `decimal(18,3)` | Received quantity |
| `net_weight` | `decimal(18,3)` | Net weight |
| `total_weight` | `decimal(18,3)` | Gross/total weight |
| `lot_number` | `string` | Lot/batch number |
| `manufacturing_date` | `string` | YYYY-MM-DD |
| `expiry_date` | `string` | YYYY-MM-DD |
| `unit_rate` | `decimal(18,2)` | Rate per unit |
| `total_amount` | `decimal(18,2)` | Total amount |
| `carton_weight` | `decimal(18,3)` | Carton weight |

#### Box fields (by approver)
| Field | Type | Description |
|-------|------|-------------|
| `article_description` | `string` | Must match an article's `item_description` |
| `box_number` | `int >= 1` | Sequential box number |
| `net_weight` | `decimal(18,3)` | Box net weight |
| `gross_weight` | `decimal(18,3)` | Box gross weight |
| `lot_number` | `string` | Lot number |
| `count` | `int >= 0` | Item count in box |

---

## 6. Old → New Migration Map

### Removed fields (no longer in v2)
| Old Field | Table | Notes |
|-----------|-------|-------|
| `batch_number` | articles | Use `lot_number` instead |
| `item_code` | articles | Removed |
| `hsn_code` | articles | Removed |
| `packaging_type` | articles | Removed |
| `import_date` | articles | Removed |
| `issuance_date` | articles | Removed |
| `job_card_no` | articles | Removed |
| `issuance_quantity` | articles | Removed |
| `tax_amount` | articles | Moved to transaction level only |
| `discount_amount` | articles | Moved to transaction level only |
| `currency` | articles | Moved to transaction level only |
| `received_quantity` | transaction | Renamed to `po_quantity` |
| `purchase_by` | transaction | Renamed to `purchased_by` |

### Added fields (new in v2)
| New Field | Table | Notes |
|-----------|-------|-------|
| `status` | transaction | `pending` / `approved` / `rejected` |
| `approved_by` | transaction | Auto-set on approve/reject |
| `approved_at` | transaction | Auto-set on approve/reject |
| `rejection_remark` | transaction | Required for rejection |
| `po_weight` | articles | From PO extract (kg) |
| `po_quantity` | articles | PO ordered quantity (approval) |
| `units` | articles | Unit label (approval) |
| `material_type` | articles | From SKU lookup |
| `carton_weight` | articles | Carton weight (approval) |

### Renamed fields
| Old Name | New Name | Table |
|----------|----------|-------|
| `purchase_by` | `purchased_by` | transaction |
| `received_quantity` | `po_quantity` | transaction |
| `transaction_id` (in list) | `transaction_no` (in list) | API response |

### Changed table names
| Old | New |
|-----|-----|
| `cfpl_transactions` | `cfpl_transactions_v2` |
| `cfpl_articles` | `cfpl_articles_v2` |
| `cfpl_boxes` | `cfpl_boxes_v2` |

### Changed API endpoints
| Old Endpoint | New Endpoint |
|-------------|-------------|
| `POST /pdf-extraction/extract-purchase-order` | `POST /inward/extract-po` |
| `GET /sku/dropdown?company=&...` | `GET /inward/sku/dropdown?company=&...` |
| `GET /sku/global-search?company=&search=` | `GET /inward/sku/global-search?company=&search=` |
| `GET /sku/id?company=&item_description=&...` | `GET /inward/sku/id?company=&item_description=&...` |
| *(none)* | `POST /inward/sku-lookup/{company}` (new, simple lookup) |
| *(none)* | `PUT /inward/{company}/{txn}/approve` (new) |

### List response field change
```
Old:  { "transaction_id": "TR-..." }
New:  { "transaction_no": "TR-...", "status": "pending" }
```

The frontend must use `record.transaction_no` (not `record.transaction_id`) to build the detail URL.
