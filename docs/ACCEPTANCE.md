# Acceptance cases and the demonstration script

Every case from manual section 11, mapped to the test that pins it. Run them with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Result at the time of writing: **452 passed**.

## The acceptance table

| Case from the manual | Required result | Where it is pinned |
|---|---|---|
| Same import or replay submitted twice | No duplicate companies, offers or change alerts | `test_imports.py::test_importing_the_same_batch_twice_creates_no_duplicates`, `::test_replaying_an_identical_offer_snapshot_appends_no_observation`, `::test_replaying_an_identical_snapshot_raises_no_second_alert`, `test_workflow.py::test_replaying_twice_changes_nothing` |
| One VIN appears on two dealer sources | One reviewed identity links two distinct offers; not counted twice as independent inventory | `test_identity_and_observations.py::test_one_vin_on_two_sources_is_one_vehicle_and_two_offers`, `test_pricing.py::test_same_vin_on_two_sources_counts_once`, `test_workflow.py::test_one_vin_links_two_offers_in_the_demo_data` |
| Similar spec with no VIN | Possible duplicate for review, never automatic identity proof | `test_identity_and_observations.py::test_similar_spec_without_a_vin_creates_a_review_not_a_merge` (and `::test_clearly_different_cars_do_not_raise_a_duplicate_review`, so the queue stays useful) |
| Car is an allocation but the buyer needs immediate physical stock | No current fulfilment match, with a clear reason | `test_matching.py::test_allocation_cannot_fulfil_a_physical_stock_requirement`, `test_workflow.py::test_the_allocation_cannot_satisfy_a_physical_stock_requirement` |
| Buyer needs five homologated seats, car has four | Rejected match | `test_matching.py::test_four_seats_against_a_five_seat_requirement_is_rejected`, `test_workflow.py::test_the_four_seat_car_is_rejected_for_a_five_seat_requirement` |
| Required rear-seat option is unknown | Needs verification; never assumed present | `test_matching.py::test_unknown_required_option_needs_verification_and_is_never_assumed`, `test_workflow.py::test_the_unknown_option_needs_verification_and_is_not_assumed` |
| AMG Line or modified badges resemble AMG G63 naming | No automatic equivalence; exact model review required | `test_matching.py::test_amg_line_styling_is_never_equated_with_an_amg_g63`, `::test_amg_line_on_a_lesser_model_does_not_resolve_to_g63`, `test_identity_and_observations.py::test_a_lookalike_model_goes_to_review_and_does_not_join_the_g63_family` |
| Net standard-VAT price versus margin-scheme gross price | No automatic comparable-price or profit result | `test_pricing.py::test_margin_scheme_gross_is_excluded_not_divided_by_a_vat_rate`, `::test_basis_compatibility`, `test_matching.py::test_incompatible_vat_basis_is_rejected`, `test_workflow.py::test_the_margin_scheme_car_produces_no_automatic_comparison` |
| Unknown VAT or required transport/tax cost | Scenario incomplete, not zero-cost profit | `test_pricing.py::test_unknown_cost_makes_the_scenario_incomplete_not_zero_cost`, `::test_unknown_vat_regime_blocks_completion`, `::test_confirmed_zero_differs_from_unknown` |
| Mixed currency without dated conversion | Excluded from automatic comparison | `test_pricing.py::test_mixed_currency_is_excluded_until_a_dated_rate_is_entered`, `test_matching.py::test_mixed_currency_budget_cannot_be_compared`, `test_money.py::test_refuses_to_mix_currencies_without_a_rate`, `test_fx.py::test_a_foreign_comparable_stays_excluded_without_a_rate` |
| Mixed currency **with** a dated rate | Converted, and labelled with the rate and its date | `test_fx.py::test_a_dated_rate_brings_it_in_and_labels_it`, `::test_a_dated_rate_resolves_a_cross_currency_budget`, `::test_a_conversion_does_not_excuse_a_real_difference` |
| **Calculation example in section 7** | **€35,000 asking gap; €206,500 acquisition plus costs; €8,500 scenario contribution** | `test_pricing.py::test_manual_worked_example_exactly` |
| **Retail assumption falls by €10,000** | **Hypothetical trade price €205,000; contribution −€1,500** | `test_pricing.py::test_manual_downside_example_exactly`, `::test_sensitivity_reproduces_the_downside_from_the_base_case` |
| Recoverable tax paid before refund | Cash outflow shown separately from final cost; missing timing makes the schedule incomplete | `test_pricing.py::test_recoverable_tax_is_cash_out_but_not_a_final_cost`, `::test_missing_refund_date_makes_the_cash_schedule_incomplete`, `::test_missing_payment_date_makes_the_cash_schedule_incomplete` |
| Only one usable comparable | Insufficient-comparables state; no fabricated median | `test_pricing.py::test_only_one_usable_comparable_shows_the_observation_not_a_median`, `::test_median_needs_three_distinct_vehicles`, `test_workflow.py::test_the_s_class_has_insufficient_comparables` |
| A listing is absent or a request fails | Not observed, or fetch failed. Never sold | `test_identity_and_observations.py::test_a_disappeared_advert_is_not_observed_never_sold`, `::test_a_failed_fetch_leaves_the_last_successful_observation_intact`, `test_matching.py::test_not_observed_is_not_a_sale`, `::test_fetch_failure_is_not_a_sale` |
| Company advertises a 700-car partner network | The claim is recorded; no claim it owns 700 cars or purchases centrally | `test_imports.py::test_a_network_claim_is_stored_as_a_claim`, `test_workflow.py::test_the_network_claim_is_recorded_without_attributing_stock` |
| Public email discovered | Policy remains unknown or review required | `test_imports.py::test_a_discovered_contact_never_becomes_a_permission`, `test_outreach.py::test_a_discovered_public_email_does_not_become_permitted`, `test_connectors.py::test_website_adapter_never_grants_a_contact_permission` |
| Account objects after draft generation | Draft, copy and export blocked immediately | `test_outreach.py::test_an_objection_after_generation_blocks_the_draft_immediately`, `::test_a_suppressed_account_is_blocked_outright`, `::test_a_suppressed_account_cannot_enter_an_outreach_export` |
| Offer expires after draft generation | Availability claim and contact-ready draft need reconfirmation | `test_outreach.py::test_an_offer_expiring_after_generation_blocks_the_draft`, `::test_an_expired_offer_blocks_a_contact_ready_draft`, `::test_stale_availability_blocks_a_contact_ready_draft` |
| Source licence expires or prohibits export | Live access and export blocked as applicable; stored data under its retention rules | `test_source_policy.py::test_approval_lapses_on_its_review_due_date_without_an_edit`, `::test_an_expired_licence_blocks_export`, `::test_a_source_that_forbids_export_blocks_export`, `test_outreach.py::test_an_export_forbidden_by_the_source_licence_is_refused` |
| Website redirects to a local or private address | Fetch blocked without contacting that address | `test_source_policy.py::test_a_host_resolving_to_a_private_address_is_blocked_without_contacting_it`, `::test_local_names_are_blocked_by_name` |
| Source text instructs the app to email or export secrets | Treated only as data; no action and no privilege change | `test_connectors.py::test_website_adapter_does_not_execute_scripts_or_obey_page_text` (the fixture contains an injected instruction and an inline script) |
| Model invents a phone number or an unavailable option | Output rejected or flagged unsupported; no canonical field overwritten | No runtime AI is implemented, so nothing generates a field. The equivalent guard is tested on imports: `test_identity_and_observations.py::test_a_reviewed_vehicle_fact_is_filled_but_never_overwritten`, `test_workflow.py::test_a_source_specification_correction_does_not_overwrite_the_reviewed_vehicle` |
| Model or API unavailable | Existing records remain; deterministic call-brief fallback works | The deterministic template is the only generator and always produces an internal brief: `test_outreach.py::test_an_internal_brief_is_still_produced_when_a_gate_fails` |
| Demo reset or replay | Only demo data affected; live database untouched | `test_source_policy.py::test_demo_reset_touches_only_the_demo_file`, `::test_demo_reset_refuses_a_database_stamped_live`, `test_workflow.py::test_a_demo_reset_leaves_the_live_database_alone`, `::test_seeding_refuses_to_run_in_live_mode` |
| App restarts during or after an import | Transactional state preserved; no half-applied batch | `test_workflow.py::test_an_import_crash_leaves_no_half_applied_batch`, `test_imports.py::test_an_invalid_batch_writes_nothing_by_default` |
| Streamlit page reruns | No repeated fetches, paid AI calls, imports or duplicate tasks without explicit action | `test_app.py::test_opening_and_rerunning_a_page_creates_no_records`, `::test_recompute_happens_only_when_the_button_is_pressed`, `test_workflow.py::test_recompute_is_idempotent` |

### Cases added with the second session's features

| Case | Required result | Where it is pinned |
|---|---|---|
| A rate with no stated origin, a zero or negative rate | Refused; an unattributed rate is not evidence | `test_fx.py::test_a_rate_needs_a_stated_origin`, `::test_a_non_positive_rate_is_refused` |
| A rate dated after the moment analysed | Not used; that would be hindsight | `test_fx.py::test_a_rate_dated_after_the_moment_is_not_used` |
| A rate older than the window | Not used; the comparable is excluded and says so | `test_fx.py::test_a_stale_rate_is_refused_rather_than_used` |
| A rate applied in the wrong direction | Refused rather than silently inverted | `test_fx.py::test_a_rate_cannot_be_applied_in_the_wrong_direction` |
| A conversion is not a licence to ignore a real difference | A euro-priced but wrong-steering car is still excluded | `test_fx.py::test_a_conversion_does_not_excuse_a_real_difference` |
| A source contradicts the reviewed record | Both the disagreement and both possible decisions are shown; nothing is applied automatically | `test_corrections.py::test_a_disagreement_is_detected_with_both_values`, `::test_the_import_did_not_overwrite_the_reviewed_fact` |
| A correction is accepted | The reviewed record changes, with an audit entry, a named person and a dated note; the observation history is intact | `test_corrections.py::test_accepting_updates_the_reviewed_record`, `::test_accepting_writes_an_audit_entry`, `::test_the_original_observation_survives_acceptance` |
| A correction is rejected | Equally recorded, and that claim stops being raised; a *different* later claim still is | `test_corrections.py::test_keeping_the_reviewed_value_changes_nothing_but_is_recorded`, `::test_a_later_different_value_is_raised_again` |
| A correction without a note or a named person | Refused | `test_corrections.py::test_a_decision_requires_a_dated_note`, `::test_a_decision_requires_a_named_person` |
| An accepted correction reaches the buyer rules | After a recompute, a specification fit becomes a rejection | `test_corrections.py::test_an_accepted_correction_changes_the_match_after_a_recompute` |
| An analyst adjusts a comparable | Applied only when entered by a person with a reason; the median before adjustments is reported separately | `test_adjustments_and_review.py::test_an_adjustment_moves_the_median_and_shows_the_effect`, `::test_the_unadjusted_price_is_kept_beside_the_figure_used` |
| An adjustment with no reason, no author, zero, or wildly large | Refused, or not applied | `test_adjustments_and_review.py::test_an_adjustment_needs_a_reason`, `::test_an_adjustment_needs_an_author`, `::test_a_zero_adjustment_is_refused`, `::test_an_implausibly_large_adjustment_is_refused` |
| An adjustment is removed | Stops applying, but the record survives | `test_adjustments_and_review.py::test_retiring_an_adjustment_keeps_the_record_but_stops_applying_it` |
| A source is approved | Reviewer, scope, evidence kind and an expiry are all recorded, and kept as history | `test_adjustments_and_review.py::test_an_approval_records_reviewer_scope_and_expiry`, `::test_a_review_is_kept_as_history` |
| A licensed source approved on its published terms | Refused: reading terms is not holding the account scope | `test_adjustments_and_review.py::test_reading_published_terms_does_not_approve_a_licensed_source` |
| An approval with no stated use, evidence, evidence kind or host scope | Refused | `test_adjustments_and_review.py::test_an_approval_without_a_stated_use_is_refused`, `::test_an_approval_without_evidence_is_refused`, `::test_an_approval_must_say_what_kind_of_evidence_it_rests_on`, `::test_a_public_api_approval_needs_a_host_scope` |
| A source approved in demo mode | Warned as a rehearsal, and still cannot fetch | `test_adjustments_and_review.py::test_a_review_in_demo_mode_says_it_is_a_rehearsal`, `::test_approving_in_demo_mode_still_does_not_permit_a_fetch` |
| A confirmation message after an action | Survives the rerun and is readable | `test_app.py::test_resolving_a_contradiction_confirms_it_on_screen` |

### Cases added with the live refresh, capture and cost templates

| Case | Required result | Where it is pinned |
|---|---|---|
| A refresh in demo mode | Refused before a run is even opened; nothing is written | `test_refresh.py::test_demo_mode_refuses_without_opening_a_run` |
| A refresh from an unapproved source | Refused, with no fallback to scraping | `test_refresh.py::test_an_unapproved_source_is_refused` |
| A refresh from a source with no adapter | Refused; no endpoint is improvised | `test_refresh.py::test_a_source_with_no_adapter_is_refused_rather_than_improvised` |
| A successful discovery run | Company *candidates* stored, with field-level evidence and attribution; no buying route or demand is created | `test_refresh.py::test_a_successful_run_stores_company_candidates`, `::test_a_discovered_company_is_a_candidate_not_a_buyer`, `::test_field_level_evidence_is_recorded`, `::test_attribution_survives_into_the_report` |
| The same run twice | No duplicates | `test_refresh.py::test_running_twice_creates_no_duplicates` |
| A failed live fetch | The run is recorded as failed, records are unchanged, and the last successful observation survives | `test_refresh.py::test_a_failed_fetch_records_the_run_and_changes_no_records`, `::test_a_failed_supply_fetch_marks_offers_without_claiming_a_sale` |
| An adapter bug mid-run | Caught; stored records are not corrupted | `test_refresh.py::test_an_adapter_bug_does_not_corrupt_stored_records` |
| A requirement captured on a call | Stored, matchable, and expiring by default | `test_briefs_and_templates.py::test_a_captured_requirement_is_stored_and_matchable`, `::test_a_captured_requirement_expires_by_default`, `::test_a_captured_requirement_reaches_the_matching_rules` |
| A field the salesperson did not ask about | Dropped, not stored as a rule | `test_briefs_and_templates.py::test_blank_requirements_are_dropped_rather_than_stored_as_unknown` |
| A budget with no stated basis | Captured, and flagged as uncomparable | `test_briefs_and_templates.py::test_a_budget_with_no_basis_is_flagged_as_uncomparable` |
| A cost template loaded onto a worksheet | Supplies starting figures only; every line stays editable | `test_app.py::test_a_reviewed_cost_template_is_offered_and_does_not_invent_a_number` |
| A template line with no amount | Arrives unknown and still blocks a complete scenario | `test_briefs_and_templates.py::test_a_template_line_with_no_amount_arrives_unknown_and_blocks_completion` |
| A template with no reviewer or basis | Refused | `test_briefs_and_templates.py::test_a_template_needs_a_reviewer_and_a_basis` |
| A template whose review has lapsed | Still offered, and says so loudly rather than hiding | `test_briefs_and_templates.py::test_a_lapsed_template_says_so_loudly`, `::test_a_lapsed_template_is_still_offered_rather_than_hidden` |
| A schema upgrade on an existing database | Applies incrementally, takes a backup, loses no data | `test_workflow.py::test_a_later_migration_lands_on_an_existing_database_without_losing_data` |

### Cases added after the advert audit

| Case | Required result | Where it is pinned |
|---|---|---|
| Model text written the way adverts write it | Resolves, for all ten real title forms including "S 450" and "S 580" | `test_matching.py::test_model_text_written_the_way_adverts_write_it_resolves` |
| A GLS 63, whose text contains "s 63" | One family, not an ambiguous two | `test_matching.py::test_a_gls_63_is_not_also_an_s_class` |
| The word "single" in advert prose | Does not resolve a family | `test_matching.py::test_the_word_single_does_not_make_a_car_a_gle` |
| "740d" | Still matches the 7 Series; the boundary is at the start only | `test_matching.py::test_a_trailing_boundary_is_not_required` |
| An S 580 among S 450 comparables | Excluded, with the variants named in the reason | `test_pricing.py::test_a_different_variant_in_the_same_generation_is_excluded` |
| "G 63" beside "G 63 4MATIC" | Both included; a typed suffix is not a different car | `test_pricing.py::test_a_suffix_somebody_typed_is_not_a_different_car` |
| A comparable whose variant is unknown | Included; unknown does not manufacture an exclusion | `test_pricing.py::test_an_unknown_variant_is_not_treated_as_a_mismatch` |
| The variant rule turned off by the analyst | Honoured, and recorded in the result's filters | `test_pricing.py::test_the_variant_rule_can_be_turned_off_by_the_analyst` |
| The cross-border tax basis on a call brief | Stated on the brief, not buried in a calculation | `test_outreach.py::test_the_brief_states_the_new_means_of_transport_basis` |
| A vehicle with no registration date or mileage | The basis cannot be determined, and that becomes a missing fact | `test_outreach.py::test_an_undeterminable_tax_basis_becomes_a_missing_fact` |

### Cases added with the hosted build

| Case | Required result | Where it is pinned |
|---|---|---|
| A public instance pointed at live data | Refused at startup, naming the objection | `test_hosted_desk.py::test_a_public_instance_in_live_mode_is_refused` |
| A public instance with network access or runtime AI | Refused | `::test_a_public_instance_with_network_access_is_refused`, `::test_a_public_instance_with_runtime_ai_is_refused` |
| A redeploy over existing data | Seeds once; never overwrites | `::test_seeding_runs_once_and_never_overwrites` |
| mobile.de with no credentials | Refuses, and does **not** substitute the sample feed | `::test_the_adapter_refuses_and_does_not_fall_back_to_samples` |
| mobile.de with credentials but no approval | Refused by the policy gate first | `::test_the_policy_gate_runs_before_the_credential_check` |
| The mobile.de status label | Never says "connected" | `::test_the_adapter_reports_that_it_is_not_connected` |
| A listing price with no currency | Reported as an error, not assumed to be euros | `::test_a_price_without_a_currency_is_reported_not_assumed` |
| A listing with no identifier | Skipped, not invented | `::test_an_advert_with_no_identifier_is_skipped_rather_than_invented` |
| A listing with no stated VAT position | Basis stays unknown through the import | `::test_an_unstated_vat_position_leaves_the_basis_unknown` |
| A buy-side draft | Never shows the seller our comparable median or a waiting buyer | `test_drafting_and_ranking.py::test_the_buy_side_draft_never_shows_the_seller_our_own_median`, `::test_the_buy_side_draft_does_not_reveal_a_waiting_buyer` |
| A sell-side draft on a stale confirmation | Asks about availability rather than asserting it | `::test_a_stale_confirmation_is_never_described_as_available` |
| A sell-side draft on an unconfirmed requirement | Does not describe it as confirmed | `::test_an_unconfirmed_requirement_is_not_described_as_confirmed` |
| A budget with no stated basis | Blocks the price comparison in the draft | `::test_a_budget_with_no_basis_blocks_the_price_comparison` |
| Ranking | A verified contribution always outranks a larger unverified gap | `::test_a_verified_contribution_outranks_a_bigger_unverified_gap` |
| A car priced above its comparables | Not an opportunity | `::test_a_car_priced_above_its_comparables_is_not_an_opportunity` |
| The opportunities feed | Contains supply only; retail comparables are evidence, not stock | `::test_the_feed_only_contains_supply` |
| Two variants in one generation | Separate medians, tens of thousands apart | `test_workflow.py::test_the_two_s_class_variants_get_separate_medians` |
| The two seeded cost templates | One completes, one correctly cannot | `test_workflow.py::test_one_seeded_template_completes_and_one_cannot` |

### Additional checks the manual asks for in prose

| Requirement | Where |
|---|---|
| Freeze the clock for expiring confirmations | Every test uses `FrozenClock`; `tests/conftest.py` |
| Test import parsing with EU and UK number formats | `test_money.py::test_parses_eu_and_uk_number_formats`, `::test_ambiguous_separator_demands_the_locale_rather_than_guessing` |
| Test a formula-like company-name cell | `test_matching.py::test_formula_like_export_cells_are_neutralised`, `test_imports.py::test_export_neutralises_formula_like_cells` |
| Test permissions at service level, not only by hiding a button | `test_outreach.py` calls `evaluate_gates` and `build_export` directly; `test_source_policy.py` calls the policy directly |
| Calculations are reproducible | Five independent fresh seeds give an identical comparable set and median (verified; see `STATUS.md`) |
| Every displayed fact has a provenance route | `evidence`, `observations` and `confirmed_by`; see `DATA_DICTIONARY.md` |
| Demo data is clearly labelled | `test_workflow.py::test_every_demo_company_uses_a_reserved_example_domain`, `::test_no_demo_phone_number_is_routable`, `::test_every_demo_row_is_marked_as_demo_data`, `test_app.py::test_the_demo_label_is_persistent` |
| Blocked integrations described accurately | `test_connectors.py::test_no_connector_can_fetch_from_the_seeded_demo_register`; `SOURCE_ACCESS.md` |
| No Send or Auto-dial anywhere | `test_app.py::test_there_is_no_send_or_dial_button_anywhere` |
| The worked example produces the manual's figures **on screen**, not only in the service | `test_app.py::test_the_worked_example_toggle_actually_reloads_the_form`, `::test_the_worked_example_produces_the_manuals_figures_on_screen`, `::test_the_downside_is_shown_on_screen_as_a_loss` |
| An unfilled worksheet reports Incomplete on screen | `test_app.py::test_an_unfilled_worksheet_reports_incomplete_not_free_profit` |
| A call brief is readable by a person: no internal slug, no raw dict, no ISO timestamp | `test_outreach.py::test_the_brief_contains_no_internal_slug_or_raw_dict`, `::test_requirements_are_described_in_words` |

## The five-minute demonstration

Start from a clean database so the numbers match this script:

```powershell
.\.venv\Scripts\python.exe -m desk.cli reset-demo --yes
.\.venv\Scripts\python.exe -m desk.cli seed-demo
.\.venv\Scripts\python.exe -m desk.cli recompute
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

### First minute — what this is, and what it is not

Open **Today**. Say out loud that every name and price on screen is synthetic, that the sidebar
and the banner say so, and that the idea is to complement existing systems rather than replace
them.

Point at two numbers side by side: **Confirmed requirements** and, on **Matches → Category-fit
prospects**, the prospect count. A business carrying similar stock is not a buyer. Open
`Demo Prestige Dealer B` on **Businesses** and show the confirmed G 63 brief — who confirmed it,
when, and the evidence note from the recorded call.

Read the standing limitation aloud: no authorised price feed is connected, so this analyses
imported observations, not the live European market.

### Second minute — a change, with its evidence

Leave the app running and replay the second snapshot:

```powershell
.\.venv\Scripts\python.exe -m desk.cli replay-demo
```

Back on **Today**, three alerts appear: a price change on `OFR-001` from €200,000 to €193,000,
`OFR-004` reserved by its seller, and a seat-count correction on the `OFR-002` allocation. Each
shows the old and new value, when the source stated it, and when the desk recorded it.

On **Vehicles**, open `OFR-001` and show the observation history: the original €200,000
observation is still there. Then open `OFR-002` and show the contradiction banner — the source now
says four seats, the reviewed record still says five, and the import did not overwrite it.

Open **Matches → `OFR-001 × BRF-001` → Comparable prices → Excluded**. Walk down the reasons: a
margin-scheme gross price, a car with 95,000 km, a GBP price with no dated rate, a dealer bid, a
completed sale, a stale observation, and a second advert for the same VIN. Same badge is not
enough.

### Third minute — from a gap to a conditional contribution

Still on that match, open the cost worksheet and turn on **Load the build manual's worked
example**. Press *Calculate scenario*.

| On screen | Figure |
|---|---|
| Acquisition plus included costs | €206,500 |
| Observed asking-price gap | €35,000 |
| Hypothetical trade price | €215,000 |
| Scenario contribution | €8,500 |

Say the important sentence: the €35,000 is an investigation signal, not profit. The €8,500 is
conditional on an assumed retail outcome of €230,000 and on that dealer's own cost and profit
requirements — it is not a bid. Then point at the downside panel: if the retail outcome falls by
€10,000 and the dealer keeps its requirements, the trade price becomes €205,000 and the
contribution becomes **−€1,500**.

Open the assumptions panel: unmodelled overhead is excluded, expected commission is blank because
no approved compensation rule exists, and the risk allowance is a provision rather than proof the
risk is covered.

### Fourth minute — a useful brief, and an objection

Go to **Call preparation**, pick the `OFR-001` match, channel *phone*. The brief names the actual
contact and role, states the confirmed requirement, gives a truthful opener, three qualification
questions and a 12-minute meeting request. Note the two available actions: **Copy** and
**Download brief**. There is no Send and no Auto-dial.

Switch the channel to *email* and show it drop to an internal research brief, because the email
policy is `review_required` — a published address is not consent.

Now go to **Businesses → Demo Prestige Dealer F**, record an outcome of `objection`, and return to
**Call preparation → Exports**. Build a contact-ready export: that account is excluded with its
reason, even though it previously had a phone permission. Open **Saved drafts** to show a
previously valid draft now blocked.

### Fifth minute — the next company decision

Go to **Sources and imports**. Walk the register: three synthetic sources enabled, three public
APIs implemented but `review_required`, three commercial sources `blocked` with no scraper, and
one entry deliberately past its review date to show that approval lapses on its own.

Then ask the real questions, rather than claiming a result:

- Which stock export could the company authorise, and who owns that data?
- Which markets and which contact routes are approved?
- Which existing systems would this sit beside, and what would a small pilot look like?

Do not say it has found a real buyer or a profitable trade. It has not, and the screen does not
claim to.

## Definition of done

| Criterion | State |
|---|---|
| The offline demo works from a clean setup | Yes, from the commands in the README |
| Critical tests pass | Yes, 452 |
| Every displayed fact has a provenance route | Yes, via `evidence`, `observations` and `confirmed_by` |
| Calculations are reproducible | Yes, verified across five independent fresh seeds |
| False matches are rejected | Yes, with the reason shown |
| Drafts respect current records | Yes, re-checked on every open, copy and export |
| Blocked or unavailable integrations described accurately | Yes, in the register, the UI and `SOURCE_ACCESS.md` |
| The README states which live sources were tested | Yes: **none** |
| No public deployment or third-party outreach required | Correct, and neither is possible from this codebase |
