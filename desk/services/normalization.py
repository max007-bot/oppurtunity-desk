"""Normalising source text into reviewable canonical facts.

Two rules drive this module:

* An unsupported model variant goes to review rather than being guessed.
* A trim package that merely borrows a performance name ("AMG Line") is never
  treated as the performance model ("AMG G 63").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from ..config import PriceBasis, VatRegime

# Model families the prototype supports out of the box. Anything else can still
# be entered, but resolves to review rather than a canonical family.
SUPPORTED_FAMILIES: dict[str, dict[str, object]] = {
    "mercedes_g63": {
        "label": "Mercedes-AMG G 63",
        "make": "Mercedes-Benz",
        "must_include": ("g63", "g 63"),
        "generation_hints": {"w463a": ("2018", "w463a"), "w463": ("w463",)},
    },
    "mercedes_s_class": {
        "label": "Mercedes-Benz S-Class",
        "make": "Mercedes-Benz",
        # The trim designation matters as much as the family name. Real adverts
        # are titled "S 450" or "S 580" and never say "S-Class", so a matcher
        # built only from the family name missed every one of them.
        "must_include": (
            "s class",
            "s-class",
            "sklasse",
            "s klasse",
            "s 350",
            "s 400",
            "s 450",
            "s 500",
            "s 580",
            "s 63",
            "s 680",
        ),
        "generation_hints": {"w223": ("w223", "2021"), "w222": ("w222",)},
    },
    "bmw_7_series": {
        "label": "BMW 7 Series",
        "make": "BMW",
        "must_include": ("7 series", "7er", "serie 7", "730", "740", "750", "760", "i7"),
        "generation_hints": {"g70": ("g70", "2022"), "g11": ("g11", "g12")},
    },
    "bmw_x5": {
        "label": "BMW X5",
        "make": "BMW",
        "must_include": ("x5",),
        "generation_hints": {"g05": ("g05",)},
    },
    "bmw_x7": {
        "label": "BMW X7",
        "make": "BMW",
        "must_include": ("x7",),
        "generation_hints": {"g07": ("g07",)},
    },
    "mercedes_gle": {
        "label": "Mercedes-Benz GLE",
        "make": "Mercedes-Benz",
        "must_include": ("gle",),
        "generation_hints": {"w167": ("w167", "2019"), "w166": ("w166",)},
    },
    "mercedes_glc": {
        "label": "Mercedes-Benz GLC",
        "make": "Mercedes-Benz",
        "must_include": ("glc",),
        "generation_hints": {"x254": ("x254", "2022"), "x253": ("x253",)},
    },
    "mercedes_gls": {
        "label": "Mercedes-Benz GLS",
        "make": "Mercedes-Benz",
        "must_include": ("gls",),
        "generation_hints": {"x167": ("x167", "2019")},
    },
}

# Tokens that turn a performance badge into a styling package or a modification.
# Their presence blocks automatic equivalence with the real performance model.
LOOKALIKE_TOKENS = (
    "amg line",
    "amg-line",
    "amgline",
    "amg styling",
    "amg sport",
    "amg package",
    "amg paket",
    "m sport",
    "m-sport",
    "m paket",
    "m package",
    "look",
    "bodykit",
    "body kit",
    "styling kit",
    "replica",
    "conversion",
    "widebody",
    "tuned",
    "optic",
)

# Real performance designations, kept separate from the lookalike list above.
PERFORMANCE_BADGES = (
    "g63",
    "g 63",
    "g65",
    "s63",
    "s 63",
    "m760",
    "x5 m",
    "x7 m",
    "gle 53",
    "gle 63",
    "glc 63",
    "gls 63",
)


def _mentions(body: str, needle: str) -> bool:
    """Does ``body`` contain ``needle`` at the start of a word?

    A plain substring test is wrong in both directions here. "gle" appears inside
    "single", and "s 63" appears inside "gls 63", which would make a GLS 63 match
    the S-Class family as well as its own. A *trailing* boundary would be wrong
    too, because "740" has to keep matching "740d". So the boundary is required
    only at the start.
    """
    return re.search(r"\b" + re.escape(needle), body) is not None


def family_label(family: str | None) -> str:
    """The name a person would say, for a stored family key.

    Briefs and openers are read aloud, so they must not contain an internal slug
    such as ``mercedes_g63``.
    """
    if not family:
        return "this model"
    spec = SUPPORTED_FAMILIES.get(family)
    if spec is not None:
        return str(spec["label"])
    return family.replace("_", " ")


def display_name(family: str | None, variant: str | None) -> str:
    """The name a person would write, from a family and a free-text variant.

    The two overlap constantly, because a variant is usually written to stand on
    its own: family "Mercedes-AMG G 63" beside variant "G 63 4MATIC" would read
    "Mercedes-AMG G 63 G 63 4MATIC" if they were simply concatenated. The longest
    run of words that ends the family and begins the variant is written once.
    """
    name = family_label(family)
    if not variant:
        return name
    left = name.split()
    right = variant.split()
    overlap = 0
    for size in range(min(len(left), len(right)), 0, -1):
        if [w.lower() for w in left[-size:]] == [w.lower() for w in right[:size]]:
            overlap = size
            break
    merged = left + right[overlap:]
    return " ".join(merged)


def slugify(text: str) -> str:
    """Lowercase ASCII slug used for comparing source strings.

    Underscores and hyphens become spaces so an internal family key such as
    ``mercedes_s_class`` still matches the advert wording ``S-Class``.
    """
    normalised = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    spaced = re.sub(r"[_\-]+", " ", stripped.lower())
    return re.sub(r"\s+", " ", spaced).strip()


def variant_designation(text: str | None) -> str | None:
    """The numeric model designation inside a free-text variant, if there is one.

    Variants arrive as advertising copy: "S 450 4MATIC L", "G 63", "GLE 350 de".
    Within one generation, what separates two genuinely different cars is the
    designation number; what differs between two records of the *same* car is
    usually whichever suffix the person typing chose to include. So only the
    first number is compared and the rest is ignored, which keeps a G 63 and a
    G 63 4MATIC together while holding an S 450 apart from an S 580.

    It is deliberately conservative, and it has a known limit: it does not tell
    an S 450 from an S 450 L, because both designate 450. It narrows a comparable
    set only where the evidence is unambiguous.
    """
    if not text:
        return None
    match = re.search(r"[0-9]+", slugify(text))
    return match.group(0) if match else None


@dataclass
class ModelResolution:
    """Outcome of resolving free text to a canonical model family."""

    family: str | None
    label: str
    status: str  # resolved | review_required | lookalike_conflict
    reasons: list[str] = field(default_factory=list)
    generation: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and self.family is not None


def resolve_model(text: str, *, declared_family: str | None = None) -> ModelResolution:
    """Resolve advert text to a supported family, or send it to review.

    ``declared_family`` is an explicit family key from an authorised import. It is
    still checked against the text, so a mislabelled row surfaces as a conflict
    instead of silently overriding the evidence.
    """
    body = slugify(text)
    if not body and not declared_family:
        return ModelResolution(None, "unknown", "review_required", ["no model text supplied"])

    lookalikes = [token for token in LOOKALIKE_TOKENS if token in body]
    has_real_badge = any(_mentions(body, badge) for badge in PERFORMANCE_BADGES)

    candidates: list[str] = []
    for key, spec in SUPPORTED_FAMILIES.items():
        needles: Iterable[str] = spec["must_include"]  # type: ignore[assignment]
        if any(_mentions(body, needle) for needle in needles):
            candidates.append(key)

    # "G 63 AMG Line" style text: a trim package wearing a performance name.
    if lookalikes and not has_real_badge:
        performance_families = [c for c in candidates if c == "mercedes_g63"]
        if performance_families:
            return ModelResolution(
                None,
                "unknown",
                "lookalike_conflict",
                [
                    f"text contains styling package token(s) {', '.join(lookalikes)} "
                    f"without an exact performance designation; exact model review required"
                ],
            )

    if lookalikes and has_real_badge:
        # Both present: a genuine G 63 can carry an "AMG Line" interior option,
        # but the contradiction is worth a human look before it is canonical.
        return ModelResolution(
            None,
            "unknown",
            "lookalike_conflict",
            [
                f"text mixes a performance designation with styling token(s) "
                f"{', '.join(lookalikes)}; confirm the exact model"
            ],
        )

    if declared_family:
        key = declared_family.strip().lower()
        if key not in SUPPORTED_FAMILIES:
            return ModelResolution(
                None,
                declared_family,
                "review_required",
                [f"declared family {declared_family!r} is not a supported family"],
            )
        if candidates and key not in candidates:
            return ModelResolution(
                None,
                str(SUPPORTED_FAMILIES[key]["label"]),
                "review_required",
                [
                    f"declared family {key!r} contradicts the source text, which reads "
                    f"as {', '.join(candidates)}"
                ],
            )
        return ModelResolution(
            key,
            str(SUPPORTED_FAMILIES[key]["label"]),
            "resolved",
            [],
            _generation(key, body),
        )

    if len(candidates) == 1:
        key = candidates[0]
        return ModelResolution(
            key,
            str(SUPPORTED_FAMILIES[key]["label"]),
            "resolved",
            [],
            _generation(key, body),
        )
    if len(candidates) > 1:
        return ModelResolution(
            None,
            "unknown",
            "review_required",
            [f"text matches several families: {', '.join(candidates)}"],
        )
    return ModelResolution(
        None,
        "unknown",
        "review_required",
        ["model text does not match a supported family; enter it for review"],
    )


def _generation(family: str, body: str) -> str | None:
    hints = SUPPORTED_FAMILIES[family].get("generation_hints", {})
    for generation, needles in hints.items():  # type: ignore[union-attr]
        if any(needle in body for needle in needles):
            return generation
    return None


# -- tax basis -----------------------------------------------------------


@dataclass
class BasisCompatibility:
    compatible: bool
    reason: str


def basis_compatible(
    left_basis: PriceBasis | str,
    left_regime: VatRegime | str,
    right_basis: PriceBasis | str,
    right_regime: VatRegime | str,
) -> BasisCompatibility:
    """Decide whether two prices may be compared or combined.

    Only net-to-net or gross-to-gross under a known, identical regime qualifies.
    Unknown regimes, the margin scheme and mismatched bases are all refused: the
    prototype does not divide a used-car gross price by a VAT rate.
    """
    lb = PriceBasis(left_basis)
    rb = PriceBasis(right_basis)
    lr = VatRegime(left_regime)
    rr = VatRegime(right_regime)

    if PriceBasis.UNKNOWN in (lb, rb):
        return BasisCompatibility(False, "one price basis is unknown")
    if VatRegime.UNKNOWN in (lr, rr):
        return BasisCompatibility(False, "one VAT regime is unknown")
    if lb is not rb:
        return BasisCompatibility(
            False, f"price basis differs ({lb.value} versus {rb.value}) and is not converted"
        )
    if VatRegime.MARGIN in (lr, rr) and lr is not rr:
        return BasisCompatibility(
            False, "margin-scheme and standard-VAT prices are not automatically comparable"
        )
    if lr is not rr:
        return BasisCompatibility(
            False, f"VAT regime differs ({lr.value} versus {rr.value})"
        )
    if lr is VatRegime.OTHER:
        return BasisCompatibility(False, "regime recorded as other; needs a manual review")
    return BasisCompatibility(True, f"both {lb.value} under the {lr.value} regime")


# -- EU new-vehicle indicator -------------------------------------------

NEW_VEHICLE_MONTHS = 6
NEW_VEHICLE_KM = 6000


@dataclass
class NewVehicleIndicator:
    """Age/mileage indicator only.

    The EU definition of a new means of transport for car VAT can be met by a car
    within six months of first use *or* with no more than 6,000 km. This is an
    indicator the prototype can compute when the dates are known; it is not the
    complete tax treatment of a live sale.
    """

    indicated_new: bool | None
    reasons: list[str]

    @property
    def determinable(self) -> bool:
        return self.indicated_new is not None


def new_vehicle_indicator(
    *,
    first_registration: datetime | None,
    mileage_km: int | None,
    at: datetime,
) -> NewVehicleIndicator:
    reasons: list[str] = []
    within_six_months: bool | None = None
    under_km: bool | None = None

    if first_registration is not None:
        # Approximate six months as 183 days; the boundary case belongs to a human.
        within_six_months = (at - first_registration) <= timedelta(days=183)
        reasons.append(
            f"first use {first_registration.date().isoformat()} is "
            f"{'within' if within_six_months else 'more than'} six months before "
            f"{at.date().isoformat()}"
        )
    else:
        reasons.append("first registration date unknown")

    if mileage_km is not None:
        under_km = mileage_km <= NEW_VEHICLE_KM
        reasons.append(
            f"{mileage_km:,} km is {'at or under' if under_km else 'above'} "
            f"the {NEW_VEHICLE_KM:,} km threshold"
        )
    else:
        reasons.append("mileage unknown")

    if within_six_months is True or under_km is True:
        reasons.append("either limb is sufficient, so the indicator is positive")
        return NewVehicleIndicator(True, reasons)
    if within_six_months is None or under_km is None:
        reasons.append("one limb is unknown, so the indicator cannot be determined")
        return NewVehicleIndicator(None, reasons)
    reasons.append("neither limb is met")
    return NewVehicleIndicator(False, reasons)


# -- assorted text helpers ----------------------------------------------


def normalise_powertrain(text: str | None) -> str | None:
    if not text:
        return None
    body = slugify(text)
    table = {
        "petrol": ("petrol", "benzin", "gasoline", "essence"),
        "diesel": ("diesel", "tdi", "cdi"),
        "hybrid": ("hybrid", "phev", "plug-in", "plug in"),
        "electric": ("electric", "bev", "elektro", "ev"),
    }
    for canonical, needles in table.items():
        if any(needle in body for needle in needles):
            return canonical
    return None


def normalise_steering(text: str | None) -> str:
    if not text:
        return "unknown"
    body = slugify(text)
    if body in {"lhd", "left", "left hand drive", "lenkung links"} or "left" in body:
        return "lhd"
    if body in {"rhd", "right", "right hand drive"} or "right" in body:
        return "rhd"
    return "unknown"


def neutralise_csv_cell(value: object) -> str:
    """Stop a spreadsheet interpreting an exported cell as a formula.

    A company name legitimately starting with ``=`` or ``+`` is prefixed with an
    apostrophe so the exported file cannot execute anything on open.
    """
    if value is None:
        return ""
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text
