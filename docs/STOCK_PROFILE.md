# What the advert audit actually shows

Derived from the 38 saved adverts in the parent `optimum-research` folder
(`inventory-normalized.json`, extracted from mobile.de detail pages).

**Status of this document: research, not a feed.** It is a snapshot from one
point in time, used to calibrate which vehicles the tool should cover. Nothing
here has been imported into the prototype as stock, and it is not evidence of
current availability. The build manual is explicit that this audit is background
research rather than a feed licence or proof of current stock.

**What it proves and what it does not.** An advert is evidence of what a business
*offers for sale*. It is not direct evidence of what that business *buys*. The
buying pattern below is inferred from the composition of the stock, and is
labelled as inference throughout.

## The single most important finding

**33 of the 38 cars are new vehicles with 0–30 km. Only 5 are genuinely used.**

This is not a used-car trading business. It is a **new and nearly-new export
operation**, and that changes what the tool should be good at.

| Condition | Count |
|---|---|
| New vehicle, accident-free | 33 |
| Used vehicle, accident-free | 5 |

The adverts say so directly. The highlight tags on the stock read
`IN STOCK`, `WORLDWIDE EXPORT`, `AVAILABLE NOW`, and the descriptions open with
"IN STOCK – AVAILABLE NOW – EXPORT – WORLDWIDE DELIVERY".

### Why this matters more than anything else in the model

Every one of those 33 cars meets the EU definition of a **new means of
transport** for VAT — under six months since first use *or* no more than
6,000 km. At 0–30 km they all qualify on the mileage limb alone.

That makes the intra-EU VAT treatment the *mechanism of the trade*, not a footnote:
a new means of transport supplied cross-border is handled differently from a used
car, and the buyer accounts for acquisition VAT in their own country.

The prototype already computes this indicator (`new_vehicle_indicator` in
`desk/services/normalization.py`), and the acceptance tests cover it. On this
evidence it should be **promoted from a background check to a headline fact on
the vehicle record**, because it applies to almost the entire stock.

## What they sell, by model family

| Family | Count | Net price band (EUR) | Covered by the tool today |
|---|---:|---|---|
| Mercedes S-Class (S 450, S 580) | 7 | 131,000 – 192,000 | yes |
| BMW 7 Series (740) | 6 | 119,000 – 142,000 | yes |
| Mercedes-AMG G 63 | 5 | 211,000 – 219,900 | yes |
| Mercedes GLE (300, 350, 53, 63) | 5 | 87,000 – 113,000 | **no** |
| BMW X5 | 4 | 75,000 – 104,050 | yes |
| Mercedes GLC 200 | 2 | 65,000 | **no** |
| Audi (RS5, other) | 2 | 119,000 – 119,063 | **no** |
| BMW X7 M60 | 1 | 112,000 | yes |
| Mercedes GLS 450 | 1 | 127,000 | **no** |
| Rolls-Royce Cullinan | 1 | 468,000 | **no** |
| Range Rover | 1 | 167,000 | **no** |
| Ferrari California | 1 | 88,000 | **no** |
| ALPINA B8 | 1 | 99,174 | **no** |
| Mercedes (other) | 1 | 139,669 | **no** |

### The manual's four families were a good call, and there is one clear gap

G 63, S-Class, 7 Series and X5/X7 account for **23 of 38 cars, or 61%** of the
stock. That validates the starting scope in section 1 of the build manual.

The gap worth closing is **the rest of the Mercedes SUV range**: GLE, GLC and GLS
together are 8 cars, or 21% of the stock — a bigger cluster than the G 63. Adding
`mercedes_gle`, `mercedes_glc` and `mercedes_gls` to `SUPPORTED_FAMILIES` would
take coverage from 61% to 82%.

The long tail (Rolls-Royce, Ferrari, ALPINA, Range Rover, Audi) is one car each.
Those are correctly left to go to review rather than guessed at, exactly as the
manual specifies.

## Price bands, against the prototype's synthetic figures

The demo fixtures use a G 63 at €200,000 net acquisition against a €235,000
comparable median. The real adverts show G 63 asking at **€211,000 – €219,900
net**, in a notably tight band across five cars.

So the synthetic figures are the right order of magnitude and the right shape —
a narrow band on a high-value model — but pitched slightly low. The manual's
worked example is fixed by specification and should not be changed; this is worth
knowing when explaining that the demo numbers are plausible rather than arbitrary.

## Tax basis

| VAT rate | Count | Reading |
|---|---:|---|
| 19% | 36 | German rate — consistent with "German edition" origin |
| 21% | 2 | Czech rate |

Every advert carries both a gross and a net price with the rate stated, so the
`price_basis` / `vat_regime` distinction the tool enforces maps directly onto the
source data. **No margin-scheme cars appear in this sample**, which is consistent
with a new-vehicle business: the margin scheme applies to used goods.

## Stock versus allocation

The distinction the tool models is present in the real data:

| State | Count |
|---|---:|
| In stock (`stock_flag` true) | 28 |
| No stock flag | 10 |
| Availability "Available in 4 months after order" | 2 |
| Availability "Available in 6 weeks after order" | 1 |
| Availability "From Nov 10, 2026" | 2 |

Those last five are **allocations or build slots, not cars on the ground** —
precisely the `physical_stock` versus `allocation` distinction, and precisely the
case where a buyer needing immediate stock must get a rejection rather than a
maybe. It is not a hypothetical: it is 13% of this stock.

## Origin

| Origin | Count |
|---|---:|
| German edition | 33 |
| EU edition | 2 |
| Not stated | 3 |

Consistent with sourcing German-specification cars for export.

## Specification patterns

Useful for calibrating what a buyer brief should ask about:

- **Seats:** 33 cars are 5-seat, 2 are 4-seat, 2 are 7-seat, 1 is 6-seat. The
  homologated seat count matters and varies — the rejection case in the tool is
  a real one.
- **Fuel:** 26 petrol, 6 diesel, 8 hybrid of some kind (some overlap).
- **Colour:** heavily concentrated — 16 black metallic, 6 grey metallic, 4 white
  metallic. Colour is plausibly a real buyer requirement.
- **Interior:** 34 of 38 are full leather.

## What they buy — inference, not evidence

Stating the limit first: **these adverts are evidence of what is offered for
sale, not of what is purchased.** With that said, the composition supports a
reasonable inference:

- A stock that is 87% new cars with 0–30 km is not assembled by taking trade-ins.
  It is bought, as new or nearly-new inventory, from within the EU dealer and
  distributor network.
- "German edition" on 33 of 38 points to German-market supply specifically.
- The presence of order-based availability (4 months, 6 weeks) suggests they also
  place orders against demand rather than only buying stock.
- The 5 used cars (Ferrari California at 96k km, two GLE 63, an ALPINA B8) look
  like the exception — possibly taken in part-exchange or bought opportunistically.

**This should be confirmed, not assumed.** It is the first question in
`docs/PILOT_QUESTIONS.md`, and the answer changes what the tool should prioritise:
if they buy new stock from EU networks, supply monitoring matters most; if they
buy against confirmed orders, the buyer-requirement side matters most.

## What I would change in the prototype, on this evidence

In order of value:

1. **Add the Mercedes SUV range** (GLE, GLC, GLS) to `SUPPORTED_FAMILIES`. Takes
   coverage from 61% to 82% of observed stock.
2. **Promote the new-vehicle VAT indicator** from a computed check to a prominent
   fact on the vehicle record and in the call brief. It applies to almost
   everything they list and it is the mechanism of the cross-border trade.
3. **Recalibrate the demo fixtures' G 63 band** towards €211,000–220,000 net, so
   the numbers on screen are recognisable to anyone who knows the stock. The
   manual's worked example stays as specified.
4. **Make allocation versus physical stock more prominent**, since 13% of real
   stock is order-based and a buyer needing a car now must see that immediately.
5. **Treat margin-scheme handling as lower priority than it first appeared.** No
   margin-scheme car appears in this sample. The logic should stay — it is
   correct and cheap — but it is not the common case here.

## What was actually changed, after this was written

Recommendations 1, 2 and 4 are done. Recommendation 3 is deliberately not done, and 5 needed no
code.

| # | Recommendation | Outcome |
|---|---|---|
| 1 | Add GLE, GLC and GLS | Done. Coverage of the observed stock goes from 61% to 82%. |
| 2 | Promote the new-vehicle VAT indicator | Done. It is a line on the vehicle record and a **Cross-border basis** line on every call brief. |
| 3 | Recalibrate the demo G 63 band | **Not done, on purpose.** The manual fixes the worked example at €200,000 against a €235,000 median and acceptance cases assert it. Moving the seed would break mandatory arithmetic to make the demo prettier. The real band is recorded here instead. |
| 4 | Make allocation more prominent | Done. The match screen states it in a sentence, with the buyer's own deadline beside it. |
| 5 | Treat margin-scheme handling as lower priority | No code change. The logic is correct and cheap; it simply is not the common case here. |

Writing the audit also exposed two defects that the synthetic fixtures had hidden, both recorded
in `STATUS.md`: **the S-Class matcher matched no real advert at all**, because listings say
"S 450" and never "S-Class"; and nothing separated an S 450 from an S 580 inside one comparable
set, although the real spread between those two is €131,000 to €192,000.

The fix for the second one then needed a fix of its own. Comparing variant strings exactly splits
"G 63" from "G 63 4MATIC", which is one car described at two levels of detail — and the demo data
carries both spellings. The comparison is now on the numeric designation.

That is the strongest argument in this document for getting hold of real data. Neither defect was
visible from the specification, from the tests, or from the demo. Both were obvious within
minutes of looking at how the adverts are actually written.

## Provenance

- Source: 38 mobile.de detail pages saved locally on 26 September 2026, parsed to
  `inventory-normalized.json`.
- This is a one-off research snapshot. It is not monitored, not refreshed, and
  not evidence of current availability or current pricing.
- It has not been imported into the prototype. The build manual directs that a
  genuine demonstration uses synthetic fixtures or a company-authorised export,
  and the prototype's source register accordingly records mobile.de as `blocked`
  with no adapter implementation.
