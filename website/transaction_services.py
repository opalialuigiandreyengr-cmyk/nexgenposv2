import json

from .models import OrderAuditLog, Settlement


DISCOUNT_CUSTOMER_TYPES = {
    "senior",
    "pwd",
    "athlete",
    "medal_of_valor",
    "solo_parent",
}


def _json_form_value(form, key):
    raw_value = form.get(key)
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except Exception:
        return None


def _collect_discount_identity(form, order_discount_type, discount_quantity, discount_details):
    discount_names = []
    discount_ids = []
    is_mixed_discount = "," in order_discount_type

    if is_mixed_discount:
        discount_types = [discount_type.strip() for discount_type in order_discount_type.split(",")]
        for discount_type in discount_types:
            if discount_type in DISCOUNT_CUSTOMER_TYPES:
                quantity = 1
                if isinstance(discount_details, dict):
                    quantity = discount_details.get(discount_type, 1)
                for index in range(1, quantity + 1):
                    name = form.get(f"modal_discount_name_{discount_type}_{index}", "").strip()
                    id_value = form.get(f"modal_discount_id_{discount_type}_{index}", "").strip()
                    if name:
                        discount_names.append(name)
                    if id_value:
                        discount_ids.append(id_value)
    elif order_discount_type in DISCOUNT_CUSTOMER_TYPES:
        for index in range(1, discount_quantity + 1):
            name = form.get(f"modal_discount_name_{order_discount_type}_{index}", "").strip()
            id_value = form.get(f"modal_discount_id_{order_discount_type}_{index}", "").strip()
            if name:
                discount_names.append(name)
            if id_value:
                discount_ids.append(id_value)
    elif isinstance(discount_details, dict):
        for discount_type, value in discount_details.items():
            if discount_type != "no_discount" and isinstance(value, int) and value > 0:
                for index in range(1, value + 1):
                    name = form.get(f"modal_discount_name_{discount_type}_{index}", "").strip()
                    id_value = form.get(f"modal_discount_id_{discount_type}_{index}", "").strip()
                    if name:
                        discount_names.append(name)
                    if id_value:
                        discount_ids.append(id_value)

    if not discount_names:
        discount_name = ""
    elif is_mixed_discount:
        discount_name = _join_mixed_discount_values(
            discount_names,
            order_discount_type,
            discount_details,
        )
    else:
        discount_name = ",".join(discount_names)

    if not discount_ids:
        discount_id = ""
    elif is_mixed_discount:
        discount_id = _join_mixed_discount_values(
            discount_ids,
            order_discount_type,
            discount_details,
        )
    else:
        discount_id = ",".join(discount_ids)

    return discount_name, discount_id


def _join_mixed_discount_values(values, order_discount_type, discount_details):
    values_with_type = []
    value_index = 0
    discount_types = sorted(
        discount_type.strip()
        for discount_type in order_discount_type.split(",")
        if discount_type.strip() not in {"regular", "oth"}
    )

    for discount_type in discount_types:
        quantity = 1
        if isinstance(discount_details, dict):
            quantity = discount_details.get(discount_type, 1)
        for _ in range(quantity):
            if value_index < len(values):
                values_with_type.append(f"{discount_type}:{values[value_index]}")
                value_index += 1

    return ",".join(values_with_type) if values_with_type else ",".join(values)


def _settlement_float(form, key, default):
    value = form.get(key)
    return float(value) if value is not None else default


def build_settlement_from_form(form, order, cashier_id):
    payment_method = form.get("payment_method", "cash")
    card_type = form.get("card_type", "gcash")
    cash_received = form.get("cash_received", 0, type=float)
    gift_check_amount = form.get("gift_check_amount", 0, type=float)
    gift_check_number = form.get("gift_check_number", "").strip()
    cheque_amount = form.get("cheque_amount", 0, type=float)
    cheque_number = form.get("cheque_number", "").strip()

    if payment_method != "gift_check":
        gift_check_amount = 0
        gift_check_number = ""
    if payment_method != "cheque":
        cheque_amount = 0
        cheque_number = ""
    final_total = form.get("final_total", order.total, type=float)
    discount_amount = form.get("discount_amount", 0, type=float)
    tax_exempt_amount = form.get("tax_exempt_amount", 0, type=float)

    order_discount_type = form.get("order_discount", "no_discount")
    regular_discount_percent = form.get("regular_discount_percent", 0, type=float)
    oth_discount_amount = form.get("oth_discount_amount", 0, type=float)
    discount_quantity = form.get("discount_quantity", 0, type=int)
    total_no_pax = form.get("total_no_pax", 1, type=int)
    manual_si_number = form.get("remarks", "").strip()
    card_swipe_json = form.get("card_swipe_json", "").strip()
    discount_details = _json_form_value(form, "discount_details")
    discount_breakdown = _json_form_value(form, "discount_breakdown")

    discount_name, discount_id = _collect_discount_identity(
        form,
        order_discount_type,
        discount_quantity,
        discount_details,
    )

    if "," in order_discount_type:
        discount_amount = form.get("discount_amount", 0, type=float)
        oth_discount_amount = 0
        if "regular" not in order_discount_type.split(","):
            regular_discount_percent = 0
    elif order_discount_type == "regular":
        discount_amount = form.get("discount_amount", 0, type=float)
        oth_discount_amount = 0
    elif order_discount_type == "oth":
        discount_amount = oth_discount_amount
        regular_discount_percent = 0
    elif order_discount_type in DISCOUNT_CUSTOMER_TYPES:
        discount_amount = form.get("discount_amount", 0, type=float)
        oth_discount_amount = 0
        regular_discount_percent = 0
    else:
        discount_amount = 0
        regular_discount_percent = None
        oth_discount_amount = 0

    vat_sales = _settlement_float(form, "vat_sales", order.subtotal)
    vat_exempt_sale = _settlement_float(form, "vat_exempt_sale", 0)
    zero_rated_sales = _settlement_float(form, "zero_rated_sales", 0)
    total_sale = _settlement_float(form, "total_sale", order.total)
    vat_amount = _settlement_float(form, "vat_amount", order.vat)
    amount_due = _settlement_float(form, "amount_due", order.total)

    settlement = Settlement(
        order_id=order.id,
        payment_method=payment_method,
        card_type=card_type if payment_method == "card" else None,
        cash_received=cash_received if cash_received > 0 else None,
        gift_check_amount=gift_check_amount if gift_check_amount > 0 else None,
        gift_check_number=gift_check_number if gift_check_amount > 0 else None,
        cheque_amount=cheque_amount if cheque_amount > 0 else None,
        cheque_number=cheque_number if cheque_amount > 0 else None,
        final_total=final_total,
        tax_exempt_amount=tax_exempt_amount,
        discount_amount=discount_amount,
        vat_sales=vat_sales,
        vat_exempt_sale=vat_exempt_sale,
        zero_rated_sales=zero_rated_sales,
        total_sale=total_sale,
        vat_amount=vat_amount,
        amount_due=amount_due,
        order_discount_type=order_discount_type,
        regular_discount_percent=regular_discount_percent,
        oth_discount_amount=oth_discount_amount,
        discount_quantity=discount_quantity,
        discount_name=discount_name,
        discount_id=discount_id,
        total_no_pax=total_no_pax,
        discount_breakdown=json.dumps(discount_breakdown) if discount_breakdown else None,
        cashier_id=cashier_id,
        manual_si_number=manual_si_number if manual_si_number else None,
        card_swipe_json=card_swipe_json if card_swipe_json else None,
    )

    return settlement, {
        "payment_method": payment_method,
        "cash_received": cash_received,
        "gift_check_amount": gift_check_amount,
        "cheque_amount": cheque_amount,
        "final_total": final_total,
        "discount_amount": discount_amount,
        "manual_si_number": manual_si_number,
    }


def create_order_audit_logs(order, event_type, reason, reference_no, cashier_id):
    audit_logs = []

    for item in order.items:
        audit_log = OrderAuditLog(
            order_id=order.id,
            product_name=item.product_name,
            original_quantity=item.quantity,
            modified_qty=item.quantity,
            price=item.price,
            reason=reason,
            event_type=event_type,
            reference_no=reference_no,
            cashier_id=cashier_id,
        )
        audit_logs.append(audit_log)

    return audit_logs
