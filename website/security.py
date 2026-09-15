import hashlib
import hmac
import os
import time


INTERNAL_SIGNATURE_MAX_AGE_SECONDS = 300
CARD_HASH_PREFIX = "hmac_sha256$"


def get_internal_request_secret():
    return os.environ.get("POS_INTERNAL_REQUEST_SECRET") or os.environ.get("SECRET_KEY")


def get_card_hash_secret():
    return os.environ.get("POS_CARD_HASH_SECRET") or os.environ.get("SECRET_KEY")


def build_internal_request_signature(method, path, timestamp=None):
    secret = get_internal_request_secret()
    if not secret:
        raise RuntimeError("SECRET_KEY or POS_INTERNAL_REQUEST_SECRET is required for signed internal requests")

    timestamp = str(timestamp or int(time.time()))
    message = f"{method.upper()}\n{path}\n{timestamp}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return timestamp, signature


def build_internal_request_headers(method, path):
    timestamp, signature = build_internal_request_signature(method, path)
    return {
        "X-Internal-Timestamp": timestamp,
        "X-Internal-Signature": signature,
    }


def verify_internal_request_signature(method, path, timestamp, signature):
    if not timestamp or not signature:
        return False

    try:
        issued_at = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(int(time.time()) - issued_at) > INTERNAL_SIGNATURE_MAX_AGE_SECONDS:
        return False

    try:
        _, expected = build_internal_request_signature(method, path, issued_at)
    except RuntimeError:
        return False

    return hmac.compare_digest(expected, signature)


def normalize_card_number(card_number):
    value = (card_number or "").strip()
    if not value or is_card_number_hash(value):
        return value

    digits_only = "".join(ch for ch in value if ch.isdigit())
    if not digits_only:
        return value

    return digits_only.lstrip("0") or "0"


def is_card_number_hash(value):
    return bool(value and value.startswith(CARD_HASH_PREFIX))


def _hash_normalized_card_number(normalized):
    if not normalized:
        return None

    if is_card_number_hash(normalized):
        return normalized

    secret = get_card_hash_secret()
    if not secret:
        raise RuntimeError("SECRET_KEY or POS_CARD_HASH_SECRET is required for card hashing")

    digest = hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{CARD_HASH_PREFIX}{digest}"


def hash_card_number(card_number):
    normalized = normalize_card_number(card_number)
    return _hash_normalized_card_number(normalized)


def card_number_hash_candidates(card_number):
    value = (card_number or "").strip()
    if not value:
        return []

    candidate_values = []
    for candidate in (
        normalize_card_number(value),
        value,
        "".join(ch for ch in value if ch.isdigit()),
    ):
        if candidate and candidate not in candidate_values:
            candidate_values.append(candidate)

    hashes = []
    for candidate in candidate_values:
        card_hash = _hash_normalized_card_number(candidate)
        if card_hash and card_hash not in hashes:
            hashes.append(card_hash)
    return hashes
