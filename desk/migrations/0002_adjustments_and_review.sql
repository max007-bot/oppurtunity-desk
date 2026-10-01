-- Adds the four capabilities listed as next steps in docs/STATUS.md:
--   1. accepting a source correction onto a reviewed vehicle, with an audit trail;
--   2. dated FX conversion, so a non-EUR observation can enter a comparison;
--   3. analyst adjustments on a comparable, labelled as assumptions;
--   4. a recorded source review, so enabling a source is not a hand edit.
--
-- The same rules as the initial schema apply: money is integer minor units,
-- unknown stays unknown, and nothing here lets a derived value overwrite a
-- reviewed fact without a person and a timestamp attached.

-- ------------------------------------------------ analyst adjustments

-- An explicit, reasoned monetary adjustment an analyst applies to one comparable
-- so it reads as more like the target car. It is always an assumption, never an
-- automatic correction, and the unadjusted price is kept beside it.
CREATE TABLE comparable_adjustments (
    id                   TEXT PRIMARY KEY,
    target_offer_id      TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    comparable_offer_id  TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    amount_minor         INTEGER NOT NULL,   -- signed: negative reduces the comparable
    currency             TEXT NOT NULL,
    reason               TEXT NOT NULL,
    created_by           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    retired_at           TEXT,               -- kept for the audit trail, not deleted
    is_demo              INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    UNIQUE (target_offer_id, comparable_offer_id, retired_at)
);
CREATE INDEX idx_comparable_adjustments_target
    ON comparable_adjustments(target_offer_id, retired_at);

-- What a stored comparable set did with adjustments and conversions.
ALTER TABLE comparable_sets ADD COLUMN median_before_adjustments_minor INTEGER;
ALTER TABLE comparable_sets ADD COLUMN adjustments_applied INTEGER NOT NULL DEFAULT 0;
ALTER TABLE comparable_sets ADD COLUMN conversions_applied INTEGER NOT NULL DEFAULT 0;

-- Per-member provenance for anything that changed the figure used.
ALTER TABLE comparable_members ADD COLUMN observed_price_minor INTEGER;
ALTER TABLE comparable_members ADD COLUMN observed_currency TEXT;
ALTER TABLE comparable_members ADD COLUMN adjustment_minor INTEGER;
ALTER TABLE comparable_members ADD COLUMN adjustment_reason TEXT;
ALTER TABLE comparable_members ADD COLUMN fx_rate TEXT;
ALTER TABLE comparable_members ADD COLUMN fx_rate_date TEXT;

-- ------------------------------------------------------ source review

-- Every recorded review of a source, kept as history rather than overwritten, so
-- "who approved this, when, and on what evidence" survives the next review.
CREATE TABLE source_reviews (
    id                TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    previous_status   TEXT,
    status            TEXT NOT NULL CHECK (status IN (
                          'approved','review_required','expired','blocked')),
    approved_use      TEXT,
    approval_evidence TEXT,
    evidence_kind     TEXT NOT NULL DEFAULT 'unknown' CHECK (evidence_kind IN (
                          'published_terms_reviewed','contract_or_account_scope',
                          'owner_authorisation','unknown')),
    allowed_hosts     TEXT NOT NULL DEFAULT '[]',
    allowed_paths     TEXT NOT NULL DEFAULT '[]',
    retention_rule    TEXT,
    retention_days    INTEGER,
    raw_retention_allowed INTEGER NOT NULL DEFAULT 1 CHECK (raw_retention_allowed IN (0,1)),
    export_allowed    INTEGER NOT NULL DEFAULT 0 CHECK (export_allowed IN (0,1)),
    rate_limit_per_minute INTEGER,
    max_concurrency   INTEGER,
    reviewer          TEXT NOT NULL,
    reviewed_at       TEXT NOT NULL,
    review_due_at     TEXT,
    notes             TEXT,
    mode              TEXT NOT NULL,
    is_demo           INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_source_reviews_source ON source_reviews(source_id, reviewed_at);

-- --------------------------------------------- accepted corrections

-- A human decision about a contradiction between a source observation and the
-- reviewed vehicle record. Both outcomes are recorded: accepting the source's
-- value, and deliberately keeping the reviewed one.
CREATE TABLE fact_reviews (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL DEFAULT 'vehicle',
    entity_id       TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    previous_value  TEXT,
    observed_value  TEXT,
    decision        TEXT NOT NULL CHECK (decision IN ('accepted_source','kept_reviewed')),
    note            TEXT NOT NULL,
    observation_id  TEXT REFERENCES observations(id) ON DELETE SET NULL,
    change_event_id TEXT REFERENCES change_events(id) ON DELETE SET NULL,
    decided_by      TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_fact_reviews_entity ON fact_reviews(entity_type, entity_id, field_name);
