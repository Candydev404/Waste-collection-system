-- Waste Collection Request System - SQLite schema
-- Group 26, NS-CSC 229 Integrated Design Project II
-- Safe to run more than once (IF NOT EXISTS).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collection_requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_number TEXT    NOT NULL UNIQUE,
    full_name        TEXT    NOT NULL,
    area             TEXT    NOT NULL,
    collection_date  TEXT    NOT NULL,            -- ISO format YYYY-MM-DD
    waste_type       TEXT    NOT NULL
        CHECK (waste_type IN ('Household Waste','Plastic Waste','Organic Waste',
                              'Paper Waste','General Waste','Other')),
    status           TEXT    NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending','Scheduled','Collected')),
    is_demo          INTEGER NOT NULL DEFAULT 0,  -- 1 = synthetic demonstration record
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON collection_requests (status);
CREATE INDEX IF NOT EXISTS idx_requests_area   ON collection_requests (area);
CREATE INDEX IF NOT EXISTS idx_requests_date   ON collection_requests (collection_date);
CREATE INDEX IF NOT EXISTS idx_requests_created ON collection_requests (created_at);
