-- Luxury Car Opportunity Desk - initial schema.
--
-- Design rules enforced here:
--   * money is an integer count of minor units plus an explicit currency;
--   * price_basis (net/gross/unknown) and vat_regime are separate columns;
--   * source observations are append-only and never overwritten by review;
--   * every record carries is_demo so demo rows can never be mistaken for live;
--   * unknown is a first-class stored value, not NULL-as-optimism.

CREATE TABLE schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE app_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- ---------------------------------------------------------------- sources

CREATE TABLE sources (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    category              TEXT NOT NULL CHECK (category IN (
                              'company_discovery','identity_verification','vehicle_supply',
                              'comparables','human_demand','mixed')),
    access_mode           TEXT NOT NULL CHECK (access_mode IN (
                              'fixture','file_import','human_entry','public_api',
                              'licensed_api','approved_website')),
    base_url              TEXT,
    documentation_url     TEXT,
    allowed_hosts         TEXT NOT NULL DEFAULT '[]',   -- JSON array of hostnames
    allowed_paths         TEXT NOT NULL DEFAULT '[]',   -- JSON array of path prefixes
    approved_use          TEXT,
    attribution           TEXT,
    -- retention / reuse restrictions
    retention_rule        TEXT,
    retention_days        INTEGER,
    raw_retention_allowed INTEGER NOT NULL DEFAULT 1 CHECK (raw_retention_allowed IN (0,1)),
    export_allowed        INTEGER NOT NULL DEFAULT 0 CHECK (export_allowed IN (0,1)),
    -- limits
    rate_limit_per_minute INTEGER,
    max_concurrency       INTEGER NOT NULL DEFAULT 1,
    -- approval trail
    status                TEXT NOT NULL CHECK (status IN ('approved','review_required','expired','blocked')),
    approval_evidence     TEXT,
    reviewer              TEXT,
    reviewed_at           TEXT,
    review_due_at         TEXT,
    last_fetch_at         TEXT,
    last_fetch_status     TEXT,
    notes                 TEXT,
    is_demo               INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

-- Field-level provenance. Nothing displayed as a fact should lack a route here.
CREATE TABLE evidence (
    id               TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT,
    field_name       TEXT NOT NULL,
    value_text       TEXT,
    excerpt          TEXT,              -- permitted short excerpt only
    url              TEXT,
    file_ref         TEXT,
    row_ref          TEXT,
    source_record_id TEXT,
    content_hash     TEXT,
    observed_at      TEXT NOT NULL,     -- when the source stated it
    recorded_at      TEXT NOT NULL,     -- when this app stored it
    confidence       TEXT NOT NULL CHECK (confidence IN (
                         'observed_public','seller_claimed','buyer_confirmed',
                         'company_confirmed','derived','unknown','conflicting')),
    confirmed_by     TEXT,
    expires_at       TEXT,
    is_demo          INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_evidence_entity ON evidence(entity_type, entity_id);
CREATE INDEX idx_evidence_source ON evidence(source_id);

-- ------------------------------------------------------------- companies

CREATE TABLE companies (
    id                 TEXT PRIMARY KEY,
    source_id          TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    external_record_id TEXT,
    legal_name         TEXT NOT NULL,
    trading_name       TEXT,
    country            TEXT NOT NULL,
    region             TEXT,
    city               TEXT,
    address            TEXT,
    registry_id        TEXT,            -- e.g. Czech ICO, verified only
    registry_verified  INTEGER NOT NULL DEFAULT 0 CHECK (registry_verified IN (0,1)),
    website            TEXT,
    parent_company_id  TEXT REFERENCES companies(id) ON DELETE SET NULL,
    is_branch          INTEGER NOT NULL DEFAULT 0 CHECK (is_branch IN (0,1)),
    category           TEXT NOT NULL DEFAULT 'unknown' CHECK (category IN (
                           'dealer','fleet','rental','chauffeur','broker','referral_partner','unknown')),
    is_referral_partner INTEGER NOT NULL DEFAULT 0 CHECK (is_referral_partner IN (0,1)),
    buying_route       TEXT NOT NULL DEFAULT 'unknown' CHECK (buying_route IN (
                           'buy_for_stock','buy_against_order','lease','broker_only',
                           'referral_only','unknown')),
    buying_authority   TEXT NOT NULL DEFAULT 'unknown',
    network_claim      TEXT,            -- e.g. "advertises 700-car partner network"
    review_status      TEXT NOT NULL DEFAULT 'review_required',
    introduction_notes TEXT,
    notes              TEXT,
    is_demo            INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (source_id, external_record_id)
);
CREATE INDEX idx_companies_country ON companies(country);

CREATE TABLE contacts (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    full_name      TEXT,                -- NULL means unknown, never invented
    role_title     TEXT,
    business_email TEXT,
    business_phone TEXT,
    contact_type   TEXT NOT NULL DEFAULT 'unknown' CHECK (contact_type IN (
                       'published_business','switchboard','web_form','unknown')),
    verified_at    TEXT,
    notes          TEXT,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX idx_contacts_company ON contacts(company_id);

-- An accountable human decision per company/contact, channel and purpose.
-- Discovering an address must never create a permitted row.
CREATE TABLE contact_policies (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id    TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    channel       TEXT NOT NULL CHECK (channel IN ('phone','email','post','web_form','in_person')),
    purpose       TEXT NOT NULL,
    market        TEXT,
    status        TEXT NOT NULL CHECK (status IN (
                      'unknown','review_required','permitted_for_scope','denied','expired')),
    basis         TEXT,
    restrictions  TEXT,
    reviewer      TEXT,
    reviewed_at   TEXT,
    expires_at    TEXT,
    is_demo       INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_policies_company ON contact_policies(company_id);

CREATE TABLE suppressions (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id  TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    scope       TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('all','email','phone','post','web_form')),
    reason      TEXT NOT NULL,
    recorded_by TEXT,
    created_at  TEXT NOT NULL,
    is_demo     INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_suppressions_company ON suppressions(company_id);

-- --------------------------------------------------------- buyer demand

CREATE TABLE buyer_briefs (
    id                 TEXT PRIMARY KEY,
    source_id          TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    external_record_id TEXT,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id         TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    model_family       TEXT NOT NULL,
    variant            TEXT,
    required_specs     TEXT NOT NULL DEFAULT '{}',  -- JSON: hard requirements
    preferred_specs    TEXT NOT NULL DEFAULT '{}',  -- JSON: ranking preferences only
    budget_minor       INTEGER,
    budget_currency    TEXT,
    budget_basis       TEXT NOT NULL DEFAULT 'unknown' CHECK (budget_basis IN ('net','gross','unknown')),
    budget_vat_regime  TEXT NOT NULL DEFAULT 'unknown' CHECK (budget_vat_regime IN (
                           'standard','margin','other','unknown')),
    quantity           INTEGER NOT NULL DEFAULT 1,
    destination_country TEXT,
    route              TEXT NOT NULL DEFAULT 'unknown' CHECK (route IN (
                           'buy_for_stock','buy_against_order','lease','broker_only',
                           'referral_only','unknown')),
    required_by        TEXT,
    conversation_date  TEXT NOT NULL,
    confirmed_at       TEXT,
    confirmed_by       TEXT,
    expires_at         TEXT,
    evidence_note      TEXT,
    is_demo            INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    CHECK (quantity > 0),
    CHECK (budget_minor IS NULL OR budget_minor >= 0),
    UNIQUE (source_id, external_record_id)
);
CREATE INDEX idx_briefs_company ON buyer_briefs(company_id);
CREATE INDEX idx_briefs_model ON buyer_briefs(model_family);

-- -------------------------------------------------------------- supply

-- A reviewed vehicle identity. Multiple offers may point at one vehicle.
CREATE TABLE vehicles (
    id                    TEXT PRIMARY KEY,
    vin                   TEXT,          -- only when legitimately known
    vin_verified          INTEGER NOT NULL DEFAULT 0 CHECK (vin_verified IN (0,1)),
    identity_review_status TEXT NOT NULL DEFAULT 'review_required' CHECK (identity_review_status IN (
                              'review_required','reviewed','possible_duplicate','allocation_no_vin')),
    model_family          TEXT NOT NULL,
    variant               TEXT,
    generation            TEXT,
    model_year            INTEGER,
    first_registration    TEXT,
    mileage_km            INTEGER,
    powertrain            TEXT,
    steering              TEXT CHECK (steering IN ('lhd','rhd','unknown')),
    seats                 INTEGER,
    specification         TEXT NOT NULL DEFAULT '{}',  -- JSON of reviewed canonical options
    notes                 TEXT,
    is_demo               INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    CHECK (mileage_km IS NULL OR mileage_km >= 0),
    CHECK (seats IS NULL OR (seats BETWEEN 1 AND 9))
);
CREATE INDEX idx_vehicles_vin ON vehicles(vin);

CREATE TABLE offers (
    id                    TEXT PRIMARY KEY,
    source_id             TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    external_record_id    TEXT NOT NULL,
    vehicle_id            TEXT REFERENCES vehicles(id) ON DELETE SET NULL,
    seller_company_id     TEXT REFERENCES companies(id) ON DELETE SET NULL,
    seller_name           TEXT,
    listing_url           TEXT,
    price_minor           INTEGER,
    price_currency        TEXT,
    price_basis           TEXT NOT NULL DEFAULT 'unknown' CHECK (price_basis IN ('net','gross','unknown')),
    vat_regime            TEXT NOT NULL DEFAULT 'unknown' CHECK (vat_regime IN (
                              'standard','margin','other','unknown')),
    price_evidence_type   TEXT NOT NULL DEFAULT 'supply_asking' CHECK (price_evidence_type IN (
                              'retail_asking','supply_asking','dealer_bid','buyer_budget',
                              'completed_transaction','analyst_assumption')),
    raw_price_text        TEXT,
    status                TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN (
                              'unknown','advertised_available','availability_confirmed',
                              'reserved','unavailable','allocation')),
    stock_kind            TEXT NOT NULL DEFAULT 'unknown' CHECK (stock_kind IN (
                              'physical_stock','allocation','unknown')),
    location_country      TEXT,
    location_city         TEXT,
    authority_to_sell     TEXT NOT NULL DEFAULT 'unknown' CHECK (authority_to_sell IN (
                              'unknown','claimed','confirmed','denied')),
    available_from        TEXT,
    valid_until           TEXT,
    availability_confirmed_at TEXT,
    last_observation_state TEXT NOT NULL DEFAULT 'seen' CHECK (last_observation_state IN (
                              'seen','not_seen','fetch_failed')),
    last_seen_at          TEXT,
    notes                 TEXT,
    is_demo               INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    CHECK (price_minor IS NULL OR price_minor >= 0),
    UNIQUE (source_id, external_record_id)
);
CREATE INDEX idx_offers_vehicle ON offers(vehicle_id);
CREATE INDEX idx_offers_source ON offers(source_id);

-- Append-only history. A later snapshot never rewrites an earlier row.
CREATE TABLE observations (
    id                 TEXT PRIMARY KEY,
    offer_id           TEXT REFERENCES offers(id) ON DELETE CASCADE,
    source_id          TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    external_record_id TEXT,
    run_id             TEXT,
    observed_at        TEXT NOT NULL,   -- timestamp stated by the source
    fetched_at         TEXT NOT NULL,   -- when this app looked
    state              TEXT NOT NULL CHECK (state IN ('seen','not_seen','fetch_failed')),
    raw_price_text     TEXT,
    price_minor        INTEGER,
    price_currency     TEXT,
    price_basis        TEXT NOT NULL DEFAULT 'unknown',
    vat_regime         TEXT NOT NULL DEFAULT 'unknown',
    price_evidence_type TEXT NOT NULL DEFAULT 'supply_asking',
    status             TEXT,
    stock_kind         TEXT,
    mileage_km         INTEGER,
    seats              INTEGER,
    powertrain         TEXT,
    location_country   TEXT,
    parsed_fields      TEXT NOT NULL DEFAULT '{}',  -- JSON
    response_hash      TEXT,
    fetch_status       TEXT,
    evidence_id        TEXT REFERENCES evidence(id) ON DELETE SET NULL,
    version            INTEGER NOT NULL DEFAULT 1,
    is_demo            INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at         TEXT NOT NULL
);
CREATE INDEX idx_observations_offer ON observations(offer_id, observed_at);
-- One observation per source record per stated instant per snapshot hash:
-- replaying an identical snapshot is therefore idempotent.
CREATE UNIQUE INDEX idx_observations_dedupe
    ON observations(source_id, external_record_id, observed_at, response_hash);

-- Change alerts derived from consecutive observations.
CREATE TABLE change_events (
    id             TEXT PRIMARY KEY,
    offer_id       TEXT REFERENCES offers(id) ON DELETE CASCADE,
    company_id     TEXT REFERENCES companies(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN (
                       'price_change','availability_change','specification_correction',
                       'not_observed','fetch_failed','company_signal','requirement_confirmed',
                       'follow_up_recorded')),
    field_name     TEXT,
    old_value      TEXT,
    new_value      TEXT,
    currency       TEXT,
    price_basis    TEXT,
    detected_at    TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    observation_id TEXT REFERENCES observations(id) ON DELETE SET NULL,
    dedupe_key     TEXT NOT NULL,
    acknowledged_at TEXT,
    notes          TEXT,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    UNIQUE (dedupe_key)
);
CREATE INDEX idx_change_events_detected ON change_events(detected_at);

CREATE TABLE duplicate_reviews (
    id            TEXT PRIMARY KEY,
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('vehicle','company','contact')),
    left_id       TEXT NOT NULL,
    right_id      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    signals       TEXT NOT NULL DEFAULT '{}',
    decision      TEXT NOT NULL DEFAULT 'pending' CHECK (decision IN (
                      'pending','same','different','deferred')),
    decided_by    TEXT,
    decided_at    TEXT,
    created_at    TEXT NOT NULL,
    is_demo       INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    UNIQUE (entity_type, left_id, right_id)
);

-- --------------------------------------------------- analysis artefacts

CREATE TABLE comparable_sets (
    id              TEXT PRIMARY KEY,
    target_offer_id TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    filters         TEXT NOT NULL DEFAULT '{}',
    tax_basis       TEXT NOT NULL,
    vat_regime      TEXT NOT NULL,
    included_count  INTEGER NOT NULL DEFAULT 0,
    distinct_vehicles INTEGER NOT NULL DEFAULT 0,
    median_minor    INTEGER,
    low_minor       INTEGER,
    high_minor      INTEGER,
    currency        TEXT,
    result_status   TEXT NOT NULL CHECK (result_status IN ('median_available','insufficient_comparables')),
    rule_version    TEXT NOT NULL,
    analysed_at     TEXT NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);

CREATE TABLE comparable_members (
    id                TEXT PRIMARY KEY,
    comparable_set_id TEXT NOT NULL REFERENCES comparable_sets(id) ON DELETE CASCADE,
    offer_id          TEXT REFERENCES offers(id) ON DELETE CASCADE,
    observation_id    TEXT REFERENCES observations(id) ON DELETE SET NULL,
    included          INTEGER NOT NULL CHECK (included IN (0,1)),
    reason            TEXT NOT NULL,
    price_minor       INTEGER,
    currency          TEXT,
    observed_at       TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_comparable_members_set ON comparable_members(comparable_set_id);

CREATE TABLE matches (
    id             TEXT PRIMARY KEY,
    offer_id       TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    brief_id       TEXT REFERENCES buyer_briefs(id) ON DELETE CASCADE,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    fit_status     TEXT NOT NULL CHECK (fit_status IN (
                       'no_match','needs_verification','specification_fit',
                       'category_fit_prospect','brief_expired')),
    score          INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL DEFAULT '{}',
    passed_rules   TEXT NOT NULL DEFAULT '[]',
    failed_rules   TEXT NOT NULL DEFAULT '[]',
    unknown_rules  TEXT NOT NULL DEFAULT '[]',
    evidence_refs  TEXT NOT NULL DEFAULT '[]',
    supply_confirmed_at TEXT,
    computed_at    TEXT NOT NULL,
    expires_at     TEXT,
    rule_version   TEXT NOT NULL,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
-- One row per offer, brief and company. brief_id is NULL for a category-fit
-- prospect, so the company has to be part of the key or one prospect would
-- overwrite another.
CREATE UNIQUE INDEX idx_matches_identity
    ON matches(offer_id, IFNULL(brief_id, ''), company_id);
CREATE INDEX idx_matches_company ON matches(company_id);
CREATE INDEX idx_matches_status ON matches(fit_status);

CREATE TABLE scenarios (
    id                    TEXT PRIMARY KEY,
    offer_id              TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    match_id              TEXT REFERENCES matches(id) ON DELETE SET NULL,
    buyer_company_id      TEXT REFERENCES companies(id) ON DELETE SET NULL,
    market                TEXT,
    currency              TEXT NOT NULL DEFAULT 'EUR',
    acquisition_minor     INTEGER,
    acquisition_basis     TEXT NOT NULL DEFAULT 'unknown',
    acquisition_vat_regime TEXT NOT NULL DEFAULT 'unknown',
    acquisition_evidence_type TEXT,
    sale_minor            INTEGER,
    sale_basis            TEXT NOT NULL DEFAULT 'unknown',
    sale_vat_regime       TEXT NOT NULL DEFAULT 'unknown',
    sale_evidence_type    TEXT,
    -- downstream dealer requirements, used for the hypothetical trade price
    retail_assumption_minor      INTEGER,
    dealer_downstream_cost_minor INTEGER,
    dealer_required_contribution_minor INTEGER,
    -- computed
    included_costs_minor  INTEGER,
    total_outlay_minor    INTEGER,
    hypothetical_trade_price_minor INTEGER,
    contribution_minor    INTEGER,
    asking_gap_minor      INTEGER,
    cash_outflow_minor    INTEGER,
    recoverable_tax_minor INTEGER,
    cash_schedule_status  TEXT NOT NULL DEFAULT 'incomplete' CHECK (cash_schedule_status IN (
                              'complete','incomplete')),
    status                TEXT NOT NULL CHECK (status IN ('complete','incomplete')),
    missing_inputs        TEXT NOT NULL DEFAULT '[]',
    assumptions           TEXT NOT NULL DEFAULT '[]',
    commission_rule_id    TEXT,
    computed_at           TEXT NOT NULL,
    is_demo               INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);

CREATE TABLE scenario_costs (
    id             TEXT PRIMARY KEY,
    scenario_id    TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    label          TEXT NOT NULL,
    amount_minor   INTEGER,             -- NULL means unknown; 0 means confirmed zero
    currency       TEXT NOT NULL DEFAULT 'EUR',
    is_confirmed_zero INTEGER NOT NULL DEFAULT 0 CHECK (is_confirmed_zero IN (0,1)),
    tax_treatment  TEXT NOT NULL DEFAULT 'unknown' CHECK (tax_treatment IN (
                       'non_recoverable','recoverable','outside_scope','unknown')),
    already_in_purchase_price INTEGER NOT NULL DEFAULT 0 CHECK (already_in_purchase_price IN (0,1)),
    required       INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0,1)),
    paid_at        TEXT,
    refunded_at    TEXT,
    notes          TEXT,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_scenario_costs_scenario ON scenario_costs(scenario_id);

CREATE TABLE fx_rates (
    id             TEXT PRIMARY KEY,
    from_currency  TEXT NOT NULL,
    to_currency    TEXT NOT NULL,
    rate           TEXT NOT NULL,       -- decimal string, never a float
    rate_date      TEXT NOT NULL,
    source_note    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    UNIQUE (from_currency, to_currency, rate_date)
);

-- ------------------------------------------------- follow-up and drafts

CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,
    company_id  TEXT REFERENCES companies(id) ON DELETE CASCADE,
    match_id    TEXT REFERENCES matches(id) ON DELETE SET NULL,
    offer_id    TEXT REFERENCES offers(id) ON DELETE SET NULL,
    action      TEXT NOT NULL CHECK (action IN (
                    'check_availability','ask_buyer','review_costs','ready_for_call_preparation',
                    'reconfirm_brief','verify_contact','review_duplicate')),
    label       TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    channel     TEXT,
    state       TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','done','cancelled')),
    completed_at TEXT,
    notes       TEXT,
    dedupe_key  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_demo     INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    UNIQUE (dedupe_key)
);
CREATE INDEX idx_tasks_due ON tasks(state, due_at);

CREATE TABLE interactions (
    id           TEXT PRIMARY KEY,
    source_id    TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    external_record_id TEXT,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id   TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    match_id     TEXT REFERENCES matches(id) ON DELETE SET NULL,
    brief_id     TEXT REFERENCES buyer_briefs(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    meeting_status TEXT,
    notes        TEXT,
    evidence_id  TEXT REFERENCES evidence(id) ON DELETE SET NULL,
    recorded_by  TEXT,
    created_at   TEXT NOT NULL,
    is_demo      INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_interactions_company ON interactions(company_id, occurred_at);
-- One recorded interaction per source record: re-importing the same file, or
-- replaying a snapshot, must not double-count a conversation that happened once.
CREATE UNIQUE INDEX idx_interactions_external
    ON interactions(source_id, external_record_id)
    WHERE source_id IS NOT NULL AND external_record_id IS NOT NULL;

CREATE TABLE drafts (
    id                TEXT PRIMARY KEY,
    match_id          TEXT REFERENCES matches(id) ON DELETE CASCADE,
    brief_id          TEXT REFERENCES buyer_briefs(id) ON DELETE SET NULL,
    -- The supporting offer is recorded directly. Reaching it only through the
    -- match would make a draft unverifiable once the match is recomputed.
    offer_id          TEXT REFERENCES offers(id) ON DELETE SET NULL,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id        TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    channel           TEXT NOT NULL,
    body              TEXT NOT NULL,
    facts_used        TEXT NOT NULL DEFAULT '[]',
    unresolved_fields TEXT NOT NULL DEFAULT '[]',
    status            TEXT NOT NULL CHECK (status IN ('internal_research','contact_ready','blocked')),
    block_reasons     TEXT NOT NULL DEFAULT '[]',
    policy_checked_at TEXT NOT NULL,
    facts_fingerprint TEXT NOT NULL,
    expires_at        TEXT,
    created_at        TEXT NOT NULL,
    is_demo           INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_drafts_company ON drafts(company_id);

-- ------------------------------------------------------------ run trail

CREATE TABLE runs (
    id                TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    connector_id      TEXT,
    connector_version TEXT,
    mode              TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    records_seen      INTEGER NOT NULL DEFAULT 0,
    records_created   INTEGER NOT NULL DEFAULT 0,
    records_updated   INTEGER NOT NULL DEFAULT 0,
    records_skipped   INTEGER NOT NULL DEFAULT 0,
    errors            TEXT NOT NULL DEFAULT '[]',
    status            TEXT NOT NULL DEFAULT 'running',
    notes             TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);

CREATE TABLE change_log (
    id           TEXT PRIMARY KEY,
    run_id       TEXT REFERENCES runs(id) ON DELETE SET NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    field_name   TEXT,
    old_value    TEXT,
    new_value    TEXT,
    action       TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT 'system',
    occurred_at  TEXT NOT NULL,
    is_demo      INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_change_log_entity ON change_log(entity_type, entity_id);
