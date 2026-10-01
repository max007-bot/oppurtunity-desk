"""Exact money handling, including the ambiguous-separator cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from desk.money import Money, MoneyError, median, parse_money, total


def test_stored_as_integer_minor_units():
    amount = Money.from_decimal("200000", "EUR")
    assert amount.minor_units == 20_000_000
    assert amount.decimal == Decimal("200000")


def test_no_float_creeps_into_arithmetic():
    # 0.1 + 0.2 is the classic binary-float failure; these are exact.
    tenth = Money.from_decimal("0.10")
    fifth = Money.from_decimal("0.20")
    assert (tenth + fifth).minor_units == 30
    assert (tenth + fifth) == Money.from_decimal("0.30")


def test_refuses_to_mix_currencies_without_a_rate():
    with pytest.raises(MoneyError, match="dated FX rate"):
        Money.from_decimal("100", "EUR") + Money.from_decimal("100", "GBP")


def test_unknown_currency_is_rejected_not_assumed():
    with pytest.raises(MoneyError, match="unknown currency"):
        Money(100, "XYZ")


def test_median_is_exact_for_odd_and_even_samples():
    prices = [Money.from_decimal(v) for v in ("228000", "232000", "235000", "239000", "243000")]
    assert median(prices) == Money.from_decimal("235000")
    assert median(prices[:4]) == Money.from_decimal("233500")


def test_median_of_empty_sample_is_an_error_not_zero():
    with pytest.raises(MoneyError):
        median([])


def test_total_sums_exactly():
    lines = [Money.from_decimal(v) for v in ("1500", "1000", "500", "2000", "1500")]
    assert total(lines) == Money.from_decimal("6500")


@pytest.mark.parametrize(
    "raw,locale,expected",
    [
        ("€119.000", "eu", "119000"),
        ("119.000,50", None, "119000.50"),
        ("EUR 119.000,-", "eu", "119000"),
        ("£119,000", "uk", "119000"),
        ("119,000.50", None, "119000.50"),
        ("1.234.567", "eu", "1234567"),
        ("78500", None, "78500"),
        ("119 000", None, "119000"),
    ],
)
def test_parses_eu_and_uk_number_formats(raw, locale, expected):
    assert parse_money(raw, locale=locale).decimal == Decimal(expected)


def test_ambiguous_separator_demands_the_locale_rather_than_guessing():
    """119.000 is 119,000 in the EU and 119.0 in the UK.

    Guessing would be wrong by three orders of magnitude, so the parser refuses.
    """
    with pytest.raises(MoneyError, match="ambiguous separator"):
        parse_money("119.000")
    assert parse_money("119.000", locale="eu").decimal == Decimal("119000")
    assert parse_money("119.000", locale="uk").decimal == Decimal("119.000")


def test_currency_is_detected_from_the_symbol():
    assert parse_money("£78,500", locale="uk").currency == "GBP"
    assert parse_money("€119.000", locale="eu").currency == "EUR"


def test_price_text_without_digits_is_rejected():
    with pytest.raises(MoneyError):
        parse_money("price on application")


def test_negative_is_representable_for_a_loss():
    assert Money.from_decimal("-1500").is_negative
    result = Money.from_decimal("205000") - Money.from_decimal("206500")
    assert result == Money.from_decimal("-1500")
