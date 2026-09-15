from datetime import timedelta
from zoneinfo import ZoneInfo

from .helpers import get_philippine_time
from .models import User, db


ORDER_LOCK_TIMEOUT_SECONDS = 15
ORDER_LOCK_MAX_FUTURE_SKEW_SECONDS = 30
from datetime import timezone
try:
    PH_TZ = ZoneInfo("Asia/Manila")
except Exception:
    PH_TZ = timezone(timedelta(hours=8))


def _lock_holder_name(user_id):
    if not user_id:
        return "another user"
    user = User.query.get(user_id)
    if not user:
        return "another user"
    return user.username


def _clear_lock(order):
    order.processing_user_id = None
    order.processing_started_at = None
    order.processing_last_seen_at = None


def _normalize_dt_for_compare(dt_value):
    """
    Normalize datetime values to Manila-local naive datetime for safe SQLite comparison.
    SQLite often returns naive datetimes even if aware values were written.
    """
    if dt_value is None:
        return None
    if dt_value.tzinfo is not None:
        return dt_value.astimezone(PH_TZ).replace(tzinfo=None)
    return dt_value


def try_acquire_or_touch_order_lock(order, user_id, commit=False):
    """
    Acquire or refresh an order lock for the current user.
    Returns (ok, payload_dict). payload_dict always includes "success" and "message".
    """
    now = _normalize_dt_for_compare(get_philippine_time())
    cutoff = now - timedelta(seconds=ORDER_LOCK_TIMEOUT_SECONDS)

    if order.status != "pending":
        _clear_lock(order)
        if commit:
            db.session.commit()
        return True, {"success": True, "message": ""}

    lock_user_id = order.processing_user_id
    lock_seen_at = _normalize_dt_for_compare(order.processing_last_seen_at or order.processing_started_at)
    lock_active = bool(lock_user_id and lock_seen_at and lock_seen_at >= cutoff)

    # Guard against bad device clock / future timestamps causing "stuck" locks.
    if lock_user_id and lock_seen_at and lock_seen_at > now + timedelta(seconds=ORDER_LOCK_MAX_FUTURE_SKEW_SECONDS):
        lock_active = False
        _clear_lock(order)

    if lock_active and lock_user_id != user_id:
        holder = _lock_holder_name(lock_user_id)
        return False, {
            "success": False,
            "lock_conflict": True,
            "processing_by_user_id": lock_user_id,
            "processing_by": holder,
            "message": f"Order is currently being processed by {holder}. Please wait until they finish.",
        }

    # If the lock is stale, clear it before taking over.
    if not lock_active and lock_user_id:
        _clear_lock(order)

    if order.processing_user_id != user_id:
        order.processing_user_id = user_id
        order.processing_started_at = now
    order.processing_last_seen_at = now

    if commit:
        db.session.commit()

    return True, {"success": True, "message": ""}


def release_order_lock(order, user_id, commit=False, force=False):
    if force or order.processing_user_id == user_id:
        _clear_lock(order)
        if commit:
            db.session.commit()
