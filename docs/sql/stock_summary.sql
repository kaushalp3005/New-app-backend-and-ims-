-- ============================================================
-- Stock Summary Table
-- One row per article per shift (attendance).
-- Closing qty = opening + received - sold (set at punch-out).
-- ============================================================

CREATE TABLE stock_summary (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    attendance_id   UUID            NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
    product_id      UUID            NOT NULL REFERENCES products(id),
    ean             VARCHAR(13)     NOT NULL,
    article_code    VARCHAR(15)     NOT NULL,
    opening_qty     INTEGER         NOT NULL CHECK (opening_qty >= 0),
    received_qty    INTEGER         NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
    sold_qty        INTEGER         NOT NULL DEFAULT 0 CHECK (sold_qty >= 0),
    closing_qty     INTEGER         NULL,
    status          VARCHAR(10)     NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    received_at     TIMESTAMP       NOT NULL,
    closed_at       TIMESTAMP       NULL,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- Prevent duplicate article per shift
CREATE UNIQUE INDEX idx_stocksummary_attendance_product
    ON stock_summary (attendance_id, product_id);

-- All stock entries for a single shift
CREATE INDEX idx_stocksummary_attendance
    ON stock_summary (attendance_id);

-- Product history across all shifts/stores
CREATE INDEX idx_stocksummary_product
    ON stock_summary (product_id);

-- Barcode lookup
CREATE INDEX idx_stocksummary_ean
    ON stock_summary (ean);

-- Date range queries (daily/weekly/monthly reports)
CREATE INDEX idx_stocksummary_created
    ON stock_summary (created_at);

-- Find open shifts (useful if mid-shift sync is added later)
CREATE INDEX idx_stocksummary_active
    ON stock_summary (status)
    WHERE status = 'active';

-- Closing stock validation check
ALTER TABLE stock_summary
    ADD CONSTRAINT chk_closing_qty_formula
    CHECK (
        closing_qty IS NULL
        OR closing_qty = opening_qty + received_qty - sold_qty
    );

-- Sold cannot exceed available stock
ALTER TABLE stock_summary
    ADD CONSTRAINT chk_sold_lte_available
    CHECK (sold_qty <= opening_qty + received_qty);
