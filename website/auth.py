from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from .models import User, db, Settlement, Order
from datetime import datetime
from .helpers import is_mobile_user_agent, get_philippine_time
from .activity_logger import log_activity, EventType
from .security import card_number_hash_candidates, verify_internal_request_signature
from .permissions import ROLE_ADMIN, ROLE_CASHIER, ROLE_CREW, roles_for
import re

auth = Blueprint('auth', __name__)

def validate_password(password):
    if len(password) < 8:
        return False
    return True

@auth.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == ROLE_ADMIN:
            return redirect(url_for("main.home"))
        elif current_user.role == ROLE_CREW:
            return redirect(url_for("orders.orders_page"))
        else:
            return redirect(url_for("main.dashboard"))
    
    user_agent = request.headers.get('User-Agent', '')
    is_mobile = is_mobile_user_agent(user_agent)
    
    return redirect(url_for("auth.login"))

@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        card_number = request.form.get("card_number", "").strip()
        
        # Card swipe login for admin/manager.
        # If credentials are present, prefer normal username/password login so a
        # stale hidden card_number value cannot hijack manual login.
        if card_number and not (username or password):
            user = User.query.filter(
                User.card_number.in_(card_number_hash_candidates(card_number)),
                User.role.in_(roles_for("admin_authorize"))
            ).first()
            
            if not user:
                flash("Card not recognized or not authorized for swipe login.", "danger")
                return render_template("login.html")
            
            if user.status in ['suspended', 'banned', 'deleted']:
                flash("Your account is inactive. Please contact your POS provider.", "danger")
                return render_template("login.html")
            
            login_user(user, remember=True)
            user.last_login = get_philippine_time()
            db.session.commit()
            log_activity(
                EventType.LOGIN_SUCCESS,
                f"Manager '{user.username}' logged in via card swipe",
                details={
                    'username': user.username,
                    'role': user.role,
                    'login_method': 'card_swipe'
                }
            )
            flash("You have logged in successfully via card swipe", "success")
            if user.role == ROLE_ADMIN:
                return redirect(url_for("main.home"))
            return redirect(url_for("main.dashboard"))
        
        if not username or not password:
            flash("Please enter both username and password", "danger")
            return render_template("login.html")
        
        user = User.query.filter_by(username=username).first()
        
        if user:
            # Auto-reactivate cashier accounts that were shift-locked on a previous day.
            if user.role == ROLE_CASHIER and user.status == 'suspended' and user.shift_locked_on:
                today_ph = get_philippine_time().date()
                if today_ph > user.shift_locked_on:
                    user.status = 'active'
                    user.shift_locked_on = None
                    user.failed_login_attempts = 0
                    db.session.commit()
                    log_activity(
                        EventType.USER_STATUS_CHANGE,
                        f"Cashier '{user.username}' auto-reactivated for next business day",
                        details={
                            'username': user.username,
                            'user_id': user.id,
                            'change_type': 'status',
                            'old_status': 'suspended',
                            'new_status': 'active',
                            'auto_reactivated': True
                        }
                    )

            # Block login for non-active account states
            if user.status in ['suspended', 'banned', 'deleted']:
                if user.status == 'deleted':
                    flash("This account has been deleted. Please contact your POS provider for assistance.", "danger")
                elif user.status == 'banned':
                    flash("Your account has been locked due to multiple failed login attempts. Please contact your POS provider for assistance.", "danger")
                else:
                    if user.role == ROLE_CASHIER and user.shift_locked_on:
                        flash("Your shift has already ended for this account. Please ask an admin or manager to reactivate your login for the next shift.", "warning")
                    else:
                        flash("Your account has been suspended. Please contact your POS provider for assistance.", "danger")
                return render_template("login.html")
            
            # Check password
            if user.check_password(password):
                # Reset failed login attempts on successful login
                user.failed_login_attempts = 0
                
                # Check if the password hash was upgraded
                if not user.password_hash.startswith('$2b$') and not user.password_hash.startswith('$2a$'):
                    db.session.add(user)
                    db.session.commit()
                    flash("Your password has been upgraded to a more secure format.", "info")
                else:
                    db.session.commit()
                
                login_user(user, remember=True)
                user.last_login = get_philippine_time()
                db.session.commit()
                
                # Log successful login
                log_activity(
                    EventType.LOGIN_SUCCESS,
                    f"User '{username}' logged in successfully",
                    details={
                        'username': username,
                        'role': user.role,
                        'login_method': 'web' if not is_mobile_user_agent(request.headers.get('User-Agent', '')) else 'mobile'
                    }
                )
                
                flash("You have logged in successfully", "success")
                
                # Redirect based on role
                if user.role == ROLE_ADMIN:
                    return redirect(url_for("main.home"))
                elif user.role == ROLE_CREW:
                    return redirect(url_for("orders.orders_page"))
                else:
                    return redirect(url_for("main.dashboard"))
            else:
                # Log failed login attempt
                log_activity(
                    EventType.LOGIN_FAILED,
                    f"Failed login attempt for username '{username}'",
                    details={
                        'username': username,
                        'attempts': user.failed_login_attempts + 1,
                        'ip_address': request.remote_addr
                    }
                )
                
                # Increment failed login attempts
                user.failed_login_attempts += 1
                
                # Ban user if failed attempts reach 5
                if user.failed_login_attempts >= 5:
                    user.status = 'banned'
                    db.session.commit()
                    flash("Your account has been locked due to multiple failed login attempts. Please contact your POS provider for assistance.", "danger")
                else:
                    db.session.commit()
                    remaining_attempts = 5 - user.failed_login_attempts
                    flash(f"Invalid username or password. {remaining_attempts} attempt(s) remaining before account lock.", "danger")
        else:
            # Log failed login for non-existent user
            log_activity(
                EventType.LOGIN_FAILED,
                f"Failed login attempt for non-existent username '{username}'",
                details={'username': username, 'reason': 'user_not_found'}
            )
            flash("Invalid username or password", "danger")
    
    return render_template("login.html")

@auth.route("/logout")
@login_required
def logout():
    # Log logout before clearing session
    username = current_user.username if current_user else 'Unknown'
    user_id = current_user.id if current_user else None
    user_role = current_user.role if current_user else None

    # End-shift lock for cashier accounts.
    # Cashiers must be re-activated by admin/manager before next login.
    # Only suspend if they have completed transactions (shift accountability required).
    if user_id and user_role == ROLE_CASHIER:
        try:
            # Check if cashier has any completed transactions today
            today_ph = get_philippine_time().date()
            start_date = datetime.combine(today_ph, datetime.min.time().replace(hour=9))
            end_date = datetime.now()
            
            transaction_count = Settlement.query.join(Order).filter(
                Settlement.timestamp >= start_date,
                Settlement.timestamp < end_date,
                Order.status == 'completed',
                Settlement.cashier_id == user_id
            ).count()
            
            # Only suspend if cashier has transactions (needs accountability)
            if transaction_count > 0:
                cashier_user = User.query.get(user_id)
                if cashier_user and cashier_user.status == 'active':
                    cashier_user.status = 'suspended'
                    cashier_user.shift_locked_on = get_philippine_time().date()
                    db.session.commit()
                    log_activity(
                        EventType.SHIFT_CLOSED,
                        f"Cashier '{username}' ended shift and account was locked",
                        details={
                            'username': username,
                            'user_id': user_id,
                            'locked_on_logout': True,
                            'shift_locked_on': str(cashier_user.shift_locked_on),
                            'transaction_count': transaction_count
                        }
                    )
        except Exception:
            db.session.rollback()
    
    logout_user()
    
    # Log logout activity
    log_activity(
        EventType.LOGOUT,
        f"User '{username}' logged out",
        details={'username': username}
    )
    
    flash("You have logged out successfully", "success")
    return redirect(url_for("auth.login"))

@auth.route("/internal/clear-sessions", methods=["POST"])
def clear_sessions():
    """Internal endpoint to clear all sessions on shutdown.
    This endpoint is called by the watcher process when the application is closing."""
    if not verify_internal_request_signature(
        request.method,
        request.path,
        request.headers.get("X-Internal-Timestamp"),
        request.headers.get("X-Internal-Signature"),
    ):
        return jsonify({"success": False, "message": "Invalid internal request signature"}), 403

    try:
        # Clear the current session
        session.clear()
        
        # Return success
        return jsonify({"success": True, "message": "Sessions cleared"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
