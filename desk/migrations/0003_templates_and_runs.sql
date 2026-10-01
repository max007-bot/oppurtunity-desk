-- Reviewed, reusable cost templates for a route, and the evidence trail a live
-- refresh produces.
--
-- A cost template is a reviewed set of assumptions about what it costs to move a
-- car along one route. It is not a tax engine: it records what a named person
-- established, when, and from what, and every line stays editable in the
-- worksheet after it is loaded.

CREATE TABLE cost_templates (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    origin_country    TEXT,
    destination_country TEXT NOT NULL,
    model_family      TEXT,             -- NULL applies to any family
    currency          TEXT NOT NULL DEFAULT 'EUR',
    -- The review discipline used everywhere else in this application.
    basis             TEXT NOT NULL,    -- what these figures were established from
    reviewer          TEXT NOT NULL,
    reviewed_at       TEXT NOT NULL,
    review_due_at     TEXT,
    notes             TEXT,
    retired_at        TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_cost_templates_route
    ON cost_templates(destination_country, origin_country, retired_at);

CREATE TABLE cost_template_lines (
    id             TEXT PRIMARY KEY,
    template_id    TEXT NOT NULL REFERENCES cost_templates(id) ON DELETE CASCADE,
    label          TEXT NOT NULL,
    -- NULL means the template knows this line applies but not what it costs. That
    -- is deliberately different from zero, and it still blocks a complete scenario
    -- until someone fills it in for the specific car.
    amount_minor   INTEGER,
    is_confirmed_zero INTEGER NOT NULL DEFAULT 0 CHECK (is_confirmed_zero IN (0,1)),
    tax_treatment  TEXT NOT NULL DEFAULT 'non_recoverable' CHECK (tax_treatment IN (
                       'non_recoverable','recoverable','outside_scope','unknown')),
    already_in_purchase_price INTEGER NOT NULL DEFAULT 0
                       CHECK (already_in_purchase_price IN (0,1)),
    required       INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0,1)),
    -- Where this particular figure came from, per line rather than per template.
    evidence_note  TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_demo        INTEGER NOT NULL DEFAULT 1 CHECK (is_demo IN (0,1))
);
CREATE INDEX idx_cost_template_lines_template ON cost_template_lines(template_id, sort_order);

-- Which template a scenario was built from, so a figure can be traced back to the
-- review that produced it even after the template is revised.
ALTER TABLE scenarios ADD COLUMN cost_template_id TEXT;
ALTER TABLE scenarios ADD COLUMN cost_template_name TEXT;
ALTER TABLE scenario_costs ADD COLUMN from_template_id TEXT;

-- A live refresh records what it asked for as well as what came back, so a run
-- can be explained without re-reading the code.
ALTER TABLE runs ADD COLUMN request_summary TEXT;
ALTER TABLE runs ADD COLUMN attribution TEXT;
