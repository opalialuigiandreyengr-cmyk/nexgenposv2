from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user


ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_CASHIER = "cashier"
ROLE_CREW = "crew"
ROLE_BIR_GUEST = "bir_guest"

ALL_ROLES = {
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_CASHIER,
    ROLE_CREW,
    ROLE_BIR_GUEST,
}

ROLE_LABELS = {
    ROLE_ADMIN: "Administrator",
    ROLE_MANAGER: "Manager",
    ROLE_CASHIER: "Cashier",
    ROLE_CREW: "Crew",
    ROLE_BIR_GUEST: "BIR Guest",
}

PERMISSIONS = {
    "admin_authorize": {ROLE_ADMIN, ROLE_MANAGER},
    "admin_only": {ROLE_ADMIN},
    "manage_users": {ROLE_ADMIN},
    "manage_categories": {ROLE_ADMIN, ROLE_MANAGER},
    "manage_products": {ROLE_ADMIN, ROLE_MANAGER},
    "manage_backups": {ROLE_ADMIN},
    "view_audit_logs": {ROLE_ADMIN},
    "view_activity_logs": {ROLE_ADMIN, ROLE_BIR_GUEST},
    "view_misc_reports": {ROLE_ADMIN, ROLE_MANAGER, ROLE_BIR_GUEST},
    "generate_ejournal": {ROLE_ADMIN, ROLE_MANAGER, ROLE_BIR_GUEST},
    "transfer_rlc": {ROLE_ADMIN, ROLE_MANAGER},
    "manage_tables": {ROLE_ADMIN, ROLE_MANAGER, ROLE_CREW},
    "cashier_accountability": {ROLE_CASHIER},
}


def normalize_role(user_or_role):
    if isinstance(user_or_role, str):
        return user_or_role
    return getattr(user_or_role, "role", None)


def is_valid_role(role):
    return role in ALL_ROLES


def roles_for(permission):
    return PERMISSIONS.get(permission, set())


def has_permission(user_or_role, permission):
    return normalize_role(user_or_role) in roles_for(permission)


def can_settle_orders(user_or_role):
    return normalize_role(user_or_role) != ROLE_CREW


def can_auto_reactivate_shift(user_or_role):
    return normalize_role(user_or_role) == ROLE_CASHIER


def deny_response(message, redirect_endpoint="main.dashboard", status_code=403):
    wants_json = (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )
    if wants_json:
        return jsonify({"success": False, "message": message}), status_code

    flash(message, "danger")
    return redirect(url_for(redirect_endpoint))


def permission_required(permission, message=None, redirect_endpoint="main.dashboard"):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if has_permission(current_user, permission):
                return view_func(*args, **kwargs)

            allowed_labels = ", ".join(ROLE_LABELS.get(role, role) for role in sorted(roles_for(permission)))
            default_message = f"Access denied. Required role: {allowed_labels}."
            return deny_response(message or default_message, redirect_endpoint=redirect_endpoint)

        return wrapper

    return decorator
