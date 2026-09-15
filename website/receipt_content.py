"""Shared receipt/report identity content sourced from receipt settings."""

DEFAULT_RECEIPT_HEADER = [
    "iCount IT Business Solutions Inc.",
    "VAT REG. TIN 687-322-616-00000",
    "PTU NO: xxxxxxxx",
    "DATE ISSUED: xx/xx/xxxx",
    "2F 7 ELEVEN P. BURGOS ST. BRGY 23",
    "6500 CITY OF TACLOBAN LEYTE PHILIPPINES",
    "MIN xxxxxxxx",
    "SN - xxxxxxx",
]

DEFAULT_RECEIPT_FOOTER = [
    "iCount IT Business Solutions Inc.",
    "2F 7 ELEVEN P. BURGOS ST. BRGY 23",
    "6500 CITY OF TACLOBAN LEYTE PHILIPPINES",
    "VAT REG. TIN 687-322-616-00000",
    "ACC. NO. 0886873226162026012794",
    "DATE ISSUED: 03/26/2026",
]


def get_receipt_header_lines():
    """Return receipt header lines from DB settings, with stable defaults."""
    try:
        from .models import ReceiptSettings

        settings = ReceiptSettings.get_settings()
        header = settings.get_header_list()
        if header:
            return [str(line).strip() for line in header if str(line).strip()]
    except Exception:
        pass
    return list(DEFAULT_RECEIPT_HEADER)


def get_receipt_thank_you_text():
    """Return the configured thank-you/footer message."""
    try:
        from .models import ReceiptSettings

        settings = ReceiptSettings.get_settings()
        message = (settings.thank_you_message or "").strip()
        if message:
            return message
    except Exception:
        pass
    return "Thank you for dining with us!"


def _first_matching(lines, prefixes, fallback=""):
    for line in lines:
        normalized = line.strip().upper()
        if any(normalized.startswith(prefix) for prefix in prefixes):
            return line.strip()
    return fallback


def get_report_header_fields():
    """Return normalized fields used by XLSX reports and text exports."""
    lines = get_receipt_header_lines()
    defaults = DEFAULT_RECEIPT_HEADER

    name = lines[0] if lines else defaults[0]
    vat = _first_matching(lines, ("VAT", "TIN"), defaults[1])
    ptu = _first_matching(lines, ("PTU",), defaults[2])
    ptu_issued = _first_matching(lines, ("DATE ISSUED",), defaults[3])
    min_no = _first_matching(lines, ("MIN",), defaults[6])
    serial_no = _first_matching(lines, ("SN", "SERIAL"), defaults[7])

    excluded_prefixes = ("VAT", "TIN", "PTU", "DATE ISSUED", "MIN", "SN", "SERIAL")
    address_lines = [
        line.strip()
        for line in lines[1:]
        if line.strip() and not any(line.strip().upper().startswith(prefix) for prefix in excluded_prefixes)
    ]
    if not address_lines:
        address_lines = defaults[4:6]

    return {
        "company_name": name,
        "address_line1": address_lines[0] if address_lines else "",
        "address_line2": address_lines[1] if len(address_lines) > 1 else "",
        "address": ", ".join(address_lines),
        "vat": vat,
        "ptu": ptu,
        "ptu_issued": ptu_issued,
        "min": min_no,
        "sn": serial_no,
        "software": "Nexgen POS v1.2025",
        "terminal": "POS-001",
        "terminal_id": "POS-01",
    }


def get_receipt_footer_lines():
    """Return official receipt/e-journal footer lines from DB settings."""
    try:
        from .models import ReceiptSettings

        settings = ReceiptSettings.get_settings()
        footer = settings.get_footer_list()
        if footer:
            footer_lines = [str(line).strip() for line in footer if str(line).strip()]
            if footer_lines and footer_lines != DEFAULT_RECEIPT_FOOTER and not _has_placeholder_footer(footer_lines):
                return footer_lines
    except Exception:
        pass
    return list(DEFAULT_RECEIPT_FOOTER)


def _has_placeholder_footer(lines):
    text = "\n".join(lines).lower()
    return "xxxxxxxx" in text or "xx/xx" in text

