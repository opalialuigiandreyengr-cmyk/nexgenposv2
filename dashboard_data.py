"""POS_V2/dashboard_data.py — V2-side dashboard enrichment (read-only).

The legacy `main.dashboard` context ships no weekly series, no MTD/YTD
totals and no per-category product figures. This module computes them from
the SAME settled-orders semantics as the legacy helpers:

  * net  = SUM(Settlement.final_total) over completed orders   (legacy
    `_dashboard_net_sales`)
  * gross = SUM(Order.total) over the matching completed order ids (legacy
    `_dashboard_gross_sales`)
  * all ranges are based on `get_philippine_time()` (BIR/local day)

Every function returns plain JSON-safe structures and is wrapped by the
injection hook in app.py with try/except so a failure degrades to empty
defaults — the dashboard must never break because of enrichment.

Exported for the hook:

  period_stats()              -> {"mtd": {"net", "prior"}, "ytd": {...}}
  weekly_sales_data(weeks=8)  -> [{"date", "display_date", "net_sales", "gross_sales"}]
  top_products_by_category()  -> {"today"|"yesterday"|"month": {"All": [...],
                                 "<category>": [...], "_categories": [...]}}
"""

from datetime import date, datetime, timedelta

from website import db
from website.helpers import get_philippine_time
from website.main import _dashboard_gross_sales, _dashboard_net_sales
from website.models import Order, OrderItem, Product, Settlement

WEEK_BUCKETS = 8
TOP_PER_SCOPE = 8
UNCATEGORIZED = "Uncategorized"


def _start_of_day(day):
    return datetime.combine(day, datetime.min.time())


def _completed_order_ids(start, end, inclusive_end=False):
    """Order ids of completed orders settled in [start, end) — the exact
    basis the legacy dashboard uses for every range."""
    query = db.session.query(Order.id).join(Settlement).filter(
        Order.status == "completed",
        Settlement.timestamp >= start,
    )
    if inclusive_end:
        query = query.filter(Settlement.timestamp <= end)
    else:
        query = query.filter(Settlement.timestamp < end)
    return [row[0] for row in query.all()]


# ---------------------------------------------------------------------------
# MTD / YTD period cards
# ---------------------------------------------------------------------------

def _month_prior_range(today):
    """Previous month, 1st -> same day-of-month clamped to its length."""
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)  # last day of previous month
    prior_start = _start_of_day(prev_month_end.replace(day=1))
    prior_last_day = min(today.day, prev_month_end.day)
    prior_end = _start_of_day(prev_month_end.replace(day=prior_last_day)) + timedelta(days=1)
    return prior_start, prior_end


def _year_prior_range(today):
    """Last year, Jan 1 -> same day-of-month (Feb 29 clamps to Feb 28)."""
    try:
        prior_day = today.replace(year=today.year - 1)
    except ValueError:  # Feb 29 on a non-leap year
        prior_day = today.replace(year=today.year - 1, day=28)
    prior_start = _start_of_day(prior_day.replace(month=1, day=1))
    prior_end = _start_of_day(prior_day) + timedelta(days=1)
    return prior_start, prior_end


def period_stats():
    """Net sales MTD/YTD plus the same-length prior period (for deltas)."""
    today = get_philippine_time().date()
    start_of_today = _start_of_day(today)
    end_of_today = start_of_today + timedelta(days=1)

    mtd_start = _start_of_day(today.replace(day=1))
    prior_mtd_start, prior_mtd_end = _month_prior_range(today)

    ytd_start = _start_of_day(today.replace(month=1, day=1))
    prior_ytd_start, prior_ytd_end = _year_prior_range(today)

    return {
        "mtd": {
            "net": round(_dashboard_net_sales(mtd_start, end_of_today), 2),
            "prior": round(_dashboard_net_sales(prior_mtd_start, prior_mtd_end), 2),
        },
        "ytd": {
            "net": round(_dashboard_net_sales(ytd_start, end_of_today), 2),
            "prior": round(_dashboard_net_sales(prior_ytd_start, prior_ytd_end), 2),
        },
    }


# ---------------------------------------------------------------------------
# Weekly sales trend (Monday-start buckets, current week capped at today)
# ---------------------------------------------------------------------------

def weekly_sales_data(weeks=WEEK_BUCKETS):
    """Last `weeks` Monday-start buckets; the current (partial) week is
    capped at the end of today. Same settled-orders basis as the daily
    series."""
    today = get_philippine_time().date()
    end_of_today = _start_of_day(today) + timedelta(days=1)
    current_monday = today - timedelta(days=today.weekday())

    series = []
    for offset in range(weeks - 1, -1, -1):
        week_start_date = current_monday - timedelta(days=7 * offset)
        week_start = _start_of_day(week_start_date)
        week_end = week_start + timedelta(days=7)
        if week_end > end_of_today:
            week_end = end_of_today  # partial current week

        ids = _completed_order_ids(week_start, week_end)
        series.append({
            "date": week_start_date.strftime("%Y-%m-%d"),
            "display_date": week_start_date.strftime("%m/%d"),
            "gross_sales": round(_dashboard_gross_sales(ids), 2),
            "net_sales": round(_dashboard_net_sales(week_start, week_end), 2),
        })
    return series


# ---------------------------------------------------------------------------
# Top products per category (qty + peso per row)
# ---------------------------------------------------------------------------

def _top_product_rows(ids):
    """Per product: name, quantity, peso (qty x price), category.

    Category comes from Product.category via product_id; order items whose
    product was deleted fall back to 'Uncategorized' (outer join). Returns
    the union of the top-N by quantity and top-N by peso so the client can
    re-sort by either metric without a second request."""
    if not ids:
        return []
    rows = db.session.query(
        OrderItem.product_name,
        db.func.sum(OrderItem.quantity).label("total_qty"),
        db.func.sum(OrderItem.quantity * OrderItem.price).label("total_peso"),
        Product.category,
    ).outerjoin(Product, Product.id == OrderItem.product_id).filter(
        OrderItem.order_id.in_(ids)
    ).group_by(
        OrderItem.product_name, OrderItem.product_id, Product.category
    ).all()

    products = []
    for name, qty, peso, category in rows:
        products.append({
            "product_name": name,
            "quantity": round(float(qty or 0), 2),
            "peso": round(float(peso or 0), 2),
            "category": ((category or "").strip() or UNCATEGORIZED),
        })

    by_qty = sorted(products, key=lambda item: item["quantity"], reverse=True)[:TOP_PER_SCOPE]
    by_peso = sorted(products, key=lambda item: item["peso"], reverse=True)[:TOP_PER_SCOPE]
    merged, seen = [], set()
    for item in by_qty + by_peso:
        if item["product_name"] not in seen:
            seen.add(item["product_name"])
            merged.append(item)
    return merged


def _scope_order_ids(scope):
    """Settlement-timestamp ranges per scope, mirroring the legacy route:
    today/yesterday are [start, start+1d); month is inclusive to end-of-day."""
    today = get_philippine_time().date()
    if scope == "today":
        start = _start_of_day(today)
        return _completed_order_ids(start, start + timedelta(days=1))
    if scope == "yesterday":
        start = _start_of_day(today - timedelta(days=1))
        return _completed_order_ids(start, start + timedelta(days=1))
    start = _start_of_day(today.replace(day=1))
    end = datetime.combine(today, datetime.max.time())
    return _completed_order_ids(start, end, inclusive_end=True)


def top_products_by_category():
    """Per scope (today/yesterday/month): {'All': [...], '<category>': [...],
    '_categories': [...]} — every category that actually has rows."""
    result = {}
    for scope in ("today", "yesterday", "month"):
        rows = _top_product_rows(_scope_order_ids(scope))
        categories = sorted(
            {row["category"] for row in rows},
            key=str.lower,
        )
        by_category = {"All": rows, "_categories": categories}
        for category in categories:
            by_category[category] = [
                row for row in rows if row["category"] == category
            ]
        result[scope] = by_category
    return result


# ---------------------------------------------------------------------------
# Arbitrary date range series + full snapshot (global dashboard range picker)
# ---------------------------------------------------------------------------

MAX_DAILY_POINTS = 400
MAX_WEEKLY_BUCKETS = 60
MAX_MONTHLY_BUCKETS = 60


def _settlement_day_sums(start_dt, end_dt):
    """Per-day {net, gross} over completed settlements in [start_dt, end_dt).

    One pass over the range's completed settlements (the exact basis of the
    legacy helpers): net = SUM(final_total) per settlement row, gross =
    SUM(Order.total) per settlement row — duplication across multiple
    settlements of one order is preserved, like every legacy bucket."""
    net_by_day = {}
    gross_by_day = {}
    rows = db.session.query(
        Settlement.timestamp, Settlement.final_total, Order.total
    ).join(Order).filter(
        Order.status == "completed",
        Settlement.timestamp >= start_dt,
        Settlement.timestamp < end_dt,
    ).all()
    for timestamp, final_total, total in rows:
        day = timestamp.date().isoformat()
        net_by_day[day] = net_by_day.get(day, 0.0) + float(final_total or 0)
        gross_by_day[day] = gross_by_day.get(day, 0.0) + float(total or 0)
    return net_by_day, gross_by_day


def _bucket_series(start_date, end_date, net_by_day, gross_by_day):
    """daily (1 point/day) / weekly (7-day buckets from start) / monthly
    (calendar months) series derived from per-day sums — no re-querying."""
    def bucket_sum(day_start, day_end):
        """(gross, net) for the inclusive day range, defaults to zero when a
        day has no settlements."""
        gross = 0.0
        net = 0.0
        day = day_start
        while day <= day_end:
            key = day.isoformat()
            gross += gross_by_day.get(key, 0.0)
            net += net_by_day.get(key, 0.0)
            day += timedelta(days=1)
        return round(gross, 2), round(net, 2)

    daily = []
    day = start_date
    while day <= end_date:
        gross, net = bucket_sum(day, day)
        daily.append({
            "date": day.isoformat(),
            "display_date": day.strftime("%m/%d"),
            "gross_sales": gross,
            "net_sales": net,
        })
        day += timedelta(days=1)

    weekly = []
    bucket_start = start_date
    while bucket_start <= end_date:
        bucket_end = min(bucket_start + timedelta(days=6), end_date)
        gross, net = bucket_sum(bucket_start, bucket_end)
        weekly.append({
            "date": bucket_start.isoformat(),
            "display_date": bucket_start.strftime("%m/%d"),
            "gross_sales": gross,
            "net_sales": net,
        })
        bucket_start = bucket_end + timedelta(days=1)

    monthly = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        month_start = date(year, month, 1)
        month_end = min(
            (date(year + 1, 1, 1) if month == 12
             else date(year, month + 1, 1)) - timedelta(days=1),
            end_date,
        )
        gross, net = bucket_sum(month_start, month_end)
        monthly.append({
            "date": month_start.strftime("%Y-%m"),
            "display_date": month_start.strftime("%b %Y"),
            "gross_sales": gross,
            "net_sales": net,
        })
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return {"daily": daily, "weekly": weekly, "monthly": monthly}


def _sum_items(ids):
    """Total quantity sold across the given order ids (legacy items_sold)."""
    if not ids:
        return 0
    result = db.session.query(db.func.sum(OrderItem.quantity)).filter(
        OrderItem.order_id.in_(ids)
    ).scalar()
    return int(result or 0)


def sales_series_for_range(start_iso, end_iso):
    """Daily / weekly / monthly series for an inclusive date range.

    Rows mirror the legacy dashboard shape ({date, display_date, gross_sales,
    net_sales}); net and gross use the exact settled-orders semantics of
    _dashboard_net_sales / _dashboard_gross_sales, but are aggregated in ONE
    pass over the range's completed settlements (row-by-row sums in Python)
    so any bucket size can be derived without re-querying.

    Returns {"success": bool, "range": {...}, "daily": [...], "weekly":
    [...], "monthly": [...]}. Invalid dates or oversized ranges return
    success False with an error message (the client keeps the old chart).
    """
    try:
        start_date = datetime.strptime(start_iso, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": "Invalid date format - use YYYY-MM-DD.",
            "daily": [], "weekly": [], "monthly": [],
        }
    if end_date < start_date:
        return {
            "success": False,
            "error": "Start date must be on or before end date.",
            "daily": [], "weekly": [], "monthly": [],
        }

    day_count = (end_date - start_date).days + 1
    week_buckets = (day_count + 6) // 7
    month_buckets = (
        (end_date.year - start_date.year) * 12
        + end_date.month - start_date.month + 1
    )
    if (day_count > MAX_DAILY_POINTS
            or week_buckets > MAX_WEEKLY_BUCKETS
            or month_buckets > MAX_MONTHLY_BUCKETS):
        return {
            "success": False,
            "error": f"Range too large (max {MAX_DAILY_POINTS} days).",
            "daily": [], "weekly": [], "monthly": [],
        }

    start_dt = _start_of_day(start_date)
    end_dt = _start_of_day(end_date) + timedelta(days=1)

    net_by_day, gross_by_day = _settlement_day_sums(start_dt, end_dt)
    series = _bucket_series(start_date, end_date, net_by_day, gross_by_day)

    return {
        "success": True,
        "range": {"start": start_iso, "end": end_iso},
        "daily": series["daily"],
        "weekly": series["weekly"],
        "monthly": series["monthly"],
    }


def range_snapshot(start_iso, end_iso, prior="slide"):
    """Full-dashboard JSON for an inclusive date range: the global range
    picker payload. Mirrors the legacy stats semantics exactly — KPI totals
    use the same id lists, helpers and duplication rules as the legacy
    dashboard route.

    prior="slide" (default) uses the same-length period immediately before
    the range as the delta basis; prior="month" uses the previous month's
    1st through the same day-of-month (clamped) — the MTD comparison used
    by the period strip, so the default dashboard KPIs and the MTD card
    always agree.

    Returns {"success": bool, "range": {...}, "kpis": {...}, "series":
    {...}, "products": {...}, "top_orders": [...], "summary": {...}};
    invalid dates or oversized ranges return success False with an error
    message (the client keeps the current view).
    """
    try:
        start_date = datetime.strptime(start_iso, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid date format - use YYYY-MM-DD."}
    if end_date < start_date:
        return {"success": False, "error": "Start date must be on or before end date."}
    day_count = (end_date - start_date).days + 1
    if day_count > MAX_DAILY_POINTS:
        return {
            "success": False,
            "error": f"Range too large (max {MAX_DAILY_POINTS} days).",
        }

    start_dt = _start_of_day(start_date)
    end_dt = _start_of_day(end_date) + timedelta(days=1)

    # Current window (legacy semantics: the raw id list preserves the
    # settlement-row duplication the legacy dashboard counts).
    ids = _completed_order_ids(start_dt, end_dt)
    orders = len(ids)
    net = round(_dashboard_net_sales(start_dt, end_dt), 2)
    gross = round(_dashboard_gross_sales(ids), 2)
    items = _sum_items(ids)

    # Prior window: "slide" = same-length period immediately before the
    # range; "month" = previous month 1st..same day (the MTD comparison).
    if prior == "month":
        prior_start_dt, prior_end_dt = _month_prior_range(end_date)
    else:
        prior_start_dt = start_dt - timedelta(days=day_count)
        prior_end_dt = start_dt
    prior_ids = _completed_order_ids(prior_start_dt, prior_end_dt)
    prior_orders = len(prior_ids)
    prior_net = round(_dashboard_net_sales(prior_start_dt, prior_end_dt), 2)
    prior_gross = round(_dashboard_gross_sales(prior_ids), 2)
    prior_items = _sum_items(prior_ids)

    net_by_day, gross_by_day = _settlement_day_sums(start_dt, end_dt)
    series = _bucket_series(start_date, end_date, net_by_day, gross_by_day)

    rows = _top_product_rows(ids)
    categories = sorted({row["category"] for row in rows}, key=str.lower)
    products = {"All": rows, "_categories": categories}
    for category in categories:
        products[category] = [
            row for row in rows if row["category"] == category
        ]

    top_orders = []
    if ids:
        recent = Order.query.filter(Order.id.in_(ids)).order_by(
            Order.total.desc()
        ).limit(5).all()
        for order in recent:
            top_orders.append({
                "order_no": order.order_no,
                "customer_name": order.customer_name,
                "order_type": order.order_type,
                "status": order.status,
                "total": round(float(order.total or 0), 2),
                "timestamp": (
                    order.timestamp.isoformat() if order.timestamp else None
                ),
            })

    summary = {
        "avg_order_value": round(net / orders, 2) if orders else 0,
        "sales_per_order": round(gross / orders, 2) if orders else 0,
        "items_per_order": round(items / orders, 1) if orders else 0,
    }
    customer_names = set()
    if ids:
        customer_names = {
            name[0] for name in db.session.query(
                db.distinct(Order.customer_name)
            ).filter(
                Order.id.in_(ids),
                Order.customer_name.isnot(None),
                Order.customer_name != "",
            ).all()
        }
    previous_query = db.session.query(db.distinct(Order.customer_name)).filter(
        Order.status == "completed",
        Order.customer_name.isnot(None),
        Order.customer_name != "",
    )
    if ids:
        previous_query = previous_query.filter(Order.id.notin_(ids))
    previous_names = {name[0] for name in previous_query.all()}
    returning = len(customer_names & previous_names)
    summary["customers"] = len(customer_names)
    summary["returning"] = returning
    summary["returning_pct"] = (
        round((returning / len(customer_names)) * 100) if customer_names else 0
    )

    return {
        "success": True,
        "range": {"start": start_iso, "end": end_iso},
        "kpis": {
            "orders": orders,
            "net": net,
            "gross": gross,
            "items": items,
            "prior_orders": prior_orders,
            "prior_net": prior_net,
            "prior_gross": prior_gross,
            "prior_items": prior_items,
        },
        "series": series,
        "products": products,
        "top_orders": top_orders,
        "summary": summary,
    }
