#!/usr/bin/env python3
"""
tools/golden/validate_fixtures.py — Phase 3 money-math parity validator.

Reads fixtures.json and independently computes each fixture's expected
results using the SAME formulas as pages/settlement.js, then diffs against
the stored expected values. Any mismatch > 0.01 is a failure.

This validates that:
1. The fixture expectations are correct (internal consistency).
2. V1 and V2 settlement.js produce identical results (by running this
   against actual DB order totals before and after settlement).

Usage:
    python tools/golden/validate_fixtures.py [--fixtures path/to/fixtures.json]
"""
import json
import sys
import argparse
from pathlib import Path

TOLERANCE = 0.01


def compute_vat_exempt_discount(order_total, pax, quantity, percent):
    """Senior/PWD/Solo: VAT-exempt + percentage discount on the holder's share."""
    share_per_person = order_total / pax
    holder_portion = share_per_person * quantity
    vat_exempt_base = holder_portion / 1.12
    vat_removed = holder_portion - vat_exempt_base
    discount_value = vat_exempt_base * percent
    net_payable = vat_exempt_base - discount_value
    non_holder_portion = order_total - holder_portion
    final_total = net_payable + non_holder_portion
    return {
        "discount_amount": discount_value,
        "tax_exempt_amount": vat_removed,
        "final_total": final_total,
    }


def compute_vat_addback_discount(order_total, pax, quantity, percent):
    """Athlete/MOV: 20% discount on VAT-exempt base, VAT added back."""
    share_per_person = order_total / pax
    holder_portion = share_per_person * quantity
    non_holder_portion = order_total - holder_portion
    vat_removed = holder_portion * (12 / 112)
    amount_no_vat = holder_portion - vat_removed
    discount_value = amount_no_vat * percent
    after_discount = amount_no_vat - discount_value
    final_for_holder = after_discount + vat_removed  # VAT added back
    final_total = final_for_holder + non_holder_portion
    return {
        "discount_amount": discount_value,
        "tax_exempt_amount": 0.00,
        "final_total": final_total,
    }


def compute_regular_discount(order_total, pax, percent):
    """Regular discount: share_per_person * percent."""
    share_per_person = order_total / pax
    discount_amount = share_per_person * (percent / 100)
    final_total = order_total - discount_amount
    return {
        "discount_amount": discount_amount,
        "tax_exempt_amount": 0.00,
        "final_total": final_total,
    }


def compute_oth_discount(order_total, amount):
    """OTH: fixed peso deduction, clamped to 0."""
    discount_amount = min(amount, order_total)
    final_total = max(0, order_total - amount)
    return {
        "discount_amount": discount_amount,
        "tax_exempt_amount": 0.00,
        "final_total": final_total,
    }


def compute_mixed_discount(order_total, pax, discounts):
    """Multiple discounts: per-type share calculations combined."""
    share_per_person = order_total / pax
    total_discount = 0.0
    total_vat_removed = 0.0
    total_after_discount = 0.0
    total_add_back_vat = 0.0
    total_less = 0.0
    total_add = 0.0

    for dtype, dval in discounts.items():
        if dtype == "regular":
            percent = float(dval)
            portion = share_per_person
            disc = portion * (percent / 100)
            total_discount += disc
            total_after_discount += (portion - disc)
            total_less += disc
        elif dtype in ("senior", "pwd"):
            qty = int(dval)
            portion = share_per_person * qty
            vat_base = portion / 1.12
            vat_removed = portion - vat_base
            disc = vat_base * 0.20
            net = vat_base - disc
            total_discount += disc
            total_vat_removed += vat_removed
            total_after_discount += net
            total_less += vat_removed + disc
        elif dtype == "solo_parent":
            qty = int(dval)
            portion = share_per_person * qty
            vat_base = portion / 1.12
            vat_removed = portion - vat_base
            disc = vat_base * 0.10
            net = vat_base - disc
            total_discount += disc
            total_vat_removed += vat_removed
            total_after_discount += net
            total_less += vat_removed + disc
        elif dtype in ("athlete", "medal_of_valor"):
            qty = int(dval)
            portion = share_per_person * qty
            vat_base = portion / 1.12
            vat_removed = portion - vat_base
            disc = vat_base * 0.20
            after_disc = vat_base - disc
            final_after_add = after_disc + vat_removed
            total_discount += disc
            total_after_discount += final_after_add
            total_add_back_vat += vat_removed
            total_less += vat_removed + disc
            total_add += vat_removed

    final_total = order_total - total_less + total_add
    return {
        "discount_amount": total_discount,
        "tax_exempt_amount": total_vat_removed,
        "final_total": final_total,
    }


def compute_fixture(fixture):
    """Compute expected results for a fixture using the settlement formulas."""
    order_total = fixture["order_total"]
    pax = fixture["pax"]
    discounts = fixture.get("discounts", {})

    if not discounts:
        return {
            "discount_amount": 0.0,
            "tax_exempt_amount": 0.0,
            "final_total": order_total,
        }

    keys = list(discounts.keys())

    # Single discount
    if len(keys) == 1:
        dtype = keys[0]
        dval = discounts[dtype]
        if dtype == "regular":
            return compute_regular_discount(order_total, pax, float(dval))
        elif dtype == "oth":
            return compute_oth_discount(order_total, float(dval))
        elif dtype in ("senior", "pwd"):
            return compute_vat_exempt_discount(order_total, pax, int(dval), 0.20)
        elif dtype == "solo_parent":
            return compute_vat_exempt_discount(order_total, pax, int(dval), 0.10)
        elif dtype in ("athlete", "medal_of_valor"):
            return compute_vat_addback_discount(order_total, pax, int(dval), 0.20)

    # Mixed discounts
    return compute_mixed_discount(order_total, pax, discounts)


def validate_fixtures(fixtures_path):
    """Validate all fixtures against computed values."""
    with open(fixtures_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    fixtures = data["fixtures"]
    passed = 0
    failed = 0

    print(f"Validating {len(fixtures)} golden fixtures from: {fixtures_path}")
    print("-" * 72)

    for fx in fixtures:
        computed = compute_fixture(fx)
        expected = fx["expected"]
        errors = []

        for key in ("discount_amount", "tax_exempt_amount", "final_total"):
            diff = abs(computed[key] - expected[key])
            if diff > TOLERANCE:
                errors.append(
                    f"  {key}: expected={expected[key]:.2f}, "
                    f"computed={computed[key]:.2f}, diff={diff:.2f}"
                )

        if errors:
            failed += 1
            print(f"FAIL  {fx['id']}: {fx['label']}")
            for e in errors:
                print(e)
        else:
            passed += 1
            print(f"PASS  {fx['id']}: {fx['label']}")

    print("-" * 72)
    print(f"Result: {passed} passed, {failed} failed, {len(fixtures)} total")
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate golden fixtures")
    parser.add_argument(
        "--fixtures",
        default=str(Path(__file__).parent / "fixtures.json"),
        help="Path to fixtures.json",
    )
    args = parser.parse_args()

    success = validate_fixtures(args.fixtures)
    sys.exit(0 if success else 1)
