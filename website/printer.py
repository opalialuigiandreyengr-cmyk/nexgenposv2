# Import system libraries for frozen app detection
import sys
import os
import re
import socket
import ipaddress
import threading
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import escpos with error handling
try:
    from escpos.escpos import Escpos
    from escpos.printer import Network, Usb
    ESCPOS_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    print(f"[WARNING] ESCPOS library not available: {e}")
    ESCPOS_AVAILABLE = False
    Escpos = object
    Network = None
    Usb = None

# Use a Windows-installed printer queue for USB printers without Zadig/libusb.
try:
    import win32print
    WINDOWS_SPOOLER_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] Windows spooler support not available: {e}")
    win32print = None
    WINDOWS_SPOOLER_AVAILABLE = False

# Global USB backend variable
_usb_backend = None

# Import USB libraries with graceful fallback.
try:
    import usb.core
    import usb.util
    import usb.backend.libusb0 as libusb0

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
        libusb_path = os.path.join(application_path, "libusb0.dll")
        if os.path.exists(libusb_path):
            try:
                _usb_backend = libusb0.get_backend(find_library=lambda x: libusb_path)
            except Exception as e:
                print(f"[WARNING] Failed to configure USB backend: {e}")
except Exception as e:
    print(f"[WARNING] USB library not available: {e}")
    usb = None

# LAN printer settings (override using env vars)
def _parse_hosts(*raw_values):
    hosts = []
    for raw in raw_values:
        if not raw:
            continue
        for entry in str(raw).split(","):
            host = entry.strip()
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def _parse_int_env(name, default):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip(), 0)
    except (TypeError, ValueError):
        print(f"[WARNING] Invalid value for {name}: {raw}. Using default {default}.")
        return default


def _parse_float_env(name, default):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        print(f"[WARNING] Invalid value for {name}: {raw}. Using default {default}.")
        return default


def _parse_port_list(raw_value, defaults):
    ports = []
    for value in list(defaults):
        try:
            port = int(value)
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
        except (TypeError, ValueError):
            continue
    if raw_value:
        for entry in str(raw_value).split(","):
            try:
                port = int(entry.strip())
                if 1 <= port <= 65535 and port not in ports:
                    ports.append(port)
            except (TypeError, ValueError):
                continue
    return ports


def _pick_reachable_host(candidates, port, timeout_seconds=2):
    """Try to connect to printer hosts with better timeout handling."""
    for host in candidates:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_seconds):
                print(f"[INFO] Printer reachable at {host}:{port}")
                return host
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError, OSError) as e:
            print(f"[DEBUG] Printer at {host}:{port} not reachable: {e}")
            continue
    return ""


def _pick_reachable_host_and_port(candidates, ports, timeout_seconds=1.0):
    for port in ports:
        host = _pick_reachable_host(candidates, port, timeout_seconds=timeout_seconds)
        if host:
            return host, int(port)
    return "", 0


def _list_local_ipv4_addresses():
    addresses = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
        for info in infos:
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass

    # Fallback method that often returns the active LAN interface IP.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass

    return addresses


def _discover_printer_hosts_on_lan(port, timeout_seconds=0.65, max_workers=128, max_results=12):
    """Scan local /24 networks for reachable raw-printing sockets."""
    local_ips = _list_local_ipv4_addresses()
    if not local_ips:
        return []

    scan_targets = []
    seen = set()
    for local_ip in local_ips:
        try:
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        except ValueError:
            continue
        for ip in network.hosts():
            host = str(ip)
            if host == local_ip or host in seen:
                continue
            seen.add(host)
            scan_targets.append(host)

    if not scan_targets:
        return []

    found_hosts = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(socket.create_connection, (host, int(port)), timeout_seconds): host
            for host in scan_targets
        }
        for future in as_completed(futures):
            host = futures[future]
            try:
                conn = future.result()
                conn.close()
                found_hosts.append(host)
                if len(found_hosts) >= max_results:
                    break
            except OSError:
                continue
    return found_hosts


MAIN_CASHIER_PRINTER_PORT = int(os.getenv("CASHIER_PRINTER_PORT") or os.getenv("LAN_PRINTER_PORT") or os.getenv("PRINTER_PORT") or "9100")
MAIN_CASHIER_AUTO_DISCOVER = (os.getenv("CASHIER_PRINTER_AUTO_DISCOVER", "1").strip().lower() not in {"0", "false", "no", "off"})
MAIN_CASHIER_DISCOVERY_TTL = int(os.getenv("CASHIER_PRINTER_DISCOVERY_TTL") or "300")
MAIN_CASHIER_PRINTER_HOSTS = _parse_hosts(
    os.getenv("CASHIER_PRINTER_IPS"),
    os.getenv("LAN_PRINTER_IPS"),
    os.getenv("PRINTER_IPS"),
    os.getenv("CASHIER_PRINTER_IP"),
    os.getenv("LAN_PRINTER_IP"),
    os.getenv("PRINTER_IP"),
)
MAIN_CASHIER_PRINTER_HOST = MAIN_CASHIER_PRINTER_HOSTS[0] if MAIN_CASHIER_PRINTER_HOSTS else ""
MAIN_CASHIER_PRINTER_TIMEOUT = int(os.getenv("CASHIER_PRINTER_TIMEOUT") or os.getenv("LAN_PRINTER_TIMEOUT") or os.getenv("PRINTER_TIMEOUT") or "10")
MAIN_CASHIER_USB_VENDOR_ID = _parse_int_env("CASHIER_USB_VENDOR_ID", _parse_int_env("USB_PRINTER_VENDOR_ID", 0x1504))
MAIN_CASHIER_USB_PRODUCT_ID = _parse_int_env("CASHIER_USB_PRODUCT_ID", _parse_int_env("USB_PRINTER_PRODUCT_ID", 0x0059))
MAIN_CASHIER_USB_INTERFACE = _parse_int_env("CASHIER_USB_INTERFACE", _parse_int_env("USB_PRINTER_INTERFACE", 0))
MAIN_CASHIER_USB_IN_EP = _parse_int_env("CASHIER_USB_IN_EP", _parse_int_env("USB_PRINTER_IN_EP", 0x81))
MAIN_CASHIER_USB_OUT_EP = _parse_int_env("CASHIER_USB_OUT_EP", _parse_int_env("USB_PRINTER_OUT_EP", 0x02))
MAIN_CASHIER_USB_ENABLED = (os.getenv("CASHIER_USB_FALLBACK", os.getenv("USB_PRINTER_FALLBACK", "1")).strip().lower() not in {"0", "false", "no", "off"})
MAIN_CASHIER_USB_PRIORITY = (os.getenv("CASHIER_USB_PRIORITY", "1").strip().lower() not in {"0", "false", "no", "off"})
MAIN_CASHIER_WINDOWS_PRINTER_NAME = (os.getenv("CASHIER_WINDOWS_PRINTER_NAME") or os.getenv("WINDOWS_PRINTER_NAME") or "").strip()
LAN_RAW_PRINT_PORTS = _parse_port_list(os.getenv("LAN_PRINTER_DISCOVERY_PORTS"), [9100, 9101, 9102, 9103])
PRINTER_CONNECT_TIMEOUT = _parse_float_env("PRINTER_CONNECT_TIMEOUT", 1.0)
PRINTER_MAX_RETRIES = max(1, _parse_int_env("PRINTER_MAX_RETRIES", 2))
PRINTER_RETRY_DELAY = max(0.0, _parse_float_env("PRINTER_RETRY_DELAY", 0.25))

_cashier_discovery_lock = threading.Lock()
_cashier_discovery_cache = {"host": "", "port": 0, "timestamp": 0.0}
_kitchen_discovery_lock = threading.Lock()
_kitchen_discovery_cache = {"host": "", "port": 0, "timestamp": 0.0}
_lan_discovery_lock = threading.Lock()
_lan_discovery_cache = {}

KITCHEN_PRINTER_HOSTS = _parse_hosts(
    os.getenv("KITCHEN_PRINTER_IPS"),
    os.getenv("KITCHEN_PRINTER_IP"),
)
KITCHEN_PRINTER_HOST = KITCHEN_PRINTER_HOSTS[0] if KITCHEN_PRINTER_HOSTS else ""
KITCHEN_PRINTER_PORT = int(os.getenv("KITCHEN_PRINTER_PORT") or MAIN_CASHIER_PRINTER_PORT)
KITCHEN_PRINTER_TIMEOUT = int(os.getenv("KITCHEN_PRINTER_TIMEOUT") or MAIN_CASHIER_PRINTER_TIMEOUT)
KITCHEN_AUTO_DISCOVER = (os.getenv("KITCHEN_PRINTER_AUTO_DISCOVER", "1").strip().lower() not in {"0", "false", "no", "off"})
KITCHEN_DISCOVERY_TTL = int(os.getenv("KITCHEN_PRINTER_DISCOVERY_TTL") or str(MAIN_CASHIER_DISCOVERY_TTL))
KITCHEN_USB_VENDOR_ID = _parse_int_env("KITCHEN_USB_VENDOR_ID", MAIN_CASHIER_USB_VENDOR_ID)
KITCHEN_USB_PRODUCT_ID = _parse_int_env("KITCHEN_USB_PRODUCT_ID", MAIN_CASHIER_USB_PRODUCT_ID)
KITCHEN_USB_INTERFACE = _parse_int_env("KITCHEN_USB_INTERFACE", MAIN_CASHIER_USB_INTERFACE)
KITCHEN_USB_IN_EP = _parse_int_env("KITCHEN_USB_IN_EP", MAIN_CASHIER_USB_IN_EP)
KITCHEN_USB_OUT_EP = _parse_int_env("KITCHEN_USB_OUT_EP", MAIN_CASHIER_USB_OUT_EP)
KITCHEN_USB_ENABLED = (os.getenv("KITCHEN_USB_FALLBACK", os.getenv("USB_PRINTER_FALLBACK", "1")).strip().lower() not in {"0", "false", "no", "off"})
KITCHEN_USB_PRIORITY = (os.getenv("KITCHEN_USB_PRIORITY", "0").strip().lower() not in {"0", "false", "no", "off"})
KITCHEN_WINDOWS_PRINTER_NAME = (os.getenv("KITCHEN_WINDOWS_PRINTER_NAME") or "").strip()
KITCHEN_PRINTER_COLUMNS = _parse_int_env("KITCHEN_PRINTER_COLUMNS", 32)
KITCHEN_PRINTER_CONNECT_TIMEOUT = _parse_float_env("KITCHEN_PRINTER_CONNECT_TIMEOUT", 1.0)
KITCHEN_PRINTER_MAX_RETRIES = max(1, _parse_int_env("KITCHEN_PRINTER_MAX_RETRIES", 1))
KITCHEN_SCAN_ON_CONFIGURED_FAILURE = (
    os.getenv("KITCHEN_PRINTER_SCAN_ON_CONFIGURED_FAILURE", "0").strip().lower()
    not in {"0", "false", "no", "off"}
)


def _get_saved_printer_config(target):
    """Read saved printer settings from DB when Flask app context is available."""
    try:
        from flask import has_app_context
        if not has_app_context():
            return {}

        from .models import ReceiptSettings
        settings = ReceiptSettings.get_settings()
        if target == "kitchen":
            return {
                "host": (settings.kitchen_printer_host or "").strip(),
                "port": int(settings.kitchen_printer_port or KITCHEN_PRINTER_PORT),
                "windows_printer_name": (settings.kitchen_windows_printer_name or "").strip(),
                "auto_discover": settings.auto_discover_network_printers is not False,
            }
        return {
            "host": (settings.cashier_printer_host or "").strip(),
            "port": int(settings.cashier_printer_port or MAIN_CASHIER_PRINTER_PORT),
            "windows_printer_name": (settings.cashier_windows_printer_name or "").strip(),
            "auto_discover": settings.auto_discover_network_printers is not False,
        }
    except Exception as e:
        print(f"[WARNING] Could not load saved printer settings: {e}")
        return {}


def save_discovered_printer_config(target, host, port=None):
    """Persist an auto-discovered LAN printer target outside critical print flows."""
    target = (target or "").strip().lower()
    host = (host or "").strip()
    if target not in {"cashier", "kitchen"} or not host:
        return False

    # Cashier printer must be explicitly configured — never persist auto-discovered host.
    if target == "cashier":
        return False

    try:
        from flask import has_app_context
        if not has_app_context():
            return False

        from .models import ReceiptSettings, db
        settings = ReceiptSettings.get_settings()
        clean_port = int(port or KITCHEN_PRINTER_PORT)

        changed = False
        if settings.kitchen_printer_host != host:
            settings.kitchen_printer_host = host
            changed = True
        if int(settings.kitchen_printer_port or KITCHEN_PRINTER_PORT) != clean_port:
            settings.kitchen_printer_port = clean_port
            changed = True

        if not changed:
            return True

        db.session.commit()
        invalidate_printer_discovery(target)
        print(f"[INFO] Saved auto-discovered {target} printer at {host}:{clean_port}")
        return True
    except Exception as e:
        try:
            from .models import db
            db.session.rollback()
        except Exception:
            pass
        print(f"[WARNING] Could not save discovered {target} printer {host}:{port}: {e}")
        return False


USB_PRINTER_CLASS = 0x07
_USB_PRINTER_NAME_HINTS = (
    "printer",
    "receipt",
    "thermal",
    "pos",
    "esc",
    "epson",
    "xprinter",
    "rongta",
    "bixolon",
    "star",
    "citizen",
    "sewoo",
)


def _safe_usb_string(dev, index):
    if not index or usb is None or getattr(usb, "util", None) is None:
        return ""
    try:
        return usb.util.get_string(dev, index) or ""
    except Exception:
        return ""


def _usb_device_identity(dev):
    manufacturer = _safe_usb_string(dev, getattr(dev, "iManufacturer", None))
    product = _safe_usb_string(dev, getattr(dev, "iProduct", None))
    serial = _safe_usb_string(dev, getattr(dev, "iSerialNumber", None))
    label_parts = [part for part in (manufacturer, product) if part]
    label = " ".join(label_parts).strip()
    if not label:
        label = f"USB device {int(dev.idVendor):04x}:{int(dev.idProduct):04x}"
    return manufacturer, product, serial, label


def _usb_endpoint_metadata(dev, prefer_printer_class=True):
    metadata = {
        "interface": None,
        "in_ep": None,
        "out_ep": None,
        "is_printer_class": False,
    }
    if usb is None or getattr(usb, "util", None) is None:
        return metadata

    try:
        for config in dev:
            for intf in config:
                is_printer_class = int(getattr(intf, "bInterfaceClass", -1)) == USB_PRINTER_CLASS
                if prefer_printer_class and not is_printer_class:
                    continue

                in_ep = None
                out_ep = None
                for endpoint in intf:
                    try:
                        address = int(endpoint.bEndpointAddress)
                        direction = usb.util.endpoint_direction(address)
                    except Exception:
                        continue
                    if direction == usb.util.ENDPOINT_OUT and out_ep is None:
                        out_ep = address
                    elif direction == usb.util.ENDPOINT_IN and in_ep is None:
                        in_ep = address

                metadata.update({
                    "interface": int(getattr(intf, "bInterfaceNumber", 0) or 0),
                    "in_ep": in_ep,
                    "out_ep": out_ep,
                    "is_printer_class": is_printer_class,
                })
                if out_ep is not None or is_printer_class:
                    return metadata
    except Exception:
        return metadata

    return metadata


def discover_usb_printers(include_name_hints=True, include_configured=True):
    """Return USB printer candidates visible to PyUSB."""
    if usb is None or getattr(usb, "core", None) is None:
        return []

    devices = []
    try:
        found = usb.core.find(find_all=True, backend=_usb_backend)
        if found is None:
            found = []
        devices = list(found)
    except Exception:
        try:
            found = usb.core.find(find_all=True)
            devices = list(found or [])
        except Exception as e:
            print(f"[WARNING] USB printer discovery failed: {e}")
            return []

    configured_ids = {
        (int(MAIN_CASHIER_USB_VENDOR_ID), int(MAIN_CASHIER_USB_PRODUCT_ID)),
        (int(KITCHEN_USB_VENDOR_ID), int(KITCHEN_USB_PRODUCT_ID)),
    } if include_configured else set()

    printers = []
    seen = set()
    for dev in devices:
        try:
            vendor_id = int(dev.idVendor)
            product_id = int(dev.idProduct)
        except Exception:
            continue

        key = (vendor_id, product_id)
        if key in seen:
            continue

        manufacturer, product, serial, label = _usb_device_identity(dev)
        metadata = _usb_endpoint_metadata(dev, prefer_printer_class=True)
        if not metadata.get("is_printer_class"):
            fallback_metadata = _usb_endpoint_metadata(dev, prefer_printer_class=False)
            if fallback_metadata.get("out_ep") is not None:
                metadata.update({
                    "interface": fallback_metadata.get("interface"),
                    "in_ep": fallback_metadata.get("in_ep"),
                    "out_ep": fallback_metadata.get("out_ep"),
                })

        identity_text = f"{manufacturer} {product} {label}".lower()
        has_name_hint = include_name_hints and any(hint in identity_text for hint in _USB_PRINTER_NAME_HINTS)
        is_configured = key in configured_ids
        is_candidate = bool(is_configured or metadata.get("is_printer_class") or has_name_hint)
        if not is_candidate:
            continue

        seen.add(key)
        printers.append({
            "vendor_id": vendor_id,
            "product_id": product_id,
            "vendor_hex": f"0x{vendor_id:04x}",
            "product_hex": f"0x{product_id:04x}",
            "interface": metadata.get("interface"),
            "in_ep": metadata.get("in_ep"),
            "out_ep": metadata.get("out_ep"),
            "manufacturer": manufacturer,
            "product": product,
            "serial": serial,
            "label": label,
            "is_printer_class": bool(metadata.get("is_printer_class")),
            "is_configured": bool(is_configured),
        })

    printers.sort(key=lambda item: (
        not item.get("is_configured"),
        not item.get("is_printer_class"),
        item.get("label", ""),
    ))
    return printers


def _get_discovered_hosts_for_port(port, ttl_seconds, force_refresh=False):
    now = time.time()
    if not force_refresh:
        with _lan_discovery_lock:
            cache = _lan_discovery_cache.get(int(port))
            if not cache:
                cache = {"hosts": [], "timestamp": 0.0}
            cached_hosts = list(cache.get("hosts", []))
            cached_timestamp = float(cache.get("timestamp", 0.0))
            if cached_timestamp and (now - cached_timestamp) <= ttl_seconds:
                return cached_hosts

    discovered = _discover_printer_hosts_on_lan(int(port))
    with _lan_discovery_lock:
        _lan_discovery_cache[int(port)] = {"hosts": discovered, "timestamp": time.time()}
    return discovered


def _printer_port_candidates(preferred_port):
    ports = []
    try:
        preferred = int(preferred_port)
        if 1 <= preferred <= 65535:
            ports.append(preferred)
    except (TypeError, ValueError):
        pass
    for port in LAN_RAW_PRINT_PORTS:
        if port not in ports:
            ports.append(port)
    return ports


def _printer_probe_ports(target, preferred_port):
    if target == "kitchen":
        try:
            preferred = int(preferred_port)
            if 1 <= preferred <= 65535:
                return [preferred]
        except (TypeError, ValueError):
            return [KITCHEN_PRINTER_PORT]
    return _printer_port_candidates(preferred_port)


def _discover_first_printer_on_ports(ports, ttl_seconds, excluded_hosts=None):
    excluded = set(excluded_hosts or [])
    for port in ports:
        discovered_hosts = _get_discovered_hosts_for_port(port, ttl_seconds)
        preferred_hosts = [host for host in discovered_hosts if host not in excluded]
        selected = _pick_reachable_host(preferred_hosts, port, timeout_seconds=0.75) if preferred_hosts else ""
        if not selected and discovered_hosts:
            selected = _pick_reachable_host(discovered_hosts, port, timeout_seconds=0.75)
        if selected:
            return selected, int(port)

        if discovered_hosts:
            refreshed_hosts = _get_discovered_hosts_for_port(port, ttl_seconds, force_refresh=True)
            refreshed_preferred = [host for host in refreshed_hosts if host not in excluded]
            selected = _pick_reachable_host(refreshed_preferred, port, timeout_seconds=0.75) if refreshed_preferred else ""
            if not selected and refreshed_hosts:
                selected = _pick_reachable_host(refreshed_hosts, port, timeout_seconds=0.75)
            if selected:
                return selected, int(port)

    return "", 0


def _discover_cashier_printer_host():
    now = time.time()
    with _cashier_discovery_lock:
        cached_host = _cashier_discovery_cache.get("host", "")
        cached_port = int(_cashier_discovery_cache.get("port") or MAIN_CASHIER_PRINTER_PORT)
        cached_timestamp = float(_cashier_discovery_cache.get("timestamp", 0.0))
        if cached_host and (now - cached_timestamp) <= MAIN_CASHIER_DISCOVERY_TTL:
            if _pick_reachable_host([cached_host], cached_port):
                return cached_host

    # 1) Try explicit candidates first (from env vars).
    preferred, preferred_port = _pick_reachable_host_and_port(
        MAIN_CASHIER_PRINTER_HOSTS,
        _printer_port_candidates(MAIN_CASHIER_PRINTER_PORT),
        timeout_seconds=1.0
    )
    if preferred:
        with _cashier_discovery_lock:
            _cashier_discovery_cache["host"] = preferred
            _cashier_discovery_cache["port"] = preferred_port
            _cashier_discovery_cache["timestamp"] = time.time()
        return preferred

    # 2) Fall back to LAN auto-discovery (port 9100) — cashier only uses explicit config.
    return ""


def _discover_kitchen_printer_host():
    now = time.time()
    with _kitchen_discovery_lock:
        cached_host = _kitchen_discovery_cache.get("host", "")
        cached_port = int(_kitchen_discovery_cache.get("port") or KITCHEN_PRINTER_PORT)
        cached_timestamp = float(_kitchen_discovery_cache.get("timestamp", 0.0))
        if cached_host and (now - cached_timestamp) <= KITCHEN_DISCOVERY_TTL:
            if _pick_reachable_host([cached_host], cached_port, timeout_seconds=KITCHEN_PRINTER_CONNECT_TIMEOUT):
                return cached_host

    # 1) Try explicit kitchen candidates first.
    preferred, preferred_port = _pick_reachable_host_and_port(
        KITCHEN_PRINTER_HOSTS,
        _printer_port_candidates(KITCHEN_PRINTER_PORT),
        timeout_seconds=KITCHEN_PRINTER_CONNECT_TIMEOUT
    )
    if preferred:
        with _kitchen_discovery_lock:
            _kitchen_discovery_cache["host"] = preferred
            _kitchen_discovery_cache["port"] = preferred_port
            _kitchen_discovery_cache["timestamp"] = time.time()
        return preferred

    # 2) Fall back to LAN auto-discovery.
    saved_config = _get_saved_printer_config("kitchen")
    if saved_config.get("host") and not KITCHEN_SCAN_ON_CONFIGURED_FAILURE:
        return ""

    discovery_port = int(saved_config.get("port") or KITCHEN_PRINTER_PORT)
    if KITCHEN_AUTO_DISCOVER and saved_config.get("auto_discover", True):
        cashier_host = _cashier_discovery_cache.get("host", "") or _discover_cashier_printer_host()
        selected, selected_port = _discover_first_printer_on_ports(
            _printer_port_candidates(discovery_port),
            KITCHEN_DISCOVERY_TTL,
            excluded_hosts=[cashier_host] if cashier_host else []
        )
        if not selected:
            # If only one network printer is found, use it for both copies.
            selected, selected_port = _discover_first_printer_on_ports(
                _printer_port_candidates(discovery_port),
                KITCHEN_DISCOVERY_TTL
            )

        if selected:
            print(f"[INFO] Auto-discovered kitchen printer at {selected}:{selected_port}")
            with _kitchen_discovery_lock:
                _kitchen_discovery_cache["host"] = selected
                _kitchen_discovery_cache["port"] = selected_port
                _kitchen_discovery_cache["timestamp"] = time.time()
            return selected

    return ""


def invalidate_printer_discovery(target="all"):
    """Clear discovery cache so next resolve does a fresh host lookup."""
    target = (target or "all").strip().lower()
    now = 0.0

    if target in ("all", "cashier"):
        with _cashier_discovery_lock:
            _cashier_discovery_cache["host"] = ""
            _cashier_discovery_cache["port"] = 0
            _cashier_discovery_cache["timestamp"] = now

    if target in ("all", "kitchen"):
        with _kitchen_discovery_lock:
            _kitchen_discovery_cache["host"] = ""
            _kitchen_discovery_cache["port"] = 0
            _kitchen_discovery_cache["timestamp"] = now

    # Flush all port scan caches; stale entries can keep returning wrong hosts.
    with _lan_discovery_lock:
        _lan_discovery_cache.clear()


def discover_windows_printers():
    """Return printer queues registered with Windows Print Spooler.

    Virtual printers like AnyDesk, Fax, Microsoft Print to PDF, OneNote
    and similar non-POS queues are filtered out automatically.
    """
    if not WINDOWS_SPOOLER_AVAILABLE:
        return []

    _EXCLUDED_PRINTER_PATTERNS = [
        "anydesk",
        "fax",
        "microsoft print to pdf",
        "microsoft xps document writer",
        "onenote",
        "send to onenote",
        "adobe pdf",
        "snip & sketch",
        "print to pdf",
        "pdf creator",
        "pdf24",
        "foxit",
        "cute pdf",
        "bullzip",
        "do pdf",
        "nova pdf",
        "soda pdf",
    ]

    # Status flags that indicate a printer is NOT usable
    _OFFLINE_FLAGS = (
        0x01  |  # PRINTER_STATUS_PAUSED
        0x02  |  # PRINTER_STATUS_ERROR
        0x04  |  # PRINTER_STATUS_PENDING_DELETION
        0x80  |  # PRINTER_STATUS_OFFLINE
        0x1000    # PRINTER_STATUS_NOT_AVAILABLE
    )

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = []
    seen = set()
    try:
        default_name = win32print.GetDefaultPrinter()
    except Exception:
        default_name = ""
    try:
        for printer in win32print.EnumPrinters(flags, None, 2):
            name = str(printer.get("pPrinterName") or "").strip()
            if not name or name in seen:
                continue
            name_lower = name.casefold()
            if any(p in name_lower for p in _EXCLUDED_PRINTER_PATTERNS):
                continue
            status = int(printer.get("Status") or 0)
            if status & _OFFLINE_FLAGS:
                continue
            seen.add(name)
            printers.append({
                "name": name,
                "is_default": name == default_name,
                "status": int(printer.get("Status") or 0),
            })
    except Exception as e:
        print(f"[WARNING] Windows printer discovery failed: {e}")
    printers.sort(key=lambda item: (not item["is_default"], item["name"].casefold()))
    return printers


_printer_state_log_cache = {}


def _log_printer_state(key, message, min_repeat_seconds=60.0):
    """Log printer-state messages only on change (or at most once per minute).

    The readiness check runs from status polls and every print gate, so raw
    prints spam the console with identical warnings; dedupe them per printer.
    """
    import time as _t
    now = _t.time()
    last_message, last_ts = _printer_state_log_cache.get(key, (None, 0.0))
    if message == last_message and (now - last_ts) < min_repeat_seconds:
        return
    _printer_state_log_cache[key] = (message, now)
    if message:
        print(message)


def _windows_tcpip_port_dead(port_name):
    """For queues on a Standard TCP/IP RAW port, confirm the device answers.

    Fail-open: only returns True when the registry positively identifies a RAW
    TCP/IP target and the socket refuses twice; unknown ports are left alone.
    """
    name = (port_name or "").strip()
    if not name:
        return False
    try:
        import winreg
        reg_path = r"SYSTEM\CurrentControlSet\Control\Print\Monitors\Standard TCP/IP Port\Ports" + "\\" + name
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            try:
                protocol = int(winreg.QueryValueEx(key, "Protocol")[0])
            except Exception:
                protocol = 0
            if protocol != 1:  # 1 = RAW; skip LPR/unknown setups
                return False
            host = ""
            for value_name in ("HostName", "IPAddress"):
                try:
                    host = (winreg.QueryValueEx(key, value_name)[0] or "").strip()
                except Exception:
                    host = ""
                if host:
                    break
            try:
                tcp_port = int(winreg.QueryValueEx(key, "PortNumber")[0] or 9100)
            except Exception:
                tcp_port = 9100
    except Exception:
        return False
    if not host:
        return False
    import socket
    for _ in range(2):
        try:
            with socket.create_connection((host, tcp_port), timeout=1.2):
                return False
        except Exception:
            continue
    _log_printer_state("port:" + name, f"[WARNING] Windows queue port '{name}' ({host}:{tcp_port}) is unreachable - printer is off or disconnected.")
    return True


def _present_usbprint_port_numbers():
    """Return (port_numbers, has_unknown) for USB printers plugged in right now.

    Enumerates the usbprint device interface with DIGCF_PRESENT via SetupAPI so
    unplugged/powered-off printers drop out immediately, unlike the spooler
    queue which keeps reporting "Ready". Returns None when enumeration is
    unavailable so callers can fail open.
    """
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", _GUID),
            ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.c_size_t),
        ]

    # GUID_DEVINTERFACE_USBPRINT {28d78fad-5a12-11d1-ae5b-0000f803a8c2}
    usbprint_guid = _GUID(
        0x28D78FAD, 0x5A12, 0x11D1,
        (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2),
    )

    try:
        setupapi = ctypes.windll.setupapi
    except Exception:
        return None
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    DIGCF_PRESENT = 0x00000002
    DIGCF_DEVICEINTERFACE = 0x00000010
    dev_list = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(usbprint_guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not dev_list or dev_list == ctypes.c_void_p(-1).value:
        return None

    port_numbers = set()
    has_unknown = False
    try:
        import winreg
        index = 0
        while True:
            dev_info = _SP_DEVINFO_DATA()
            dev_info.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(ctypes.c_void_p(dev_list), index, ctypes.byref(dev_info)):
                break
            index += 1
            instance_id = ctypes.create_unicode_buffer(512)
            if not setupapi.SetupDiGetDeviceInstanceIdW(
                ctypes.c_void_p(dev_list), ctypes.byref(dev_info), instance_id, 512, None
            ):
                has_unknown = True
                continue
            # usbprint stores the dynamic port index (USB001 -> 1) per device.
            try:
                reg_path = r"SYSTEM\CurrentControlSet\Enum" + "\\" + instance_id.value + r"\Device Parameters"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                    port_numbers.add(int(winreg.QueryValueEx(key, "Port Number")[0]))
            except Exception:
                has_unknown = True
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(dev_list))
    return port_numbers, has_unknown


def _purge_stuck_jobs(handle, printer_name):
    """Cancel and purge any orphaned stuck jobs in the Windows printer queue."""
    if not handle or not WINDOWS_SPOOLER_AVAILABLE:
        return
    try:
        jobs = win32print.EnumJobs(handle, 0, 20, 1)
        if not jobs:
            return
        error_flags = (
            getattr(win32print, "JOB_STATUS_ERROR", 0x2)
            | getattr(win32print, "JOB_STATUS_OFFLINE", 0x20)
            | getattr(win32print, "JOB_STATUS_PAPEROUT", 0x40)
            | getattr(win32print, "JOB_STATUS_BLOCKED_DEVQ", 0x200)
            | getattr(win32print, "JOB_STATUS_USER_INTERVENTION", 0x400)
            | getattr(win32print, "JOB_STATUS_RESTART", 0x800)
        )
        for job in jobs:
            job_id = job.get("JobId")
            status = int(job.get("Status") or 0)
            if (status & error_flags) or len(jobs) >= 1:
                try:
                    win32print.SetJob(handle, job_id, 0, None, win32print.JOB_CONTROL_CANCEL)
                    _log_printer_state(printer_name, f"[INFO] Cancelled stuck job #{job_id} in queue '{printer_name}'.")
                except Exception:
                    pass
    except Exception:
        pass


def _windows_usb_port_dead(port_name):
    """For queues on a USB virtual port (USB001, TMUSB001, ESDPRT001...), confirm device is attached.

    Windows keeps USB queues "Ready" (Status = 0, no Work Offline flag) after
    the printer is unplugged or powered off, so jobs silently spool until the
    printer returns. Checking live PnP device list catches the unplug immediately.
    """
    name = (port_name or "").strip()
    if not name:
        return False

    is_usb_port = bool(re.search(r"(USB|ESDPRT|TMUSB|PORTPROMPT)", name, re.IGNORECASE))

    try:
        scan = _present_usbprint_port_numbers()
    except Exception:
        scan = None

    if scan is not None:
        port_numbers, has_unknown = scan
        # If SetupAPI reports 0 present USB printers in the system and no unknown devices,
        # then ALL USB printers are disconnected/off!
        if is_usb_port and len(port_numbers) == 0 and not has_unknown:
            _log_printer_state("port:" + name, f"[WARNING] Windows queue port '{name}' has no USB printer attached (0 present USB printers).")
            return True

        match = re.search(r"USB(\d{1,3})", name, re.IGNORECASE)
        if match:
            p_num = int(match.group(1))
            if p_num in port_numbers:
                return False
            if not has_unknown:
                _log_printer_state("port:" + name, f"[WARNING] Windows queue port '{name}' (USB port {p_num}) is not in present USB ports {port_numbers}.")
                return True
        elif is_usb_port and len(port_numbers) == 0:
            return True

    return False


def _windows_queue_stalled(handle, printer_name):
    """Detect jobs stuck in the queue (device not actually draining them)."""
    try:
        jobs = win32print.EnumJobs(handle, 0, 10, 1)
    except Exception:
        return False
    if not jobs:
        return False
    error_flags = (
        getattr(win32print, "JOB_STATUS_ERROR", 0x2)
        | getattr(win32print, "JOB_STATUS_OFFLINE", 0x20)
        | getattr(win32print, "JOB_STATUS_PAPEROUT", 0x40)
        | getattr(win32print, "JOB_STATUS_BLOCKED_DEVQ", 0x200)
        | getattr(win32print, "JOB_STATUS_USER_INTERVENTION", 0x400)
    )
    for job in jobs:
        if int(job.get("Status") or 0) & error_flags:
            _log_printer_state(printer_name, f"[WARNING] Windows printer '{printer_name}' has a stuck job in the queue (device offline?).")
            return True
    # Receipt printers drain jobs in seconds; a pile-up means the device is dead.
    if len(jobs) >= 3:
        _log_printer_state(printer_name, f"[WARNING] Windows printer '{printer_name}' has {len(jobs)} queued jobs - treating as not ready.")
        return True
    return False


def _windows_printer_ready(printer_name):
    """Return whether a configured Windows printer queue can accept jobs."""
    if not WINDOWS_SPOOLER_AVAILABLE or not printer_name:
        return False
    handle = None
    try:
        handle = win32print.OpenPrinter(printer_name)
        info = win32print.GetPrinter(handle, 2)
        status = int(info.get("Status") or 0)
        attributes = int(info.get("Attributes") or 0)

        # Windows leaves Status = 0 for unplugged/powered-off USB printers and
        # flags the queue with WORK_OFFLINE instead, so status alone reports a
        # false "ready" and jobs silently spool until the printer returns.
        work_offline = getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0x0400)
        if attributes & work_offline:
            _log_printer_state(printer_name, f"[WARNING] Windows printer '{printer_name}' is offline (queue set to Work Offline).")
            return False

        blocked = (
            getattr(win32print, "PRINTER_STATUS_ERROR", 0x2)
            | getattr(win32print, "PRINTER_STATUS_OFFLINE", 0x80)
            | getattr(win32print, "PRINTER_STATUS_NOT_AVAILABLE", 0x1000)
            | getattr(win32print, "PRINTER_STATUS_PAUSED", 0x1)
            | getattr(win32print, "PRINTER_STATUS_PAPER_JAM", 0x8)
            | getattr(win32print, "PRINTER_STATUS_PAPER_OUT", 0x10)
            | getattr(win32print, "PRINTER_STATUS_PAPER_PROBLEM", 0x40)
            | getattr(win32print, "PRINTER_STATUS_DOOR_OPEN", 0x400000)
            | getattr(win32print, "PRINTER_STATUS_USER_INTERVENTION", 0x100000)
        )
        if status & blocked:
            _log_printer_state(printer_name, f"[WARNING] Windows printer '{printer_name}' reported blocking status 0x{status:X}.")
            return False

        # Even with a clean status, jobs stuck in the queue mean the physical
        # device is not printing; refusing here prevents silent spool-and-burst.
        if _windows_queue_stalled(handle, printer_name):
            return False

        # Network-mapped queues stay "Ready" even when the LAN printer is off;
        # verify the RAW TCP/IP target actually answers before trusting them.
        if _windows_tcpip_port_dead(info.get("pPortName")):
            return False

        # USB-mapped queues also stay "Ready" after the cable is unplugged or
        # the printer is powered off; verify the PnP device is really present.
        if _windows_usb_port_dead(info.get("pPortName")):
            return False
        # Log recovery exactly once when a previously blocked printer is back.
        _log_printer_state(printer_name, f"[INFO] Windows printer '{printer_name}' is ready.", min_repeat_seconds=float("inf"))
        return True
    except Exception as e:
        _log_printer_state(printer_name, f"[WARNING] Windows printer '{printer_name}' is unavailable: {e}")
        return False
    finally:
        if handle is not None:
            win32print.ClosePrinter(handle)

# Backward-compatible aliases used by other modules (e.g. status endpoint)
PRINTER_HOST = MAIN_CASHIER_PRINTER_HOST
PRINTER_PORT = MAIN_CASHIER_PRINTER_PORT
PRINTER_TIMEOUT = MAIN_CASHIER_PRINTER_TIMEOUT


if "usb" not in globals():
    class _USBCompat:
        """Compatibility shim for modules that still import `usb` from this file."""
        core = None
    usb = _USBCompat()

from .models import Order, Product, Settlement, GiftCertificate
from .receipt_content import (
    get_report_header_fields,
    get_receipt_footer_lines,
    get_receipt_header_lines,
    get_receipt_thank_you_text,
)
from flask_login import current_user
from datetime import datetime
import time
from contextlib import contextmanager
from enum import Enum


class WindowsRawPrinter(Escpos):
    """ESC/POS printer transport that submits RAW bytes through Windows spooler."""

    def __init__(self, printer_name, document_name="NexgenPOS Receipt"):
        super().__init__()
        self.printer_name = printer_name
        self.document_name = document_name
        self._buffer = bytearray()
        self._handle = None
        self._job_id = None
        self._document_started = False
        self._page_started = False
        self.open()

    def open(self):
        if self._handle is not None:
            return
        self._handle = win32print.OpenPrinter(self.printer_name)
        # Purge any old stuck jobs so they never print when re-plugged
        _purge_stuck_jobs(self._handle, self.printer_name)

        self._job_id = win32print.StartDocPrinter(self._handle, 1, (self.document_name, None, "RAW"))
        self._document_started = True
        win32print.StartPagePrinter(self._handle)
        self._page_started = True

    def _raw(self, msg):
        if not msg:
            return
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        self._buffer.extend(msg)

    def close(self):
        if self._handle is None:
            return
        try:
            if self._buffer:
                win32print.WritePrinter(self._handle, bytes(self._buffer))
                self._buffer.clear()
            if self._page_started:
                win32print.EndPagePrinter(self._handle)
                self._page_started = False
            if self._document_started:
                win32print.EndDocPrinter(self._handle)
                self._document_started = False

            self._verify_and_purge_if_failed()
        finally:
            if self._handle is not None:
                win32print.ClosePrinter(self._handle)
                self._handle = None

    def _verify_and_purge_if_failed(self):
        """Verify job status. If stuck or printer is offline/unplugged, cancel and purge job immediately."""
        if not self._handle or not self._job_id or not WINDOWS_SPOOLER_AVAILABLE:
            return

        import time
        time.sleep(0.3)

        try:
            info = win32print.GetPrinter(self._handle, 2)
            port_name = info.get("pPortName")
            if _windows_usb_port_dead(port_name):
                print(f"[WARNING] USB Printer '{self.printer_name}' (port '{port_name}') is offline/disconnected. Purging job #{self._job_id}.")
                try:
                    win32print.SetJob(self._handle, self._job_id, 0, None, win32print.JOB_CONTROL_CANCEL)
                except Exception:
                    pass
                raise RuntimeError(f"Printer '{self.printer_name}' is offline or disconnected. Job cancelled.")

            jobs = win32print.EnumJobs(self._handle, 0, 20, 1)
            error_flags = (
                getattr(win32print, "JOB_STATUS_ERROR", 0x2)
                | getattr(win32print, "JOB_STATUS_OFFLINE", 0x20)
                | getattr(win32print, "JOB_STATUS_PAPEROUT", 0x40)
                | getattr(win32print, "JOB_STATUS_BLOCKED_DEVQ", 0x200)
                | getattr(win32print, "JOB_STATUS_USER_INTERVENTION", 0x400)
            )
            for j in jobs:
                if j.get("JobId") == self._job_id:
                    st = int(j.get("Status") or 0)
                    if (st & error_flags) or st == getattr(win32print, "JOB_STATUS_OFFLINE", 0x20):
                        print(f"[WARNING] Job #{self._job_id} stuck in offline/error status 0x{st:X}. Purging job.")
                        try:
                            win32print.SetJob(self._handle, self._job_id, 0, None, win32print.JOB_CONTROL_CANCEL)
                        except Exception:
                            pass
                        raise RuntimeError(f"Printer '{self.printer_name}' reported offline/error status. Job cancelled.")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[WARNING] Job verification error: {e}")


def get_receipt_header():
    """Return the receipt header lines from DB settings, falling back to the hardcoded default."""
    return get_receipt_header_lines()


def get_receipt_footer():
    """Return footer lines derived from DB receipt settings."""
    return get_receipt_footer_lines()


def get_receipt_thank_you_message():
    """Return the customizable thank-you message from DB settings."""
    return get_receipt_thank_you_text()


def print_terminal_metadata(p):
    """Print POS terminal metadata from shared receipt/report settings."""
    report_header = get_report_header_fields()
    p.text(f"TERMINAL ID: {report_header['terminal_id']}\n")
    p.text(f"POS/SOFTWARE: {report_header['software'].upper()}\n")


class ReceiptType(Enum):
    KITCHEN = "kitchen"
    CASHIER = "cashier"
    BILL = "bill"
    OFFICIAL = "official"
    Z_READING = "z_reading"
    X_READING = "x_reading"
    CANCELLED_VOID_REFUND = "cancelled_void_refund"
    ITEM_SALES_REPORT = "item_sales_report"
    TAKEOUT_PICKUP_DELIVERY_REPORT = "takeout_pickup_delivery_report"
    CASHIER_ACCOUNTABILITY = "cashier_accountability"


def format_line(label, amount, width=None):
    """Universal line formatter that supports labels and amounts.

    Width defaults to the configured receipt width so label/amount
    lines always right-align at the separator's right edge.
    """
    if width is None:
        width = _receipt_width()
    if isinstance(amount, (int, float)):
        # For monetary amounts, always show 2 decimal places
        amt_str = f"{amount:,.2f}"
    else:
        amt_str = str(amount)
    space = width - len(label) - len(amt_str)
    if space < 1:
        space = 1
    return f"{label}{' ' * space}{amt_str}\n"

def format_line_count(label, count, width=None):
    """Format line for count values, preserving decimal quantities when present."""
    if width is None:
        width = _receipt_width()
    if isinstance(count, (int, float)):
        count_str = format_qty(count)
    else:
        count_str = str(count)
    space = width - len(label) - len(count_str)
    if space < 1:
        space = 1
    return f"{label}{' ' * space}{count_str}\n"


def format_line_with_count(label, count, amount, width=None):
    """Universal line formatter that supports labels, counts, and amounts"""
    if width is None:
        width = _receipt_width()
    # For monetary amounts, always show 2 decimal places
    if isinstance(amount, (int, float)):
        amt_str = f"{amount:,.2f}"
    else:
        amt_str = str(amount)
    
    # For count values, format as integers without decimal places
    if isinstance(count, (int, float)):
        if isinstance(count, int) or (isinstance(count, float) and count.is_integer()):
            count_str = f"{int(count):,}"
        else:
            # If it's a true float, still format as integer (round it)
            count_str = f"{int(round(count)):,}"
    else:
        count_str = str(count)
    
    # Calculate spaces for equal centering
    # Split width into three parts: label, count, amount
    # Adjust for moving count 4 spaces to the right
    label_width = width // 3
    count_width = (width // 3) + 1  # Add 4 spaces to move count right
    amount_width = width - label_width - count_width
    
    # Ensure we have at least some space for each section
    if amount_width < 8:  # Minimum space for amount
        amount_width = 8
        count_width = width - label_width - amount_width
    
    # Format each part
    label_formatted = label[:label_width].ljust(label_width)
    count_formatted = count_str.rjust(count_width)
    amount_formatted = amt_str.rjust(amount_width)
    
    return f"{label_formatted}{count_formatted}{amount_formatted}\n"


def format_transaction_header(ref_label, ref_value, inv_label, inv_value, width=None):
    """Format a transaction header with reference and invoice numbers"""
    if width is None:
        width = _receipt_width()
    # Calculate spacing to align the labels and values
    ref_part = f"{ref_label} {ref_value}"
    inv_part = f"{inv_label} {inv_value}"
    
    # Calculate spaces between the two parts
    total_length = len(ref_part) + len(inv_part)
    spaces = width - total_length
    if spaces < 1:
        spaces = 1
    
    return f"{ref_part}{' ' * spaces}{inv_part}\n"

def format_transaction_details(cashier_label, cashier_value, time_label, time_value, width=None):
    """Format transaction details with cashier and time"""
    if width is None:
        width = _receipt_width()
    # Calculate spacing to align the labels and values
    cashier_part = f"{cashier_label} {cashier_value}"
    time_part = f"{time_label} {time_value}"
    
    # Calculate spaces between the two parts
    total_length = len(cashier_part) + len(time_part)
    spaces = width - total_length
    if spaces < 1:
        spaces = 1
    
    return f"{cashier_part}{' ' * spaces}{time_part}\n"

def format_transaction_totals(qty_label, qty_value, amount_label, amount_value, width=None):
    """Format transaction totals with quantity and amount"""
    if width is None:
        width = _receipt_width()
    qty_str = format_qty(qty_value)
    
    # Format amount (with 2 decimal places)
    if isinstance(amount_value, (int, float)):
        amount_str = f"{amount_value:,.2f}"
    else:
        amount_str = str(amount_value)
    
    # Calculate spacing
    qty_part = f"{qty_label} {qty_str}"
    amount_part = f"{amount_label} {amount_str}"
    
    # Calculate spaces between the two parts
    total_length = len(qty_part) + len(amount_part)
    spaces = width - total_length
    if spaces < 1:
        spaces = 1
    
    return f"{qty_part}{' ' * spaces}{amount_part}\n"


def format_qty(value):
    """Show whole quantities without .0 and weighted quantities with decimals."""
    try:
        qty = float(value or 0)
    except (TypeError, ValueError):
        return str(value)

    if qty.is_integer():
        return f"{int(qty):,}"
    return f"{qty:,.2f}".rstrip('0').rstrip('.')


def _get_print_size():
    """Return the configured cashier receipt print size: 'compact' or 'normal'.

    'compact' keeps the legacy small text (Font B + condensed) that suits
    dot-matrix / long receipts, while 'normal' switches to full-size Font A
    text for 80mm thermal printers.
    """
    try:
        from .models import ReceiptSettings
        settings = ReceiptSettings.get_settings()
        size = (settings.print_size or 'compact').strip().lower()
        return size if size in ('compact', 'normal') else 'compact'
    except Exception:
        return 'compact'


def _set_standard_receipt_text(p):
    """Receipt text mode honoring the configured print size.

    'compact' (default; dot-matrix / narrow thermal): Font B +
    condensed ON so the 40-character receipt lines fit the paper width
    without overflowing.  Line spacing stays at the printer default so it
    matches the header block.

    'normal' (wide thermal): Font A + condensed OFF + default spacing.
    """
    if _get_print_size() == 'normal':
        p._raw(b'\x12')           # Condensed OFF
        p._raw(b'\x1b\x4d\x00')   # Font A
        p._raw(b'\x1b\x20\x00')   # Character spacing 0
        p._raw(b'\x1d\x21\x00')   # Normal width/height
        p._raw(b'\x1b\x32')       # Default line spacing
    else:
        p._raw(b'\x1b\x4d\x01')   # Font B
        p._raw(b'\x0f')           # Condensed ON - keeps 40-char lines on-width
        p._raw(b'\x1d\x21\x00')   # Normal size
        p._raw(b'\x1b\x32')       # Default line spacing - same as header block


def _set_body_line_spacing(p):
    """Restore body text mode (font/condensed/line spacing) after the header.

    The header block sends \\x12 (condensed OFF) + \\x1b\\x32 (default
    line spacing).  This helper re-applies the right body mode while
    KEEPING the same default line spacing so header and body lines are
    spaced identically (forcing a smaller spacing like 30/180\" makes
    the body look compressed next to the header on thermal printers):

      - 'compact': Font B + condensed ON + default spacing
      - 'normal':  Font A + condensed OFF + default spacing
    """
    if _get_print_size() == 'compact':
        p._raw(b'\x0f')           # Re-enable condensed ON (header \x12 killed it)
        p._raw(b'\x1b\x32')       # Default line spacing - same as header block
    else:
        p._raw(b'\x12')           # Condensed OFF
        p._raw(b'\x1b\x4d\x00')   # Font A
        p._raw(b'\x1b\x32')       # Default line spacing - same as header block


def _set_kitchen_text_mode(p):
    """Kitchen body text mode - identical to the cashier receipt mode.

    Delegates to the standard receipt text so kitchen slips use the same
    font/condensed settings as the cashier copy ('compact': Font B +
    condensed ON, 'normal': Font A + condensed OFF).  This keeps the
    dash separators and body lines at the same physical width on both
    printers - the previous fixed Font A + condensed OFF mode printed
    wider characters than the paper allows, wrapping extra dashes onto
    the next line.
    """
    _set_standard_receipt_text(p)


def _print_kitchen_separator(p):
    """Print the kitchen dash separator exactly like the cashier one.

    Kitchen body text now uses the same mode as the cashier receipt, so
    the standard dash line fits the paper with no extra wrapped dashes.
    """
    p.text(_receipt_separator())


def _receipt_width():
    """Receipt body width (in columns) matching the configured print size.

    'normal' uses 47 columns - one less than the 48 Font A columns on
    80mm thermal - because a full 48 wraps on the user's printer, while
    'compact' stays at 40 columns to align with the 40-character layout
    of dot-matrix / narrow printers.  Body content (wrapped item names,
    label/amount lines, fill-in lines) follows this width so it always
    ends at the same right edge as the dash separator.
    """
    return 47 if _get_print_size() == 'normal' else 40


def _receipt_separator():
    """Dash separator matching the configured print size width."""
    return "-" * _receipt_width() + "\n"


def _wrap_receipt_text(text, width):
    """Wrap receipt text so long item names print completely instead of being clipped."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return [""]
    return textwrap.wrap(
        normalized,
        width=max(8, int(width)),
        break_long_words=True,
        break_on_hyphens=False,
    ) or [normalized]


def _print_wrapped_item_line(p, text, width, size_command=None):
    for line in _wrap_receipt_text(text, width):
        if size_command:
            p._raw(size_command)
        p.text(f"{line}\n")


def _format_item_two_columns(name, quantity, unit_price, width=None):
    """Format item lines in the two-column layout used by bill out and
    sales invoice receipts:

        qty product name                       total price
          @ unit price          (only when qty is more than 1)

    Product names are never abbreviated - long names are truncated with
    '...' so the line always fits the receipt width.
    """
    if width is None:
        width = _receipt_width()
    try:
        qty_val = float(quantity or 0)
    except (TypeError, ValueError):
        qty_val = 0
    try:
        price_val = float(unit_price or 0)
    except (TypeError, ValueError):
        price_val = 0
    qty_str = format_qty(quantity)
    amt_str = f"{qty_val * price_val:,.2f}"
    prefix = f"{qty_str}  "  # two spaces between qty and product name
    name_w = width - len(prefix) - len(amt_str) - 1  # keep 1 space before amount
    if name_w < 8:
        name_w = 8
    display_name = " ".join(str(name or "").split())
    if len(display_name) > name_w:
        display_name = display_name[: name_w - 3] + "..."
    gap = name_w - len(display_name) + 1
    lines = [f"{prefix}{display_name}{' ' * gap}{amt_str}\n"]
    if qty_val > 1:
        # Indent so '@' aligns under the product name
        lines.append(f"{' ' * len(prefix)}@ {price_val:,.2f}\n")
    return lines


def _finish_order_slip(p, footer_text):
    p.set(align="center")
    p.text(f"{footer_text}\n")
    p._raw(b'\x1b\x64\x05')   # Feed 5 lines so the final wrapped item/footer clears the cutter.
    p.cut()


def _finish_order_slip_without_cut(p, footer_text):
    _finish_order_slip(p, footer_text)


def _print_kitchen_order_type_and_tables(p, order):
    """Print order type/table details larger so kitchen staff can spot them fast."""
    order_type = "DINE-IN" if order.order_type == "dinein" else "TAKEOUT"
    p.set(align="left", bold=True)
    p._raw(b'\x1d\x21\x01')   # Double height only, keeps long table lists readable
    p.text(f"Type: {order_type}\n")
    if order.tables:
        p.text(f"Table(s): {order.tables}\n")
    p._raw(b'\x1d\x21\x00')
    p.set(bold=False)


def _order_type_display(order_type):
    order_type = (order_type or "").strip().lower()
    if order_type == "dinein":
        return "DINE-IN"
    if order_type == "takeout":
        return "TAKEOUT"
    if order_type == "pickup":
        return "PICKUP"
    if order_type == "delivery":
        return "DELIVERY"
    return (order_type or "ORDER").upper()


def _print_bill_order_type_and_table(p, order):
    """Emphasize bill-out order type/table without changing the rest of the bill."""
    order_type = _order_type_display(getattr(order, "order_type", ""))
    table_text = getattr(order, "tables", "") or ""

    p.set(align='left', bold=True)
    p._raw(b'\x1d\x21\x01')   # Double height only; larger than normal without getting too wide
    p.text(f"Type: {order_type}\n")
    if getattr(order, "order_type", "") == 'dinein' and table_text:
        p.text(f"Table no: {table_text}\n")
    p._raw(b'\x1d\x21\x00')   # Reset size
    p.set(align='left', bold=False)


def _print_receipt_order_type_and_table(p, order, table_label="Table no:"):
    """Print order type/table in a readable mid-size style for cashier receipts."""
    order_type = _order_type_display(getattr(order, "order_type", ""))
    table_text = getattr(order, "tables", "") or ""

    p.set(align='left', bold=True)
    p._raw(b'\x1d\x21\x01')   # Double height only
    p.text(f"Type: {order_type}\n")
    if getattr(order, "order_type", "") == 'dinein' and table_text:
        p.text(f"{table_label} {table_text}\n")
    p._raw(b'\x1d\x21\x00')
    p.set(align='left', bold=False)


def _print_emphasized_amount_line(p, label, amount):
    """Print the main amount taller while keeping receipt width stable."""
    p.set(bold=True)
    p._raw(b'\x1d\x21\x01')   # Double height only
    p.text(format_line(label, amount))
    p._raw(b'\x1d\x21\x00')
    p.set(bold=False)


class PrinterConnectionManager:
    """Centralized printer connection manager with context manager support"""
    
    def __init__(
        self,
        host=PRINTER_HOST,
        port=PRINTER_PORT,
        timeout=PRINTER_TIMEOUT,
        max_retries=None,
        retry_delay=None,
        connect_timeout=None,
        fresh_discovery_on_failure=True,
        host_candidates=None,
        host_resolver=None,
        usb_vendor_id=MAIN_CASHIER_USB_VENDOR_ID,
        usb_product_id=MAIN_CASHIER_USB_PRODUCT_ID,
        usb_interface=MAIN_CASHIER_USB_INTERFACE,
        usb_in_ep=MAIN_CASHIER_USB_IN_EP,
        usb_out_ep=MAIN_CASHIER_USB_OUT_EP,
        usb_enabled=MAIN_CASHIER_USB_ENABLED,
        usb_priority=False,
        windows_printer_name="",
    ):
        self.host = (host or "").strip()
        self.port = int(port)
        self.timeout = int(timeout)
        self.max_retries = int(max_retries or PRINTER_MAX_RETRIES)
        self.retry_delay = float(PRINTER_RETRY_DELAY if retry_delay is None else retry_delay)
        self.connect_timeout = float(PRINTER_CONNECT_TIMEOUT if connect_timeout is None else connect_timeout)
        self.fresh_discovery_on_failure = bool(fresh_discovery_on_failure)
        self.host_candidates = _parse_hosts(*(host_candidates or []))
        self.host_resolver = host_resolver
        self.usb_vendor_id = int(usb_vendor_id)
        self.usb_product_id = int(usb_product_id)
        self.usb_interface = int(usb_interface)
        self.usb_in_ep = int(usb_in_ep)
        self.usb_out_ep = int(usb_out_ep)
        self.usb_enabled = bool(usb_enabled)
        self.usb_priority = bool(usb_priority)
        self.windows_printer_name = (windows_printer_name or "").strip()
        self.connection = None
        self._connection_cache = None  # Cache active connection
        self._cache_timestamp = 0  # When connection was created
        self._cache_ttl = 60  # Reuse connection for 60 seconds
    
    @contextmanager
    def get_connection(self):
        """Context manager for automatic printer connection handling"""
        try:
            self.connection = self._establish_connection()
            if self.connection is None:
                raise Exception("Failed to establish printer connection")
            yield self.connection
        except (ConnectionRefusedError, ConnectionResetError, OSError, TimeoutError) as e:
            # Connection error - invalidate cache and retry once
            print(f"[WARNING] Printer connection error: {e}. Retrying...")
            self._invalidate_cache()
            self.connection = self._establish_connection()
            if self.connection is None:
                raise Exception("Failed to establish printer connection after retry")
            yield self.connection
        finally:
            self._close_connection()
    
    def _establish_connection(self):
        """Establish a connection to the printer with retry mechanism."""
        if not ESCPOS_AVAILABLE:
            return None

        spooler_connection = self._establish_windows_connection()
        if spooler_connection is not None:
            print(f"[INFO] Using Windows printer queue: {self.windows_printer_name}")
            return spooler_connection
        
        # Try to use cached connection if still valid
        import time
        if self._connection_cache is not None:
            if time.time() - self._cache_timestamp < self._cache_ttl:
                try:
                    # Test if cached connection is still alive
                    self._connection_cache._raw(b'')  # Send empty bytes to test
                    return self._connection_cache
                except Exception:
                    # Cache is stale, invalidate it
                    self._invalidate_cache()
        
        if self.usb_priority:
            usb_connection = self._establish_usb_connection()
            if usb_connection is not None:
                print("[INFO] USB printer detected. Using USB before LAN.")
                return usb_connection

        last_network_error = None
        tried_network_connection = False
        host = self._resolve_host()
        if host:
            tried_network_connection = True
            self.host = host
            for attempt in range(self.max_retries):
                try:
                    if Network is None:
                        raise Exception("ESCPOS Network class not available")
                    # Create new connection with optimized timeout
                    conn = Network(host=self.host, port=self.port, timeout=min(self.timeout, self.connect_timeout))
                    # Cache the connection
                    self._connection_cache = conn
                    self._cache_timestamp = time.time()
                    return conn
                except (ConnectionRefusedError, ConnectionResetError, OSError, TimeoutError) as e:
                    last_network_error = e
                    print(f"[WARNING] LAN printer connection attempt {attempt + 1}/{self.max_retries} failed: {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                    continue
                except Exception as e:
                    last_network_error = e
                    break
        else:
            last_network_error = Exception("LAN printer is not reachable")

        # Connection kept failing; clear stale discovery cache and do one fresh
        # LAN resolve before falling back. Kitchen printers often come back on
        # with the same power state but a different DHCP address.
        refreshed_host = ""
        if tried_network_connection and self.fresh_discovery_on_failure:
            if self is _kitchen_printer_manager:
                invalidate_printer_discovery("kitchen")
            else:
                invalidate_printer_discovery("cashier")
            self.host = ""
            refreshed_host = self._resolve_host()
        if refreshed_host:
            self.host = refreshed_host
            for attempt in range(self.max_retries):
                try:
                    if Network is None:
                        raise Exception("ESCPOS Network class not available")
                    conn = Network(host=self.host, port=self.port, timeout=min(self.timeout, self.connect_timeout))
                    self._connection_cache = conn
                    self._cache_timestamp = time.time()
                    print(f"[INFO] Printer reconnected after fresh discovery at {self.host}:{self.port}")
                    return conn
                except (ConnectionRefusedError, ConnectionResetError, OSError, TimeoutError) as e:
                    last_network_error = e
                    print(f"[WARNING] Fresh LAN printer connection attempt {attempt + 1}/{self.max_retries} failed: {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                except Exception as e:
                    last_network_error = e
                    break

        usb_connection = self._establish_usb_connection()
        if usb_connection is not None:
            print("[INFO] LAN printer unavailable. Switched to USB printer.")
            return usb_connection

        print(f"[ERROR] Printer connection failed (LAN then USB fallback): {last_network_error}")
        return None

    def _refresh_windows_printer_name(self):
        target = "kitchen" if self is _kitchen_printer_manager else "cashier"
        config = _get_saved_printer_config(target)
        if "windows_printer_name" in config:
            default_name = KITCHEN_WINDOWS_PRINTER_NAME if target == "kitchen" else MAIN_CASHIER_WINDOWS_PRINTER_NAME
            self.windows_printer_name = (config.get("windows_printer_name") or default_name).strip()

    def _establish_windows_connection(self):
        """Try a configured Windows queue before network or libusb transports."""
        if not self.has_windows_spooler_available():
            return None
        try:
            return WindowsRawPrinter(self.windows_printer_name)
        except Exception as e:
            print(f"[WARNING] Windows printer queue '{self.windows_printer_name}' failed: {e}")
            return None

    def has_windows_spooler_available(self):
        self._refresh_windows_printer_name()
        return _windows_printer_ready(self.windows_printer_name)
    
    def _invalidate_cache(self):
        """Invalidate the connection cache."""
        try:
            if self._connection_cache is not None:
                self._connection_cache.close()
        except Exception:
            pass
        self._connection_cache = None
        self._cache_timestamp = 0

    def _establish_usb_connection(self):
        """Try USB connection as fallback when LAN is unavailable."""
        if not self.usb_enabled:
            return None
        if Usb is None or usb is None or getattr(usb, "core", None) is None:
            return None

        candidates = self._usb_connection_candidates()
        for attempt in range(self.max_retries):
            for candidate in candidates:
                try:
                    vendor_id = int(candidate.get("vendor_id") or self.usb_vendor_id)
                    product_id = int(candidate.get("product_id") or self.usb_product_id)
                    interface = candidate.get("interface")
                    in_ep = candidate.get("in_ep")
                    out_ep = candidate.get("out_ep")
                    return Usb(
                        vendor_id,
                        product_id,
                        interface=self.usb_interface if interface is None else int(interface),
                        in_ep=self.usb_in_ep if in_ep is None else int(in_ep),
                        out_ep=self.usb_out_ep if out_ep is None else int(out_ep)
                    )
                except Exception:
                    continue
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)
        return None

    def _configured_usb_device_available(self):
        try:
            dev = usb.core.find(
                idVendor=self.usb_vendor_id,
                idProduct=self.usb_product_id,
                backend=_usb_backend
            )
            if dev is None:
                dev = usb.core.find(
                    idVendor=self.usb_vendor_id,
                    idProduct=self.usb_product_id
                )
            return dev is not None
        except Exception:
            return False

    def _usb_connection_candidates(self):
        candidates = []
        seen = set()

        if self._configured_usb_device_available():
            configured = {
                "vendor_id": self.usb_vendor_id,
                "product_id": self.usb_product_id,
                "interface": self.usb_interface,
                "in_ep": self.usb_in_ep,
                "out_ep": self.usb_out_ep,
                "is_configured": True,
            }
            candidates.append(configured)
            seen.add((self.usb_vendor_id, self.usb_product_id))

        for discovered in discover_usb_printers():
            try:
                key = (int(discovered.get("vendor_id")), int(discovered.get("product_id")))
            except Exception:
                continue
            if key in seen:
                continue
            candidates.append(discovered)
            seen.add(key)

        return candidates

    def has_usb_fallback_available(self):
        """Check if USB fallback is enabled and a matching device is currently detected."""
        if not self.usb_enabled:
            return False
        if Usb is None or usb is None or getattr(usb, "core", None) is None:
            return False
        return bool(self._usb_connection_candidates())

    def _resolve_host(self):
        target = "kitchen" if self is _kitchen_printer_manager else "cashier"
        probe_timeout = KITCHEN_PRINTER_CONNECT_TIMEOUT if target == "kitchen" else 1.0
        saved_config = _get_saved_printer_config(target)
        saved_port = saved_config.get("port")
        if saved_port:
            self.port = int(saved_port)
        saved_host = saved_config.get("host", "")
        saved_direct, saved_direct_port = _pick_reachable_host_and_port(
            [saved_host],
            _printer_probe_ports(target, self.port),
            timeout_seconds=probe_timeout
        ) if saved_host else ("", 0)
        if saved_direct:
            self.host = saved_direct
            self.port = saved_direct_port
            return saved_direct

        direct, direct_port = _pick_reachable_host_and_port(
            [self.host],
            _printer_probe_ports(target, self.port),
            timeout_seconds=probe_timeout
        ) if self.host else ("", 0)
        if direct:
            self.port = direct_port
            return direct

        candidate, candidate_port = _pick_reachable_host_and_port(
            self.host_candidates,
            _printer_probe_ports(target, self.port),
            timeout_seconds=probe_timeout
        )
        if candidate:
            self.port = candidate_port
            return candidate

        # Kitchen fallback should be fast when USB is physically connected, but
        # still allow LAN discovery when the USB printer is off/unplugged.
        if target == "kitchen" and self.has_usb_fallback_available():
            return ""

        if callable(self.host_resolver):
            try:
                resolved = (self.host_resolver() or "").strip()
                if resolved:
                    cache = _kitchen_discovery_cache if target == "kitchen" else _cashier_discovery_cache
                    cached_port = int(cache.get("port") or self.port)
                    self.port = cached_port
                    self.host = resolved
                return resolved
            except Exception as err:
                print(f"[WARNING] Printer host resolver failed: {err}")
        return ""

    def has_configured_target(self):
        return bool(
            self.windows_printer_name
            or self.host
            or self.host_candidates
            or callable(self.host_resolver)
            or self.has_usb_fallback_available()
        )
    
    def _close_connection(self):
        """Safely close the printer connection."""
        try:
            if self.connection:
                self.connection.close()
        except Exception as e:
            print(f"[ERROR] Error closing printer connection: {e}")
        finally:
            if self.connection is self._connection_cache:
                self._connection_cache = None
                self._cache_timestamp = 0
            self.connection = None


def shorten_name(name):
    """Shorten product names by removing vowels (except first letter of each word) if name exceeds 13 characters"""
    if not name or len(name) <= 13:
        return name
    vowels = "aeiouAEIOU"
    result = []
    for word in name.split():
        # Keep the first letter of each word
        short = word[0] if word else ""
        # Add consonants from the rest of the word
        for ch in word[1:]:
            if ch not in vowels:
                short += ch
        result.append(short)
    return " ".join(result)


# Global instances for role-based routing
_cashier_printer_manager = PrinterConnectionManager(
    host=MAIN_CASHIER_PRINTER_HOST,
    port=MAIN_CASHIER_PRINTER_PORT,
    timeout=MAIN_CASHIER_PRINTER_TIMEOUT,
    host_candidates=MAIN_CASHIER_PRINTER_HOSTS,
    host_resolver=_discover_cashier_printer_host,
    usb_vendor_id=MAIN_CASHIER_USB_VENDOR_ID,
    usb_product_id=MAIN_CASHIER_USB_PRODUCT_ID,
    usb_interface=MAIN_CASHIER_USB_INTERFACE,
    usb_in_ep=MAIN_CASHIER_USB_IN_EP,
    usb_out_ep=MAIN_CASHIER_USB_OUT_EP,
    usb_enabled=MAIN_CASHIER_USB_ENABLED,
    usb_priority=MAIN_CASHIER_USB_PRIORITY,
    windows_printer_name=MAIN_CASHIER_WINDOWS_PRINTER_NAME,
    fresh_discovery_on_failure=False
)
_kitchen_printer_manager = PrinterConnectionManager(
    host=KITCHEN_PRINTER_HOST,
    port=KITCHEN_PRINTER_PORT,
    timeout=KITCHEN_PRINTER_TIMEOUT,
    max_retries=KITCHEN_PRINTER_MAX_RETRIES,
    connect_timeout=KITCHEN_PRINTER_CONNECT_TIMEOUT,
    fresh_discovery_on_failure=False,
    host_candidates=KITCHEN_PRINTER_HOSTS,
    host_resolver=_discover_kitchen_printer_host,
    usb_vendor_id=KITCHEN_USB_VENDOR_ID,
    usb_product_id=KITCHEN_USB_PRODUCT_ID,
    usb_interface=KITCHEN_USB_INTERFACE,
    usb_in_ep=KITCHEN_USB_IN_EP,
    usb_out_ep=KITCHEN_USB_OUT_EP,
    usb_enabled=KITCHEN_USB_ENABLED,
    usb_priority=KITCHEN_USB_PRIORITY,
    windows_printer_name=KITCHEN_WINDOWS_PRINTER_NAME
)

# Backward-compatible alias for legacy references
_printer_manager = _cashier_printer_manager


def _has_cashier_printer():
    """Check if cashier printer is available.
    
    Returns:
        bool: True if printer is available OR if printer is not required
    """
    # Check if printer is required in settings
    try:
        from .models import ReceiptSettings
        settings = ReceiptSettings.get_settings()
        if not settings.printer_required:
            print("[INFO] Printer not required - allowing transactions without printer")
            return True  # Allow transactions even without printer
    except Exception as e:
        print(f"[WARNING] Could not check printer_required setting: {e}")
    
    # Printer is required - check if actually available
    is_available = bool(
        _cashier_printer_manager.has_windows_spooler_available()
        or _cashier_printer_manager._resolve_host()
        or _cashier_printer_manager.has_usb_fallback_available()
    )
    
    if not is_available:
        print("[ERROR] Printer is required but not available")
    
    return is_available


def _has_kitchen_printer():
    """Check if kitchen printer is available.
    
    Returns:
        bool: True if printer is available OR if printer is not required
    """
    # Check if printer is required in settings
    try:
        from .models import ReceiptSettings
        settings = ReceiptSettings.get_settings()
        if not settings.printer_required:
            print("[INFO] Printer not required - allowing kitchen operations without printer")
            return True  # Allow operations even without printer
    except Exception as e:
        print(f"[WARNING] Could not check printer_required setting: {e}")
    
    # Printer is required - check LAN first so a powered network kitchen printer
    # is used. If LAN is not reachable, USB remains the fast fallback.
    is_available = bool(
        _kitchen_printer_manager.has_windows_spooler_available()
        or _kitchen_printer_manager._resolve_host()
        or _kitchen_printer_manager.has_usb_fallback_available()
    )
    
    if not is_available:
        print("[ERROR] Kitchen printer is required but not available")
    
    return is_available


def _is_kitchen_receipt_type(receipt_type):
    return receipt_type == ReceiptType.KITCHEN


def _get_manager_for_receipt(receipt_type):
    return _kitchen_printer_manager if _is_kitchen_receipt_type(receipt_type) else _cashier_printer_manager


def get_printer_connection():
    """Get printer connection - use context manager instead"""
    try:
        with _cashier_printer_manager.get_connection() as conn:
            return conn
    except:
        return None


def open_cash_drawer():
    """Open the cash drawer via ESC/POS kick command through the main cashier printer."""
    # Skip cash drawer operation in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping cash drawer open")
        return True, "Cash drawer skipped (dev mode)"
    
    if not ESCPOS_AVAILABLE:
        return False, "ESCPOS library not available"

    try:
        if not _has_cashier_printer():
            return False, (
                "No cashier printer available via Windows spooler, LAN, or USB. "
                "Select a Windows printer queue or check network/USB configuration."
            )

        with _cashier_printer_manager.get_connection() as p:
            if p is None:
                return False, "Cannot connect to main cashier printer"
            p._raw(b'\x1b\x70\x00\x64\x64')
        return True, "Cash drawer opened successfully"
    except Exception as e:
        return False, str(e)


def print_test_page(target="cashier"):
    """Print a small test page on the selected cashier or kitchen printer.

    Returns:
        tuple: (success: bool, message: str)
    """
    import os
    target = (target or "cashier").strip().lower()
    if target not in {"cashier", "kitchen"}:
        return False, "Invalid printer target. Use 'cashier' or 'kitchen'."

    # Skip printing in development mode
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print(f"[DEV MODE] Printer checks disabled - skipping {target} test print")
        return True, f"{target.capitalize()} test print skipped (dev mode)"

    if not ESCPOS_AVAILABLE:
        return False, "ESCPOS library not available"

    manager = _kitchen_printer_manager if target == "kitchen" else _cashier_printer_manager
    role_label = "Kitchen" if target == "kitchen" else "Cashier"

    if not manager.has_configured_target():
        return False, (
            f"No {role_label.lower()} printer configured. "
            "Select a Windows printer queue or set the printer IP/port first, then click Save Setting."
        )

    try:
        with manager.get_connection() as p:
            if p is None:
                return False, f"Cannot connect to {role_label.lower()} printer"

            # Use the appropriate text mode for the target printer
            if target == "kitchen":
                _set_kitchen_text_mode(p)
            else:
                _set_standard_receipt_text(p)

            now = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

            p.set(align="center", bold=True)
            p._raw(b'\x1d\x21\x11')  # Double width + double height
            p.text(f"{role_label} Printer\n")
            p._raw(b'\x1d\x21\x00')  # Normal size
            p.text("TEST PRINT\n")
            p.set(align="center", bold=False)

            p.text("\n")
            p.set(align="center")
            p.text("This is a test print.\n")
            p.text("Your printer is working.\n")
            p.text("\n")

            p.set(align="left")
            p.text(f"Printer Target : {role_label}\n")
            p.text(f"Date / Time    : {now}\n")
            p.text(f"Terminal       : {get_report_header_fields().get('terminal_id', 'N/A')}\n")

            p.text("\n")
            p.set(align="center", bold=True)
            p.text("If you can read this,\n")
            p.text("your printer is ready.\n")
            p.set(align="center", bold=False)

            p.text("\n")
            p._raw(b'\x1b\x64\x05')  # Feed 5 lines
            p.cut()
        return True, f"{role_label} test print sent successfully."
    except Exception as e:
        print(f"[ERROR] {role_label} test print failed: {e}")
        return False, str(e)


def print_receipt(order, receipt_type, **kwargs):
    """Generic receipt renderer that handles all receipt types"""
    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping receipt printing")
        return False

    manager = _get_manager_for_receipt(receipt_type)
    if not manager.has_configured_target():
        if _is_kitchen_receipt_type(receipt_type):
            print("[INFO] Kitchen printer IP is not configured. Skipping kitchen receipt.")
        else:
            print("[ERROR] Main cashier printer IP is not configured. Set CASHIER_PRINTER_IPS, CASHIER_PRINTER_IP, LAN_PRINTER_IP, or PRINTER_IP.")
        return False
    
    try:
        with manager.get_connection() as p:
            if p is None:
                return False
                
            if receipt_type == ReceiptType.KITCHEN:
                _set_kitchen_text_mode(p)
            else:
                _set_standard_receipt_text(p)
            
            if receipt_type == ReceiptType.KITCHEN:
                return _print_kitchen_receipt(p, order)
            elif receipt_type == ReceiptType.CASHIER:
                return _print_cashier_receipt(p, order)
            elif receipt_type == ReceiptType.BILL:
                return _print_bill_receipt(p, order, kwargs.get('bill_data'))
            elif receipt_type == ReceiptType.OFFICIAL:
                return _print_official_receipt(p, order, kwargs.get('settlement'), kwargs.get('is_reprint', False))
            elif receipt_type == ReceiptType.Z_READING:
                return _print_z_reading(p, kwargs.get('z_reading_data'), kwargs.get('report_date'), kwargs.get('is_reprint', False))
            elif receipt_type == ReceiptType.X_READING:
                return _print_x_reading(p, kwargs.get('z_reading_data'), kwargs.get('report_date'), kwargs.get('x_count', 1))
            elif receipt_type == ReceiptType.CANCELLED_VOID_REFUND:
                return _print_cancelled_void_refund_report(p, kwargs.get('report_data'), kwargs.get('report_date'))
            elif receipt_type == ReceiptType.ITEM_SALES_REPORT:
                # Handle both single date and date range
                report_data = kwargs.get('report_data')
                report_date = kwargs.get('report_date')
                from_date = kwargs.get('from_date')
                to_date = kwargs.get('to_date')
                return _print_item_sales_report(p, report_data, report_date, from_date, to_date)
            elif receipt_type == ReceiptType.TAKEOUT_PICKUP_DELIVERY_REPORT:
                return _print_takeout_pickup_delivery_report(p, kwargs.get('report_data'), kwargs.get('report_date'))
            elif receipt_type == ReceiptType.CASHIER_ACCOUNTABILITY:
                return _print_cashier_accountability(p, kwargs.get('accountability_data'), kwargs.get('report_date'))
            else:
                print(f"[ERROR] Unknown receipt type: {receipt_type}")
                return False
    except Exception as e:
        print(f"[ERROR] Printing failed: {e}")
        return False

def _print_kitchen_receipt(p, order):
    """Print kitchen receipt copy"""
    try:
        _set_kitchen_text_mode(p)
        p.set(align="left", bold=False)

        # === Header (big but not too big) ===
        p.set(align="center", bold=True)
        p._raw(b'\x1d\x21\x01')   # Double height only
        p.text("ORDER SLIP\n")
        p._raw(b'\x1d\x21\x00')   # Reset to normal
        p.set(bold=False)

        # === Order details (normal) ===
        p.set(align="left")
        p.text(f"Order No: {order.order_no}\n")
        p.text(f"Date: {order.timestamp.strftime('%m/%d/%Y %I:%M %p')}\n")
        # Only print customer name if it exists
        if order.customer_name:
            p.text(f"Customer: {order.customer_name}\n")
        _print_kitchen_order_type_and_tables(p, order)

        # === Items ordered (slightly bigger for kitchen) ===
        _set_kitchen_text_mode(p)
        _print_kitchen_separator(p)
        p.set(align="center", bold=True)
        p._raw(b'\x1d\x21\x11')   # Double width + double height
        p.text("ITEMS ORDERED\n")
        p._raw(b'\x1d\x21\x00')
        p.set(align="left", bold=True)

        # Items render double-width on the kitchen printer, so each wrapped
        # segment must fit in half the receipt width to avoid hardware wrapping.
        kitchen_item_width = _receipt_width() // 2
        for item in order.items:
            # Print item name with modifier if exists
            item_text = f"{format_qty(item.quantity)} x {item.product_name}"
            if hasattr(item, 'modifier') and item.modifier:
                item_text += f" - {item.modifier}"
            _print_wrapped_item_line(p, item_text, kitchen_item_width, b'\x1d\x21\x11')
            _set_kitchen_text_mode(p)

        # === Totals (normal size) ===
        p.set(bold=False)
        _print_kitchen_separator(p)
        p.text(f"TOTAL: PHP {order.total:,.2f}\n")

        _finish_order_slip(p, "*** FOR KITCHEN ***")
        return True
    except Exception as e:
        print(f"[ERROR] Kitchen receipt printing failed: {e}")
        return False

def _print_cashier_receipt(p, order):
    """Print cashier receipt copy"""
    try:
        p.set(align="left", bold=False)

        # === Header (big but not too big) ===
        p.set(align="center", bold=True)
        p._raw(b'\x1d\x21\x01')   # Double height only
        p.text("ORDER SLIP\n")
        p._raw(b'\x1d\x21\x00')
        p.set(bold=False)

        # === Order details ===
        p.set(align="left")
        p.text(f"Order No: {order.order_no}\n")
        p.text(f"Date: {order.timestamp.strftime('%m/%d/%Y %I:%M %p')}\n")
        # Only print customer name if it exists
        if order.customer_name:
            p.text(f"Customer: {order.customer_name}\n")
        order_type = "DINE-IN" if order.order_type == "dinein" else "TAKEOUT"
        p.text(f"Type: {order_type}\n")
        if order.tables:
            p.text(f"Table(s): {order.tables}\n")

        # === Items ordered (normal size for cashier) ===
        p.text(_receipt_separator())
        p.text("ITEMS ORDERED\n")
        for item in order.items:
            # Print item name with modifier if exists
            item_text = f"{format_qty(item.quantity)} x {item.product_name}"
            if hasattr(item, 'modifier') and item.modifier:
                item_text += f" - {item.modifier}"
            _print_wrapped_item_line(p, item_text, _receipt_width())

        # === Totals (normal text only) ===
        p.text(_receipt_separator())
        p.text(f"TOTAL: PHP {order.total:,.2f}\n")

        _finish_order_slip(p, "*** FOR CASHIER ***")
        return True
    except Exception as e:
        print(f"[ERROR] Cashier receipt printing failed: {e}")
        return False

def _extract_beneficiary_list(type_data):
    """Safely extract list of beneficiary dicts from dict/list structure."""
    if not type_data:
        return []
    if isinstance(type_data, list):
        return [b for b in type_data if isinstance(b, dict)]
    if isinstance(type_data, dict):
        if 'name' in type_data or 'id' in type_data:
            return [type_data]
        def _safe_idx(k):
            try:
                return int(k)
            except (ValueError, TypeError):
                return 0
        sorted_keys = sorted(type_data.keys(), key=_safe_idx)
        return [type_data[k] for k in sorted_keys if isinstance(type_data[k], dict)]
    return []


def _print_bill_receipt(p, order, bill_data=None):
    """Print a bill receipt for an order with optional detailed bill data"""
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Official Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32') 
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")
        _set_body_line_spacing(p)
        p.text("\n")
        
        # === BILL OUT Title ===
        p._raw(b'\x1d\x21\x11')   # Double width and height
        p.text("BILL OUT\n")
        p._raw(b'\x1d\x21\x00')   # Reset size
        p.set(align='center')
        p.text(_receipt_separator())
        p.set(align='left', bold=False)

        # === Customer and date info ===
        if bill_data:
            customer_name = bill_data.get("customerName", "") or ""
            timestamp_str = bill_data.get("timestamp", "")
            order_no = bill_data.get("orderNo", "")

            if isinstance(timestamp_str, str) and timestamp_str:
                try:
                    if 'T' in timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    else:
                        timestamp = datetime.strptime(timestamp_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
                    formatted_date = timestamp.strftime('%b. %d, %Y (%a)')
                except:
                    formatted_date = timestamp_str
            else:
                formatted_date = timestamp_str
        else:
            customer_name = order.customer_name or ""
            formatted_date = order.timestamp.strftime('%b. %d, %Y (%a)')
            order_no = getattr(order, 'order_no', '')

        # Modified to keep the formatted date and remove "SAVORE-" prefix from bill #
        # Only print customer name line if there's a customer name
        if customer_name:
            p.text(f"{customer_name}\n")
        p.text(f"{formatted_date}\n")
        if order_no:
            # Remove "SAVORE-" prefix from order number
            clean_order_no = order_no.replace("SAVORE-", "")
            p.text(f"Bill#: {clean_order_no}\n")
        else:
            # Only add a blank line if we printed the customer name
            if customer_name:
                p.text("\n")
        
        _print_bill_order_type_and_table(p, order)

        p.set(align='center')
        p.text(_receipt_separator())
        p.set(align='left')

        # === Items ===
        if bill_data and "items" in bill_data:
            items = bill_data["items"]
        else:
            items = [{"product_name": item.product_name, "quantity": item.quantity, "price": item.price} for item in order.items]

        for item in items:
            name = item["product_name"]
            # Append modifier if exists
            modifier = item.get("modifier") if isinstance(item, dict) else getattr(item, "modifier", None)
            if modifier:
                name = f"{name} - {modifier}"
            # Two-column layout: qty + name on the left, total on the right
            for line in _format_item_two_columns(name, item["quantity"], item["price"]):
                p.text(line)

        p.set(align='center')
        p.text(_receipt_separator())
        p.set(align='left')

        # === Totals ===
        if bill_data:
            def bill_amount(key, fallback=0):
                try:
                    value = bill_data.get(key, fallback)
                    if value in (None, ""):
                        return fallback
                    amount = float(str(value).replace("PHP", "").replace(",", "").strip())
                    if amount < 0 and fallback > 0:
                        return fallback
                    return amount
                except (TypeError, ValueError):
                    return fallback

            total_with_vat = bill_amount("totalWithVat", order.total or 0)
            tax_exempt_amount = bill_amount("taxExemptAmount")
            subtotal_tax_exempt = bill_amount("subtotalTaxExempt")
            discount_amount = bill_amount("discountAmount")
            order_discount = bill_data.get("orderDiscount", "no_discount")
            final_total = bill_amount("finalTotal", order.total or 0)
            less_vat_amount = bill_amount("lessVatAmount")
            add_vat_amount = bill_amount("addVatAmount")

            if total_with_vat > 0:
                p.text(format_line("SUBTOTAL:", total_with_vat))
                
                # Check if this is a mixed discount (comma-separated discount types)
                if ',' in order_discount:
                    # Mixed discount - print individual breakdown for each discount type
                    discount_types = [dt.strip() for dt in order_discount.split(',')]
                    
                    # Get mixed discount details from bill_data
                    mixed_discounts = bill_data.get("mixedDiscounts", {})
                    
                    for discount_type in discount_types:
                        if discount_type == 'regular':
                            # Regular discount - get percentage
                            regular_percent = bill_data.get("regularDiscountPercent", 0)
                            regular_amount = mixed_discounts.get(discount_type, {}).get("discount", 0)
                            if regular_amount > 0:
                                p.text(format_line(f"LESS: {regular_percent}% DISCOUNT (REG):", regular_amount))
                        else:
                            # Other discount types - show VAT and discount
                            type_data = mixed_discounts.get(discount_type, {})
                            vat_amount = type_data.get("vat", 0)
                            type_discount = type_data.get("discount", 0)
                            
                            # Map discount type to label
                            label_map = {
                                'senior': 'SC',
                                'pwd': 'PWD',
                                'solo_parent': 'SOLO',
                                'athlete': 'NAAC',
                                'medal_of_valor': 'MOV'
                            }
                            label_suffix = label_map.get(discount_type, discount_type.upper())
                            
                            # Show VAT line
                            if vat_amount > 0:
                                p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                            
                            # Show discount line
                            if type_discount > 0:
                                percent = 10 if discount_type == 'solo_parent' else 20
                                p.text(format_line(f"LESS: {percent}% DISCOUNT ({label_suffix}):", type_discount))
                            
                            # Show ADD VAT back for NAAC/MOV
                            if discount_type in ['athlete', 'medal_of_valor'] and vat_amount > 0:
                                p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount))
                else:
                    # Single discount type - original logic
                    # Show VAT/tax exempt info for discounts that have tax exemption
                    if order_discount in ['senior', 'pwd', 'solo_parent']:
                        p.text(format_line("LESS: 12% VAT:", tax_exempt_amount))
                    # Show VAT breakdown for NAAC/MOV discounts
                    elif order_discount in ['athlete', 'medal_of_valor']:
                        p.text(format_line("LESS: 12% VAT:", less_vat_amount))
                    # Show discount for all discount types
                    if discount_amount > 0:
                        if order_discount == 'senior':
                            p.text(format_line("LESS: SC DISCOUNT 20%:", discount_amount))
                        elif order_discount == 'pwd':
                            p.text(format_line("LESS: PWD DISCOUNT 20%:", discount_amount))
                        elif order_discount == 'solo_parent':
                            p.text(format_line("LESS: SOLO DISCOUNT 10%:", discount_amount))
                        elif order_discount == 'athlete':
                            p.text(format_line("LESS: NAAC DISCOUNT 20%:", discount_amount))
                        elif order_discount == 'medal_of_valor':
                            p.text(format_line("LESS: MOV DISCOUNT 20%:", discount_amount))
                        elif order_discount == 'regular':
                            p.text(format_line("LESS: REGULAR DISCOUNT:", discount_amount))
                        elif order_discount == 'oth':
                            p.text(format_line("LESS: OTH DISCOUNT:", discount_amount))
                    # Show ADD VAT for NAAC/MOV discounts
                    if order_discount in ['athlete', 'medal_of_valor']:
                        p.text(format_line("ADD: 12% VAT:", add_vat_amount))

            # === TOTAL: left, amount center inline ===
            _print_emphasized_amount_line(p, "TOTAL:", f"PHP {final_total:,.2f}")

        else:
            final_total = order.total
            total_amount = f"PHP {final_total:,.2f}"
            total_line = f"TOTAL:".ljust(12) + total_amount.center(_receipt_width() - 12)
            p.set(bold=True)
            p._raw(b'\x1d\x21\x01')   # Double height only
            p.text(total_line + "\n")
            p._raw(b'\x1d\x21\x00')
            p.set(bold=False)

        p.set(align='center')
        p.text(_receipt_separator())
        p.set(align='left')

        # === Cashier info & time ===
        cashier = getattr(current_user, 'username', 'Cashier')
        total_items = sum([item["quantity"] for item in items])
        current_time = datetime.now().strftime('%I:%M%p')

        p.text(f"{cashier:<16} {total_items} item(s) {current_time:>10}\n")

        p.set(align='center')
        p.text(_receipt_separator())
        p.set(align='left')

        # === Discount beneficiary info (similar to receipt format) ===
        if bill_data:
            order_discount = bill_data.get("orderDiscount", "no_discount")
            discount_beneficiaries = bill_data.get("discountBeneficiaries", {})

            if order_discount and order_discount != "no_discount":
                # Handle mixed discounts (comma-separated)
                if ',' in order_discount:
                    discount_types = [dt.strip() for dt in order_discount.split(',')]
                    
                    # Group beneficiaries by type
                    names_by_type = {}
                    ids_by_type = {}
                    
                    for dtype in discount_types:
                        if dtype in discount_beneficiaries:
                            type_data = discount_beneficiaries[dtype]
                            names_by_type[dtype] = []
                            ids_by_type[dtype] = []
                            beneficiaries = _extract_beneficiary_list(type_data)
                            for beneficiary in beneficiaries:
                                if beneficiary.get('name'):
                                    names_by_type[dtype].append(beneficiary['name'])
                                    ids_by_type[dtype].append(beneficiary.get('id', ''))
                    
                    # Display OSCA/PWD section (senior and pwd) - only if has beneficiaries
                    has_senior_pwd = names_by_type.get('senior') or names_by_type.get('pwd')
                    if has_senior_pwd:
                        first_entry = True
                        for discount_type in ['senior', 'pwd']:
                            type_names = names_by_type.get(discount_type, [])
                            type_ids = ids_by_type.get(discount_type, [])
                            
                            for i in range(len(type_names)):
                                name = type_names[i]
                                person_id = type_ids[i] if i < len(type_ids) else ""
                                
                                if person_id:
                                    person_info = f"{name} (ID: {person_id})"
                                else:
                                    person_info = f"{name}"
                                
                                if first_entry:
                                    label = "OSCA/PWD: "
                                    p.text(label)
                                    first_entry = False
                                else:
                                    label = "         "  # 9 spaces to align
                                    p.text(label)
                                
                                # Enable underline for person info
                                p._raw(b'\x1b-\x01')  # Enable single underline
                                p.text(person_info)
                                remaining_space = _receipt_width() - len(label) - len(person_info)
                                if remaining_space > 0:
                                    p.text(" " * remaining_space)
                                p._raw(b'\x1b-\x00')  # Disable underline
                                p.text("\n")
                                
                                # Add signature line below each entry
                                sig_label = "Signature: "
                                p.text(sig_label)
                                p._raw(b'\x1b-\x01')
                                p.text(" " * (_receipt_width() - len(sig_label)))
                                p._raw(b'\x1b-\x00')
                                p.text("\n")
                    
                    # Display SOLO section - only if has beneficiaries
                    solo_names = names_by_type.get('solo_parent', [])
                    if solo_names:
                        first_solo = True
                        solo_ids = ids_by_type.get('solo_parent', [])
                        
                        for i in range(len(solo_names)):
                            name = solo_names[i]
                            person_id = solo_ids[i] if i < len(solo_ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_solo:
                                label = "SOLO: "
                                p.text(label)
                                first_solo = False
                            else:
                                label = "      "  # 6 spaces to align
                                p.text(label)
                            
                            p._raw(b'\x1b-\x01')
                            p.text(person_info)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                            
                            # Add signature line below each entry
                            sig_label = "Signature: "
                            p.text(sig_label)
                            p._raw(b'\x1b-\x01')
                            p.text(" " * (_receipt_width() - len(sig_label)))
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                    
                    # Display NAAC/MOV section - only if has beneficiaries
                    naac_names = names_by_type.get('athlete', [])
                    mov_names = names_by_type.get('medal_of_valor', [])
                    if naac_names or mov_names:
                        first_naac = True
                        for discount_type in ['athlete', 'medal_of_valor']:
                            type_names = names_by_type.get(discount_type, [])
                            type_ids = ids_by_type.get(discount_type, [])
                            
                            for i in range(len(type_names)):
                                name = type_names[i]
                                person_id = type_ids[i] if i < len(type_ids) else ""
                                
                                if person_id:
                                    person_info = f"{name} (ID: {person_id})"
                                else:
                                    person_info = f"{name}"
                                
                                if first_naac:
                                    label = "NAAC/MOV: "
                                    p.text(label)
                                    first_naac = False
                                else:
                                    label = "          "  # 10 spaces to align
                                    p.text(label)
                                
                                p._raw(b'\x1b-\x01')
                                p.text(person_info)
                                remaining_space = _receipt_width() - len(label) - len(person_info)
                                if remaining_space > 0:
                                    p.text(" " * remaining_space)
                                p._raw(b'\x1b-\x00')
                                p.text("\n")
                                
                                # Add signature line below each entry
                                sig_label = "Signature: "
                                p.text(sig_label)
                                p._raw(b'\x1b-\x01')
                                p.text(" " * (_receipt_width() - len(sig_label)))
                                p._raw(b'\x1b-\x00')
                                p.text("\n")
                
                else:
                    # Single discount type
                    dtype = order_discount
                    type_data = discount_beneficiaries.get(dtype, {})
                    
                    # Extract names and IDs
                    names = []
                    ids = []
                    beneficiaries = _extract_beneficiary_list(type_data)
                    for beneficiary in beneficiaries:
                        if beneficiary.get('name'):
                            names.append(beneficiary['name'])
                            ids.append(beneficiary.get('id', ''))
                    
                    if dtype in ['senior', 'pwd'] and names:
                        # Display on OSCA/PWD line
                        first_entry = True
                        for i in range(len(names)):
                            name = names[i]
                            person_id = ids[i] if i < len(ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "OSCA/PWD: "
                                p.text(label)
                                first_entry = False
                            else:
                                label = "         "
                                p.text(label)
                            
                            p._raw(b'\x1b-\x01')
                            p.text(person_info)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                            
                            # Add signature line below each entry
                            sig_label = "Signature: "
                            p.text(sig_label)
                            p._raw(b'\x1b-\x01')
                            p.text(" " * (_receipt_width() - len(sig_label)))
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                    
                    elif dtype == 'solo_parent' and names:
                        # Display on SOLO line
                        first_entry = True
                        for i in range(len(names)):
                            name = names[i]
                            person_id = ids[i] if i < len(ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "SOLO: "
                                p.text(label)
                                first_entry = False
                            else:
                                label = "      "
                                p.text(label)
                            
                            p._raw(b'\x1b-\x01')
                            p.text(person_info)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                            
                            # Add signature line below each entry
                            sig_label = "Signature: "
                            p.text(sig_label)
                            p._raw(b'\x1b-\x01')
                            p.text(" " * (_receipt_width() - len(sig_label)))
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                    
                    elif dtype in ['athlete', 'medal_of_valor'] and names:
                        # Display on NAAC/MOV line
                        first_entry = True
                        for i in range(len(names)):
                            name = names[i]
                            person_id = ids[i] if i < len(ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "NAAC/MOV: "
                                p.text(label)
                                first_entry = False
                            else:
                                label = "          "
                                p.text(label)
                            
                            p._raw(b'\x1b-\x01')
                            p.text(person_info)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
                            
                            # Add signature line below each entry
                            sig_label = "Signature: "
                            p.text(sig_label)
                            p._raw(b'\x1b-\x01')
                            p.text(" " * (_receipt_width() - len(sig_label)))
                            p._raw(b'\x1b-\x00')
                            p.text("\n")
        p.set(align='center')
        p.text("*** THANK YOU ***\n")
        p.text("\n")
        p.set(bold=True)
        p.text("THIS DOCUMENT IS NOT VALID FOR CLAIM OF INPUT TAX\n")
        p.set(bold=False)
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] Bill receipt printing failed: {e}")
        return False

def _print_z_reading(p, z_reading_data, report_date, is_reprint=False):
    """Print Z-reading report on dot matrix printer - matches frontend format exactly"""
    try:
        print(f"[DEBUG] Starting Z-reading print. Data fields count: {len(z_reading_data)}")
        print(f"[DEBUG] Gross Sales: {z_reading_data.get('Gross Sales', 0)}, VAT Sales: {z_reading_data.get('VAT Sales', 0)}")

        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)
        print("[DEBUG] Printer formatting set")

        # === Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")       
        _set_body_line_spacing(p)
        print("[DEBUG] Header printed")

        p.text("\n")

        # === Z-READING REPORT Header ===
        p.set(align='center', bold=True)
        p.text("Z-READING REPORT\n")
        print("[DEBUG] Z-Reading title printed")
        
        # Import datetime for reprint timestamp
        from datetime import datetime
        
        # Display REPRINT label if this is a reprint
        if is_reprint:
            p._raw(b'\x1d\x21\x11')   # Double height for REPRINT
            p.text("***REPRINT***\n")
            p._raw(b'\x1d\x21\x00')   # Reset to normal size
            p.set(bold=False, align='left')
            p.text(_receipt_separator())
            # Add reprint date and time (only for reprints)
            reprint_datetime = datetime.now()
            p.text(f"Reprint Date: {reprint_datetime.strftime('%m/%d/%Y')}\n")
            p.text(f"Reprint Time: {reprint_datetime.strftime('%I:%M %p')}\n")
        else:
            p.set(bold=False, align='left')
            p.text(_receipt_separator())
        
        # Original Z-Reading date and time (always show)
        p.text(f"Report Date: {z_reading_data.get('Report Date', 'N/A')}\n")
        p.text(f" Report Time: {z_reading_data.get('Report Time', 'N/A')}\n")
        p.text(f"Start Date & Time: {z_reading_data.get('Start Date', 'N/A')} {z_reading_data.get('Start Time', 'N/A')}\n")
        p.text(f"End Date & Time: {z_reading_data.get('End Date', 'N/A')} {z_reading_data.get('End Time', 'N/A')}\n")
        
        # Format SI numbers - remove prefix if present
        beg_si = z_reading_data.get("Beginning SI No", "0000000000")
        end_si = z_reading_data.get("Ending SI No", "0000000000")
        if beg_si and beg_si != "0000000000" and "-" in str(beg_si):
            beg_si = str(beg_si).split('-')[-1].zfill(10)
        if end_si and end_si != "0000000000" and "-" in str(end_si):
            end_si = str(end_si).split('-')[-1].zfill(10)
        
        void_count = z_reading_data.get("# Voided", 0)
        refund_count = z_reading_data.get("# Refunded", 0)
        
        # Get actual beginning and ending void/refund numbers from OrderAuditLog
        beg_void = z_reading_data.get("Beginning VOID No", "0000000000")
        end_void = z_reading_data.get("Ending VOID No", "0000000000")
        beg_refund = z_reading_data.get("Beginning REFUND No", "0000000000")
        end_refund = z_reading_data.get("Ending REFUND No", "0000000000")
        
        p.text(f"Beg. SI #: {beg_si}\n")
        p.text(f"End. SI #: {end_si}\n")
        p.text(f"Beg. VOID #: {beg_void}\n")
        p.text(f"End. VOID #: {end_void}\n")
        p.text(f"Beg. REFUND #: {beg_refund}\n")
        p.text(f"End. REFUND #: {end_refund}\n")
        p.text(f"Reset Counter No. {str(z_reading_data.get('Reset Counter', 0)).zfill(2)}\n")
        p.text(f"Z Counter No. : {z_reading_data.get('Z Counter #', 1)}\n")
        p.text(format_line_count("# Transactions:", z_reading_data.get("# Transactions", 0)))
        p.text(format_line_count("# Customers:", z_reading_data.get("# Customers( total no_pax/ covers)", 0)))
        p.text(_receipt_separator())
        print("[DEBUG] SI and counter info printed")
        
        # === BREAKDOWN OF SALES ===
        gross_amount = z_reading_data.get("Gross Sales", 0)
        present_accumulated_sales = z_reading_data.get("Present Accumulated Sales", 0)
        p.text(format_line("Present Accumulated Sales:", present_accumulated_sales))
        p.text(format_line("Previous Accumulated Sales:", z_reading_data.get('PREVIOUS NGRT', 0)))
        p.text(format_line("Sales for the Day:", gross_amount))
        p.text(_receipt_separator())
        p.text("BREAKDOWN OF SALES\n")
        p.text(format_line("VATABLE SALES :", z_reading_data.get('VAT Sales', 0)))
        p.text(format_line("VAT AMOUNT:", z_reading_data.get('VAT Collected', 0)))
        p.text(format_line("VAT EXEMPT SALES:", z_reading_data.get('VAT Exempt Sales', 0)))
        p.text(format_line("ZERO RATED SALES:", 0.00))
        p.text(_receipt_separator())
        print("[DEBUG] Breakdown of sales printed")
        
        # === TRANSACTION TOTALS ===
        less_discount = z_reading_data.get("Total Discount", 0)
        less_void = z_reading_data.get("Amount Voided", 0)
        less_vat_adjustment = z_reading_data.get("Total VAT Adjustment", 0)
        net_amount = z_reading_data.get("Net Amount", 0)
        
        p.text(format_line("Gross Amount:", gross_amount))
        p.text(format_line("Less Discount:", less_discount))
        p.text(format_line("Less Refund:", z_reading_data.get("Total Refund", 0)))
        p.text(format_line("Less Void:", less_void))
        p.text(format_line("Less VAT Adjustment:", less_vat_adjustment))
        p.text(format_line("Net Amount:", net_amount))
        p.text(_receipt_separator())
        
        # === DISCOUNT SUMMARY ===
        p.text("DISCOUNT SUMMARY\n")
        p.text(format_line("SC Disc. :", z_reading_data.get('Senior Citizen Discount', 0)))
        p.text(format_line("PWD Disc. :", z_reading_data.get('PWD Discount', 0)))
        p.text(format_line("NAAC Disc. :", z_reading_data.get('National Athlete Discount', 0)))
        p.text(format_line("MOV Disc. :", z_reading_data.get('Medal of Valor Discount', 0)))
        p.text(format_line("SOLO Disc. :", z_reading_data.get('Solo Parent Discount', 0)))
        p.text(format_line("Other Disc. :", z_reading_data.get('Other Discount', 0)))
        p.text(_receipt_separator())
        
        # === SALES ADJUSTMENT ===
        p.text("SALES ADJUSTMENT\n")
        p.text(format_line("VOID :", less_void))
        p.text(format_line("REFUND :", z_reading_data.get("Total Refund", 0)))
        p.text(_receipt_separator())
        
        # === VAT ADJUSTMENT ===
        p.text("VAT ADJUSTMENT\n")
        p.text(format_line(" SC TRANS. :", z_reading_data.get('VAT Adj SC', 0)))
        p.text(format_line(" PWD TRANS :", z_reading_data.get('VAT Adj PWD', 0)))
        p.text(format_line(" SOLO TRANS :", z_reading_data.get('VAT Adj Solo', 0)))
        p.text(format_line(" ZERO-RATED TRANS.:", 0.00))
        p.text(format_line("VAT on Return:", 0.00))
        p.text(format_line(" Other VAT Adjustments:", 0.00))
        p.text(_receipt_separator())
        
        # === TRANSACTION SUMMARY ===
        p.text("TRANSACTION SUMMARY\n")
        cash_in_drawer = z_reading_data.get("Cash In Drawer", 0)
        credit_card = z_reading_data.get("Credit Card", 0)
        maya = z_reading_data.get("Maya", 0)
        gcash = z_reading_data.get("Gcash", 0)
        gift_check = z_reading_data.get("Gift Check", 0)
        cheque = z_reading_data.get("Cheque", 0)
        debit_card = z_reading_data.get("Debit Card", 0)
        payments_received = z_reading_data.get("Payments Received", 0)
        
        # SHORT/OVER = (cash_in_drawer + non_cash) - (Opening Fund + payments_received)
        opening_fund = z_reading_data.get("Opening Fund", 0)
        short_over = z_reading_data.get("Short Over")
        if short_over is None:
            short_over = (cash_in_drawer + credit_card + gcash + maya + gift_check + cheque + debit_card) - (opening_fund + payments_received)
        
        p.text(format_line("Cash In Drawer:", cash_in_drawer))
        p.text(format_line("CHEQUE", cheque))
        p.text(format_line("CREDIT CARD", credit_card))
        p.text(format_line("MAYA", maya))
        p.text(format_line("GCASH", gcash))
        p.text(format_line("DEBIT CARD", debit_card))
        p.text(format_line("GIFT CERTIFICATE", gift_check))
        p.text(format_line("Opening Fund:", opening_fund))
        p.text(format_line("Less Withdrawal:", 0.00))
        p.text(format_line("Payments Received:", payments_received))
        p.text(_receipt_separator())
        short_over_display = "0.00" if round(short_over, 2) == 0 else f"{abs(short_over):.2f}{'+' if short_over > 0 else '-'}"
        p.text(f"SHORT/OVER: {short_over_display}\n")
        p.text(_receipt_separator())
        p.text("\n")
        print("[DEBUG] All sections printed successfully")
        
        p.set(align='center')
        p.text("*** END OF Z-READING REPORT ***\n")
        p.text("\n\n")
        print("[DEBUG] Footer printed")

        p.cut()
        print("[DEBUG] Z-reading print completed successfully")
        return True
    except Exception as e:
        print(f"[ERROR] Z-reading printing failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def _print_x_reading(p, x_reading_data, report_date, x_count=1):
    """Print X-reading report on dot matrix printer - same as Z-reading but not finalized
    
    X-reading uses the exact same format and data as Z-reading, just different header and counter.
    """
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")       
        _set_body_line_spacing(p)

        p.text("\n")

        # === X-READING REPORT Header ===
        p.set(align='center', bold=True)
        p.text("X-READING REPORT\n")
        
        p.set(bold=False, align='left')
        p.text(_receipt_separator())
        p.text(f"Report Date: {x_reading_data.get('Report Date', 'N/A')}\n")
        p.text(f" Report Time: {x_reading_data.get('Report Time', 'N/A')}\n")
        p.text(f"Start Date & Time: {x_reading_data.get('Start Date', 'N/A')} {x_reading_data.get('Start Time', 'N/A')}\n")
        p.text(f"End Date & Time: {x_reading_data.get('End Date', 'N/A')} {x_reading_data.get('End Time', 'N/A')}\n")
        
        # Format SI numbers - remove prefix if present
        beg_si = x_reading_data.get("Beginning SI No", "0000000000")
        end_si = x_reading_data.get("Ending SI No", "0000000000")
        if beg_si and beg_si != "0000000000" and "-" in str(beg_si):
            beg_si = str(beg_si).split('-')[-1].zfill(10)
        if end_si and end_si != "0000000000" and "-" in str(end_si):
            end_si = str(end_si).split('-')[-1].zfill(10)
        
        # Get actual beginning and ending void/refund numbers from OrderAuditLog
        beg_void = x_reading_data.get("Beginning VOID No", "0000000000")
        end_void = x_reading_data.get("Ending VOID No", "0000000000")
        beg_refund = x_reading_data.get("Beginning REFUND No", "0000000000")
        end_refund = x_reading_data.get("Ending REFUND No", "0000000000")
        
        p.text(f"Beg. SI #: {beg_si}\n")
        p.text(f"End. SI #: {end_si}\n")
        p.text(f"Beg. VOID #: {beg_void}\n")
        p.text(f"End. VOID #: {end_void}\n")
        p.text(f"Beg. REFUND #: {beg_refund}\n")
        p.text(f"End. REFUND #: {end_refund}\n")
        p.text(f"Reset Counter No. {str(x_reading_data.get('Reset Counter', 0)).zfill(2)}\n")
        p.text(f"X Counter No. : {x_count}\n")  # X counter instead of Z counter
        p.text(_receipt_separator())
        
        # === BREAKDOWN OF SALES (same as Z-reading) ===
        gross_amount = x_reading_data.get("Gross Sales", 0)
        present_accumulated_sales = x_reading_data.get("Present Accumulated Sales", 0)
        p.text(format_line("Present Accumulated Sales:", present_accumulated_sales))
        p.text(format_line("Previous Accumulated Sales:", x_reading_data.get('PREVIOUS NGRT', 0)))
        p.text(format_line("Sales for the Day:", gross_amount))
        p.text(_receipt_separator())
        p.text("BREAKDOWN OF SALES\n")
        p.text(format_line("VATABLE SALES :", x_reading_data.get('VAT Sales', 0)))
        p.text(format_line("VAT AMOUNT:", x_reading_data.get('VAT Collected', 0)))
        p.text(format_line("VAT EXEMPT SALES:", x_reading_data.get('VAT Exempt Sales', 0)))
        p.text(format_line("ZERO RATED SALES:", 0.00))
        p.text(_receipt_separator())
        
        # === TRANSACTION TOTALS ===
        less_discount = x_reading_data.get("Total Discount", 0)
        less_void = x_reading_data.get("Amount Voided", 0)
        less_vat_adjustment = x_reading_data.get("Total VAT Adjustment", 0)
        net_amount = x_reading_data.get("Net Amount", 0)
        
        p.text(format_line("Gross Amount:", gross_amount))
        p.text(format_line("Less Discount:", less_discount))
        p.text(format_line("Less Refund:", x_reading_data.get("Total Refund", 0)))
        p.text(format_line("Less Void:", less_void))
        p.text(format_line("Less VAT Adjustment:", less_vat_adjustment))
        p.text(format_line("Net Amount:", net_amount))
        p.text(_receipt_separator())
        
        # === DISCOUNT SUMMARY ===
        p.text("DISCOUNT SUMMARY\n")
        p.text(format_line("SC Disc. :", x_reading_data.get('Senior Citizen Discount', 0)))
        p.text(format_line("PWD Disc. :", x_reading_data.get('PWD Discount', 0)))
        p.text(format_line("NAAC Disc. :", x_reading_data.get('National Athlete Discount', 0)))
        p.text(format_line("MOV Disc. :", x_reading_data.get('Medal of Valor Discount', 0)))
        p.text(format_line("SOLO Disc. :", x_reading_data.get('Solo Parent Discount', 0)))
        p.text(format_line("Other Disc. :", x_reading_data.get('Other Discount', 0)))
        p.text(_receipt_separator())
        
        # === SALES ADJUSTMENT ===
        p.text("SALES ADJUSTMENT\n")
        p.text(format_line("VOID :", less_void))
        p.text(format_line("REFUND :", x_reading_data.get("Total Refund", 0)))
        p.text(_receipt_separator())
        
        # === VAT ADJUSTMENT ===
        p.text("VAT ADJUSTMENT\n")
        p.text(format_line(" SC TRANS. :", x_reading_data.get('VAT Adj SC', 0)))
        p.text(format_line(" PWD TRANS :", x_reading_data.get('VAT Adj PWD', 0)))
        p.text(format_line(" SOLO TRANS :", x_reading_data.get('VAT Adj Solo', 0)))
        p.text(format_line(" ZERO-RATED TRANS.:", 0.00))
        p.text(format_line("VAT on Return:", 0.00))
        p.text(format_line(" Other VAT Adjustments:", 0.00))
        p.text(_receipt_separator())
        
        # === TRANSACTION SUMMARY ===
        p.text("TRANSACTION SUMMARY\n")
        cash_in_drawer = x_reading_data.get("Cash In Drawer", 0)
        credit_card = x_reading_data.get("Credit Card", 0)
        maya = x_reading_data.get("Maya", 0)
        gcash = x_reading_data.get("Gcash", 0)
        gift_check = x_reading_data.get("Gift Check", 0)
        cheque = x_reading_data.get("Cheque", 0)
        debit_card = x_reading_data.get("Debit Card", 0)
        payments_received = x_reading_data.get("Payments Received", 0)
        
        # SHORT/OVER = (cash_in_drawer + non_cash) - (Opening Fund + payments_received)
        opening_fund = x_reading_data.get("Opening Fund", 0)
        short_over = x_reading_data.get("Short Over")
        if short_over is None:
            short_over = (cash_in_drawer + credit_card + gcash + maya + gift_check + cheque + debit_card) - (opening_fund + payments_received)
        
        p.text(format_line("Cash In Drawer:", cash_in_drawer))
        p.text(format_line("CHEQUE", cheque))
        p.text(format_line("CREDIT CARD", credit_card))
        p.text(format_line("MAYA", maya))
        p.text(format_line("GCASH", gcash))
        p.text(format_line("DEBIT CARD", debit_card))
        p.text(format_line("GIFT CERTIFICATE", gift_check))
        p.text(format_line("Opening Fund:", opening_fund))
        p.text(format_line("Less Withdrawal:", 0.00))
        p.text(format_line("Payments Received:", payments_received))
        p.text(_receipt_separator())
        short_over_display = "0.00" if round(short_over, 2) == 0 else f"{abs(short_over):.2f}{'+' if short_over > 0 else '-'}"
        p.text(f"SHORT/OVER: {short_over_display}\n")
        p.text(_receipt_separator())
        p.text("\n")
        
        p.set(align='center')
        p.text("*** END OF X-READING REPORT ***\n")
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] X-reading printing failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def _print_official_receipt(p, order, settlement=None, is_reprint=False):
    """Print an official receipt for an order with settlement details"""
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # Function to print a single receipt copy
        def print_receipt_copy(copy_type):
            # === Header ===
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32') 
            p.set(align='center', bold=True)
            for line in get_receipt_header():
                p.text(f"{line}\n")
            _set_body_line_spacing(p)

            p.text(_receipt_separator())

            # Display Sales Invoice Number with larger font
            invoice_no = getattr(order, 'invoice_no', '')
            if invoice_no:
                p.set(bold=True)
                p._raw(b'\x1d\x21\x01')   # Double height for sales invoice
                # Extract just the number part from INV-000003
                invoice_number = invoice_no.replace('INV-', '') if invoice_no.startswith('INV-') else invoice_no
                p.text(f"SALES INVOICE \n")
                p._raw(b'\x1d\x21\x00')   # Reset to normal size
                p.set(bold=False)

            # Display REPRINT label if this is a reprint
            if is_reprint:
                p.set(bold=True)
                p._raw(b'\x1d\x21\x11')   # Double height for REPRINT
                p.text("***REPRINT***\n")
                p._raw(b'\x1d\x21\x00')   # Reset to normal size
                p.set(bold=False)
            p.text(_receipt_separator())

            # === Order Info ===
            p.set(align='left')
            p.text(f"SALES INVOICE #{invoice_number}\n")
            
            # Only show current date/time at top if this is a reprint
            if is_reprint:
                current_date = datetime.now().strftime('%b %d, %Y(%a)')
                current_time = datetime.now().strftime('%I:%M %p')
                p.text(f"Reprint: {current_date} {current_time}\n")
            
            # Show Bill# with simplified format (just the number)
            # Extract number from order_no format SAVORE-000003
            if order.order_no and '-' in order.order_no:
                bill_no = order.order_no.split('-')[-1]  # Get just the number part
            else:
                bill_no = order.order_no
            p.text(f"Bill# {bill_no}\n")
            
            # Get settlement timestamp for invoice date/time
            if settlement and hasattr(settlement, 'timestamp') and settlement.timestamp:
                order_date = settlement.timestamp.strftime('%b %d, %Y(%a)')
                order_time = settlement.timestamp.strftime('%I:%M %p')
            else:
                # Fallback to order timestamp if settlement timestamp not available
                order_date = order.timestamp.strftime('%b %d, %Y(%a)')
                order_time = order.timestamp.strftime('%I:%M %p')
            
            # Display cashier and terminal info below bill number
            # Use cashier from settlement if available, otherwise use current_user
            if settlement and settlement.cashier:
                cashier = settlement.cashier.username
            else:
                cashier = getattr(current_user, 'username', 'Cashier')
            p.text(f"Cashier: {cashier}\n")
            p.text(f"Terminal ID: POS-01\n")
            p.text(f"Date: {order_date}  Time: {order_time}\n")
            
            # Get total_no_pax from settlement if available, otherwise calculate it
            if settlement and hasattr(settlement, 'total_no_pax') and settlement.total_no_pax:
                actual_pax = settlement.total_no_pax
            else:
                # Calculate total_pax as fallback
                actual_pax = 0
                for item in order.items:
                    product = Product.query.get(item.product_id)
                    no_pax = 1
                    if product and hasattr(product, 'no_pax') and product.no_pax is not None:
                        no_pax = product.no_pax
                    if no_pax > 1:
                        actual_pax += item.quantity * no_pax
                if actual_pax == 0:
                    actual_pax = 1
            
            _print_receipt_order_type_and_table(p, order, "Table #:")
            
            # Show total no pax for all orders (simple left-aligned)
            p.text(f"Total # pax: {actual_pax}\n")

            # Display discount counts below Total no pax for mixed discounts
            if settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type and ',' in settlement.order_discount_type:
                # Mixed discount types - display counts below Total no pax
                discount_types = [dt.strip() for dt in settlement.order_discount_type.split(',')]
                
                # Get discount names and IDs
                names_by_type = {}
                if settlement.discount_name:
                    discount_names_list = [n.strip() for n in settlement.discount_name.split(',') if n.strip()]
                    for name_entry in discount_names_list:
                        if ':' in name_entry:
                            dtype, name = name_entry.split(':', 1)
                            if dtype not in names_by_type:
                                names_by_type[dtype] = []
                            names_by_type[dtype].append(name)
                
                # Display count for each discount type
                for discount_type in discount_types:
                    type_names = names_by_type.get(discount_type, [])
                    count = len(type_names) if type_names else 1
                    
                    if discount_type == 'senior':
                        p.text(f"SC: {count}\n")
                    elif discount_type == 'pwd':
                        p.text(f"PWD: {count}\n")
                    elif discount_type == 'solo_parent':
                        p.text(f"SOLO: {count}\n")
                    elif discount_type == 'athlete':
                        p.text(f"National Athlete: {count}\n")
                    elif discount_type == 'medal_of_valor':
                        p.text(f"Medal of Valor: {count}\n")

            # Calculate total_pax for internal use (not displayed)
            total_pax = 0
            for item in order.items:
                product = Product.query.get(item.product_id)
                no_pax = 1
                if product and hasattr(product, 'no_pax') and product.no_pax is not None:
                    no_pax = product.no_pax
                # Only include items where no_pax > 1 in the total_pax calculation
                if no_pax > 1:
                    total_pax += item.quantity * no_pax
            # If total_pax is 0 (no items with no_pax > 1), set it to 1 as minimum
            if total_pax == 0:
                total_pax = 1

            # Display number of senior citizens or PWDs in header if applicable (for single discount types)
            if settlement and settlement.order_discount_type and ',' not in settlement.order_discount_type:
                if settlement.order_discount_type == 'senior' and settlement.discount_quantity:
                    p.text(f"SC: {settlement.discount_quantity}\n")
                elif settlement.order_discount_type == 'pwd' and settlement.discount_quantity:
                    p.text(f"PWD: {settlement.discount_quantity}\n")
                elif settlement.order_discount_type == 'athlete' and settlement.discount_quantity:
                    p.text(f"National Athlete: {settlement.discount_quantity}\n")
                elif settlement.order_discount_type == 'medal_of_valor' and settlement.discount_quantity:
                    p.text(f"Medal of Valor: {settlement.discount_quantity}\n")
                elif settlement.order_discount_type == 'solo_parent' and settlement.discount_quantity:
                    p.text(f"SOLO: {settlement.discount_quantity}\n")
            p.text(_receipt_separator())

            # === Items ===

            for item in order.items:
                name = item.product_name
                # Append modifier if exists
                if hasattr(item, 'modifier') and item.modifier:
                    name = f"{name} - {item.modifier}"
                # Two-column layout: qty + name on the left, total on the right
                for line in _format_item_two_columns(name, item.quantity, item.price):
                    p.text(line)
            
            # Display total items count
            total_items = sum([item.quantity for item in order.items])
            p.text(f"                {total_items} item(s)\n")

            # Compute base items total (sum of item amounts)
            items_total = sum([item.price * item.quantity for item in order.items])

            # Check if any product is in the "Gift Check" category or has a GC code
            has_gift_check_product = False
            for item in order.items:
                product = Product.query.get(item.product_id)
                if product and (product.category == 'Gift Check' or product.category == 'Gift Certificate' or product.gift_certificate is not None):
                    has_gift_check_product = True
                    break

            # === Calculations ===
            if has_gift_check_product:
                # For Gift Check products, show TOTAL and TOTAL AMT DUE
                p.text(format_line("TOTAL:", items_total))
                total_value = (settlement.final_total if settlement and hasattr(settlement, 'final_total') and settlement.final_total is not None else order.total) if settlement else order.total
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", total_value)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - (total_value if total_value is not None else order.total)
                    p.text(format_line("CHANGE:", change))

                # Display payment method after TOTAL
                if settlement:
                    if settlement.payment_method == 'cash':
                        pm = 'Cash'
                    elif settlement.payment_method == 'card':
                        card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                        pm = f'Credit ({card_type})'
                    elif settlement.payment_method == 'gift_check':
                        pm = 'Gift Check'
                    else:
                        pm = settlement.payment_method.capitalize()
                    p.text(format_line("Payment Method:", pm))

                if settlement and hasattr(settlement, 'payment_method') and settlement.payment_method == 'cash' and hasattr(settlement, 'cash_received') and settlement.cash_received:
                    p.text(format_line("CASH:", settlement.cash_received))
                    settlement_total = settlement.final_total if settlement and hasattr(settlement, 'final_total') else order.total
                    change = (settlement.cash_received or 0) - (settlement_total if settlement_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
                elif settlement and hasattr(settlement, 'payment_method') and settlement.payment_method == 'gift_check' and hasattr(settlement, 'gift_check_amount') and settlement.gift_check_amount:
                    p.text(format_line("GIFT CHECK:", settlement.gift_check_amount))
                    if settlement and hasattr(settlement, 'cash_received') and settlement.cash_received and settlement.cash_received > 0:
                        p.text(format_line("CASH:", settlement.cash_received))
                        total_received = (settlement.gift_check_amount or 0) + (settlement.cash_received or 0)
                        settlement_total = settlement.final_total if settlement and hasattr(settlement, 'final_total') else order.total
                        change = total_received - (settlement_total if settlement_total is not None else order.total)
                        p.text(format_line("CHANGE:", change))
                elif settlement and hasattr(settlement, 'payment_method') and settlement.payment_method == 'cheque' and hasattr(settlement, 'cheque_amount') and settlement.cheque_amount:
                    p.text(format_line("CHEQUE AMOUNT:", settlement.cheque_amount))
                    settlement_total = settlement.final_total if settlement and hasattr(settlement, 'final_total') else order.total
                    change = (settlement.cheque_amount or 0) - (settlement_total if settlement_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
            elif settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type and ',' in settlement.order_discount_type:
                # Mixed discount types (comma-separated: e.g., 'pwd,senior')
                # Mixed discount printing - no debug output
                
                p.text(format_line("TOTAL:", items_total))
                
                # Parse the comma-separated discount types
                discount_types = [dt.strip() for dt in settlement.order_discount_type.split(',')]
                # Parsed discount_types
                
                # Get discount names and IDs
                discount_names_list = []
                discount_ids_list = []
                if settlement.discount_name:
                    discount_names_list = [n.strip() for n in settlement.discount_name.split(',') if n.strip()]
                if settlement.discount_id:
                    discount_ids_list = [id.strip() for id in settlement.discount_id.split(',') if id.strip()]
                
                # Processed discount names and IDs
                
                # Parse discount type prefixes (format: type:name)
                names_by_type = {}
                ids_by_type = {}
                
                for name_entry in discount_names_list:
                    if ':' in name_entry:
                        dtype, name = name_entry.split(':', 1)
                        if dtype not in names_by_type:
                            names_by_type[dtype] = []
                        names_by_type[dtype].append(name)
                        # Parsed name with type
                    else:
                        # Fallback for old format without type prefix
                        if 'unknown' not in names_by_type:
                            names_by_type['unknown'] = []
                        names_by_type['unknown'].append(name_entry)
                        # Old format name
                
                for id_entry in discount_ids_list:
                    if ':' in id_entry:
                        dtype, id_val = id_entry.split(':', 1)
                        if dtype not in ids_by_type:
                            ids_by_type[dtype] = []
                        ids_by_type[dtype].append(id_val)
                        # Parsed ID with type
                    else:
                        # Fallback for old format without type prefix
                        if 'unknown' not in ids_by_type:
                            ids_by_type['unknown'] = []
                        ids_by_type['unknown'].append(id_entry)
                
                # Organized names and IDs by type
                
                # For mixed discounts with 2 pax (1 senior + 1 pwd for example)
                # We need to divide the total VAT and discount by number of people of that type
                # Total pax = 2, so each gets half
                total_pax = getattr(settlement, 'discount_quantity', 1) or 1
                # Total pax calculation
                
                # Display each discount type's VAT and discount breakdown
                # For mixed discounts, we need to calculate VAT properly for each type
                
                # Get the total amounts from settlement
                total_tax_exempt = getattr(settlement, 'tax_exempt_amount', 0) or 0
                total_discount = getattr(settlement, 'discount_amount', 0) or 0
                settlement_final_total = getattr(settlement, 'final_total', None)
                total_final = settlement_final_total if settlement_final_total is not None else order.total
                
                # For mixed discounts, calculate VAT and discount properly based on frontend logic
                items_total = sum([item.price * item.quantity for item in order.items])
                # Calculate covers from products (for discount calculation ONLY)
                # DO NOT use settlement.total_no_pax for calculations as it's for display only
                calc_covers = 0
                for calc_item in order.items:
                    calc_no_pax = 1
                    if calc_item.product and hasattr(calc_item.product, 'no_pax') and calc_item.product.no_pax is not None:
                        calc_no_pax = calc_item.product.no_pax
                    if calc_no_pax > 1:
                        calc_covers += calc_item.quantity * calc_no_pax
                if calc_covers == 0:
                    calc_covers = 1
                total_pax = calc_covers  # Use calculated covers for discount computation
                share_per_person = items_total / total_pax if total_pax > 0 else 0
                
                # Handle regular discount separately if present
                has_regular_discount = 'regular' in discount_types
                regular_discount_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                
                # Get discount quantities for each type
                # For mixed discounts, we assume 1 of each type for calculation purposes
                # This matches the frontend logic where each discount type applies to one person's share
                discount_quantities = {}
                for dt in discount_types:
                    discount_quantities[dt] = 1
                
                if has_regular_discount:
                    # Show regular discount line
                    # Get regular discount quantity (default to 1)
                    regular_qty = discount_quantities.get('regular', 1)
                    regular_holder_portion = share_per_person * regular_qty
                    regular_discount_amount = regular_holder_portion * (regular_discount_percent / 100)
                    p.text(format_line(f"LESS: {regular_discount_percent}% DISCOUNT (regular):", regular_discount_amount))
                
                # Process each discount type
                for idx, discount_type in enumerate(discount_types):
                    if discount_type == 'regular':
                        # Skip regular discount as it's already handled above
                        continue
                    
                    # Display with discount type label
                    if discount_type == 'senior':
                        label_suffix = 'SC'
                    elif discount_type == 'pwd':
                        label_suffix = 'PWD'
                    elif discount_type == 'solo_parent':
                        label_suffix = 'SOLO'
                    elif discount_type == 'athlete':
                        label_suffix = 'NAAC'
                    elif discount_type == 'medal_of_valor':
                        label_suffix = 'MOV'
                    else:
                        label_suffix = discount_type.upper()
                    
                    # Get discount quantity for this type (default to 1)
                    discount_qty = discount_quantities.get(discount_type, 1)
                    
                    # Calculate VAT and discount amounts for this discount type
                    # Following exact frontend logic from settlement.html lines 1538-1562
                    discount_holder_portion = share_per_person * discount_qty
                    
                    if discount_type in ['senior', 'pwd', 'solo_parent']:
                        # VAT-exempt discounts (SC, PWD, SP)
                        # Calculate VAT exemption (VAT that would have been paid)
                        # vatRemoved = discountHolderPortion * (12 / 112)
                        vat_amount = discount_holder_portion * (12 / 112)
                        # Calculate discount (20% for SC/PWD, 10% for SP)
                        discount_percent = 10 if discount_type == 'solo_parent' else 20
                        # discountAmount = vatExemptBase * (discountPercent / 100) where vatExemptBase = discountHolderPortion / 1.12
                        vat_exempt_base = discount_holder_portion / 1.12
                        discount_amount = vat_exempt_base * (discount_percent / 100)
                        
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: {discount_percent}% DISCOUNT ({label_suffix}):", discount_amount))
                    
                    elif discount_type in ['athlete', 'medal_of_valor']:
                        # VAT-applicable discounts (NAAC, MOV)
                        # Calculate VAT exemption (VAT that would have been paid)
                        # vatRemoved = discountHolderPortion * (12 / 112)
                        vat_amount = discount_holder_portion * (12 / 112)
                        # Calculate discount (20% for NAAC/MOV)
                        # discountAmount = vatExemptBase * 0.20 where vatExemptBase = discountHolderPortion / 1.12
                        vat_exempt_base = discount_holder_portion / 1.12
                        discount_amount = vat_exempt_base * 0.20
                        
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: 20% DISCOUNT ({label_suffix}):", discount_amount))
                        # Add VAT back for NAAC/MOV
                        p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount))
                
                settlement_final_total = getattr(settlement, 'final_total', None)
                final_total = settlement_final_total if settlement_final_total is not None else order.total
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", final_total)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - (final_total if final_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
            elif settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type in ['senior', 'pwd', 'solo_parent']:
                p.text(format_line("TOTAL:", items_total))
                tax_exempt_amount = getattr(settlement, 'tax_exempt_amount', 0) or 0
                p.text(format_line("LESS: 12% VAT:", tax_exempt_amount))
                # Use discount_amount for all discounts instead of senior_discount
                discount_amount = getattr(settlement, 'discount_amount', 0) or 0
                
                # Use specific discount labels based on discount type with rate
                if settlement.order_discount_type == 'senior':
                    discount_label = "Less Discount: SC 20%"
                elif settlement.order_discount_type == 'pwd':
                    discount_label = "Less Discount: PWD 20%"
                elif settlement.order_discount_type == 'solo_parent':
                    discount_label = "Less Discount: SOLO 10%"
                else:
                    discount_label = "Less Discount: SC/PWD/SOLO 20%"
                
                p.text(format_line(discount_label, discount_amount))
                settlement_final_total = getattr(settlement, 'final_total', None)
                final_total = settlement_final_total if settlement_final_total is not None else order.total
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", final_total)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - (final_total if final_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
            elif settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type in ['athlete', 'medal_of_valor']:
                # National Athlete (NAAC) / Medal of Valor (MOV) format - NO tax exempt
                # Display shows full TOTAL but discount applies to athlete/MOV portion only
                discount_quantity = getattr(settlement, 'discount_quantity', 0) or 0
                
                # For display: use FULL order total
                lessVat = items_total * (12 / 112)
                totalAmountNoVat = items_total - lessVat
                
                p.text(format_line("TOTAL:", items_total))
                p.text(format_line("LESS: 12% VAT:", lessVat))
                p.text(format_line("Total Amount:", totalAmountNoVat))
                
                # Discount amount is calculated from athlete/MOV portion only but stored in settlement
                discount_amount = getattr(settlement, 'discount_amount', 0) or 0
                if settlement.order_discount_type == 'athlete':
                    p.text(format_line("Less Discount: NAAC 20%:", discount_amount))
                else:
                    p.text(format_line("Less Discount: MOV 20%:", discount_amount))
                
                # ADD VAT is the same as LESS VAT
                addVat = lessVat
                p.text(format_line("ADD: 12% VAT:", addVat))
                
                settlement_final_total = getattr(settlement, 'final_total', None)
                final_total = settlement_final_total if settlement_final_total is not None else order.total
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", final_total)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - (final_total if final_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
            elif settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type in ['regular', 'oth']:
                p.text(format_line("TOTAL:", items_total))
                discount_amount = getattr(settlement, 'discount_amount', 0) or 0
                if settlement.order_discount_type == 'regular':
                    regular_discount_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                    p.text(format_line(f"Less Discount: REG {regular_discount_percent}%", discount_amount))
                else:
                    p.text(format_line("LESS: OTH DISCOUNT:", discount_amount))
                settlement_final_total = getattr(settlement, 'final_total', None)
                final_total = settlement_final_total if settlement_final_total is not None else order.total
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", final_total)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - (final_total if final_total is not None else order.total)
                    p.text(format_line("CHANGE:", change))
            else:
                p.text(format_line("TOTAL:", items_total))
                _print_emphasized_amount_line(p, "TOTAL AMT DUE:", order.total)
                
                # Display payment methods - handle split payments
                if settlement:
                    cash_amt = getattr(settlement, 'cash_received', 0) or 0
                    gift_check_amt = getattr(settlement, 'gift_check_amount', 0) or 0
                    cheque_amt = getattr(settlement, 'cheque_amount', 0) or 0
                    
                    # Check if it's a split payment
                    is_split = (cash_amt > 0 and (gift_check_amt > 0 or cheque_amt > 0))
                    
                    if is_split:
                        p.text("Payment Method: Split Payment\n")
                        if cash_amt > 0:
                            p.text(format_line("Cash:", cash_amt))
                        if gift_check_amt > 0:
                            p.text(format_line("Gift Check:", gift_check_amt))
                        if cheque_amt > 0:
                            p.text(format_line("Cheque:", cheque_amt))
                    else:
                        # Single payment method
                        if settlement.payment_method == 'cash':
                            pm = 'Cash'
                        elif settlement.payment_method == 'card':
                            card_type = settlement.card_type.upper() if settlement.card_type else 'CARD'
                            pm = f'Credit ({card_type})'
                        elif settlement.payment_method == 'gift_check':
                            pm = 'Gift Check'
                        elif settlement.payment_method == 'cheque':
                            pm = 'Cheque'
                        else:
                            pm = settlement.payment_method.capitalize()
                        p.text(f"Payment Method: {pm}\n")
                    
                    total_received = cash_amt + gift_check_amt + cheque_amt
                    p.text(format_line("AMOUNT RECEIVED:", total_received))
                    change = total_received - order.total
                    p.text(format_line("CHANGE:", change))

            p.text(_receipt_separator())

            # === Transaction Details (using accurate values from settlement) ===
            if settlement:
                    
                if settlement.vat_sales is not None:
                    vat_sales_value = settlement.vat_sales
                else:
                    vat_sales_value = order.subtotal
                p.text(format_line("VAT - Sales:", vat_sales_value))

                # Check if vat_exempt_sale is explicitly set to 0 or has a value
                if settlement.vat_exempt_sale is not None:
                    vat_exempt_sale_value = settlement.vat_exempt_sale
                else:
                    vat_exempt_sale_value = 0.00
                p.text(format_line("VAT - Exempt Sale:", vat_exempt_sale_value))

                # Check if vat_amount is explicitly set to 0 or has a value
                if settlement.vat_amount is not None:
                    vat_amount_value = settlement.vat_amount
                else:
                    vat_amount_value = order.vat
                p.text(format_line("Vat Amount:", vat_amount_value))
                
                # Check if zero_rated_sales is explicitly set to 0 or has a value
                if settlement.zero_rated_sales is not None:
                    zero_rated_sales_value = settlement.zero_rated_sales
                else:
                    zero_rated_sales_value = 0.00
                p.text(format_line("Zero Rated Sales:", zero_rated_sales_value))
                
                # Check if total_sale is explicitly set or use fallback
                if settlement.total_sale is not None:
                    total_sale_value = settlement.total_sale
                else:
                    total_sale_value = settlement.final_total if settlement.final_total is not None else order.total
                # p.text(format_line("Total Sale:", total_sale_value))  # Hidden per user request
            else:
                # Fallback to order values if no settlement
                p.text(format_line("VAT - Sales:", order.subtotal))
                p.text(format_line("VAT - Exempt Sale:", 0.00))
                p.text(format_line("Zero Rated Sales:", 0.00))
                # p.text(format_line("Total Sale:", order.total))  # Hidden per user request
                p.text(format_line("Vat Amount:", order.vat))

            p.text(_receipt_separator())

            # === Customer Information ===
            if order.customer_name:
                # Print customer name with label, then underline to fill the rest of the line
                label = "Customer Name: "
                remaining_space = _receipt_width() - len(label) - len(order.customer_name)
                p.text(label)
                p._raw(b'\x1b-\x01')  # Enable single underline
                p.text(order.customer_name)
                if remaining_space > 0:
                    p.text("_" * remaining_space)
                p._raw(b'\x1b-\x00')  # Disable underline
                p.text("\n")
                
                # Print address with label, then underline to fill the rest of the line
                address = "Tacloban City"
                label = "Address: "
                remaining_space = _receipt_width() - len(label) - len(address)
                p.text(label)
                p._raw(b'\x1b-\x01')  # Enable single underline
                p.text(address)
                if remaining_space > 0:
                    p.text("_" * remaining_space)
                p._raw(b'\x1b-\x00')  # Disable underline
                p.text("\n")
            else:
                p.text("Customer Name: ____________________\n")
                p.text("Address: __________________________\n")
            
            # TIN with full-width underline
            label = "TIN: "
            remaining_space = 40 - len(label)
            p.text(label)
            p._raw(b'\x1b-\x01')  # Enable single underline
            p.text("_" * remaining_space)
            p._raw(b'\x1b-\x00')  # Disable underline
            p.text("\n")

            # === Senior/PWD/Solo Parent/Athlete/MOV Info ===
            if settlement and settlement.order_discount_type and settlement.discount_name:
                # Handle mixed discounts (comma-separated) and single discounts
                if ',' in settlement.order_discount_type:
                    # Mixed discount types - parse names and IDs with type prefixes
                    names_by_type = {}
                    ids_by_type = {}
                    
                    # Parse names
                    if settlement.discount_name:
                        discount_names_list = [n.strip() for n in settlement.discount_name.split(',') if n.strip()]
                        for name_entry in discount_names_list:
                            if ':' in name_entry:
                                dtype, name = name_entry.split(':', 1)
                                if dtype not in names_by_type:
                                    names_by_type[dtype] = []
                                names_by_type[dtype].append(name)
                    
                    # Parse IDs
                    if settlement.discount_id:
                        discount_ids_list = [i.strip() for i in settlement.discount_id.split(',') if i.strip()]
                        for id_entry in discount_ids_list:
                            if ':' in id_entry:
                                dtype, id_val = id_entry.split(':', 1)
                                if dtype not in ids_by_type:
                                    ids_by_type[dtype] = []
                                ids_by_type[dtype].append(id_val)
                    
                    # Display beneficiaries in their respective sections for mixed discounts
                    # OSCA/PWD section (senior and pwd)
                    first_entry = True
                    for discount_type in ['senior', 'pwd']:
                        type_names = names_by_type.get(discount_type, [])
                        type_ids = ids_by_type.get(discount_type, [])
                        
                        for i in range(len(type_names)):
                            name = type_names[i] if i < len(type_names) else f"{discount_type.capitalize()} {i+1}"
                            person_id = type_ids[i] if i < len(type_ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "OSCA/PWD: "
                                # Print label without underline
                                p.text(label)
                                first_entry = False
                            else:
                                # For subsequent entries, just add spaces to align
                                label = "         "  # 9 spaces to align with "OSCA/PWD: " 
                                p.text(label)
                            
                            # Enable underline for person info and remaining space
                            p._raw(b'\x1b-\x01')  # Enable single underline
                            # Print person info (will be underlined)
                            p.text(person_info)
                            # Calculate and print remaining spaces (will also be underlined)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')  # Disable underline
                            p.text("\n")
                    
                    # SOLO section
                    first_solo_parent = True
                    solo_parent_names = names_by_type.get('solo_parent', [])
                    solo_parent_ids = ids_by_type.get('solo_parent', [])
                    
                    for i in range(len(solo_parent_names)):
                        name = solo_parent_names[i] if i < len(solo_parent_names) else f"SOLO {i+1}"
                        person_id = solo_parent_ids[i] if i < len(solo_parent_ids) else ""
                        
                        if person_id:
                            person_info = f"{name} (ID: {person_id})"
                        else:
                            person_info = f"{name}"
                        
                        if first_solo_parent:
                            label = "SOLO: "
                            # Print label without underline
                            p.text(label)
                            first_solo_parent = False
                        else:
                            # For subsequent entries, just add spaces to align
                            label = "      "  # 6 spaces to align with "SOLO: "
                            p.text(label)
                        
                        # Enable underline for person info and remaining space
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        # Print person info (will be underlined)
                        p.text(person_info)
                        # Calculate and print remaining spaces (will also be underlined)
                        remaining_space = _receipt_width() - len(label) - len(person_info)
                        if remaining_space > 0:
                            p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                    
                    # NAAC/MOV section
                    first_naac_mov = True
                    for discount_type in ['athlete', 'medal_of_valor']:
                        type_names = names_by_type.get(discount_type, [])
                        type_ids = ids_by_type.get(discount_type, [])
                        
                        for i in range(len(type_names)):
                            name = type_names[i] if i < len(type_names) else f"{discount_type.capitalize()} {i+1}"
                            person_id = type_ids[i] if i < len(type_ids) else ""
                            
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_naac_mov:
                                label = "NAAC/MOV: "
                                # Print label without underline
                                p.text(label)
                                first_naac_mov = False
                            else:
                                # For subsequent entries, just add spaces to align
                                label = "          "  # 10 spaces to align with "NAAC/MOV: "
                                p.text(label)
                            
                            # Enable underline for person info and remaining space
                            p._raw(b'\x1b-\x01')  # Enable single underline
                            # Print person info (will be underlined)
                            p.text(person_info)
                            # Calculate and print remaining spaces (will also be underlined)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')  # Disable underline
                            p.text("\n")
                    
                    # Show remaining placeholders
                    # Show SOLO placeholder if no solo parent in mixed discount
                    if 'solo_parent' not in names_by_type:
                        label = "SOLO: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                    
                    # Show NAAC/MOV placeholder if no athlete/MOV in mixed discount
                    has_naac_mov = 'athlete' in names_by_type or 'medal_of_valor' in names_by_type
                    if not has_naac_mov:
                        label = "NAAC/MOV: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                elif settlement.order_discount_type in ['senior', 'pwd', 'solo_parent', 'athlete', 'medal_of_valor']:
                    # Single discount type
                    names = []
                    ids = []
                    if isinstance(settlement.discount_name, str):
                        names = [n.strip() for n in settlement.discount_name.split(',') if n.strip()]
                    elif isinstance(settlement.discount_name, list):
                        names = settlement.discount_name
                    if hasattr(settlement, 'discount_id') and settlement.discount_id:
                        if isinstance(settlement.discount_id, str):
                            ids = [i.strip() for i in settlement.discount_id.split(',') if i.strip()]
                        elif isinstance(settlement.discount_id, list):
                            ids = settlement.discount_id
                    
                    # For senior/PWD, display on OSCA/PWD line with underline
                    if settlement.order_discount_type in ['senior', 'pwd']:
                        first_entry = True
                        for i in range(settlement.discount_quantity or len(names)):
                            name = names[i] if i < len(names) else f"{settlement.order_discount_type.capitalize()} {i+1}"
                            person_id = ids[i] if i < len(ids) else ""
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "OSCA/PWD: "
                                # Print label without underline
                                p.text(label)
                                first_entry = False
                            else:
                                # For subsequent entries, just add spaces to align
                                label = "         "  # 9 spaces to align with "OSCA/PWD: " 
                                p.text(label)
                            
                            # Enable underline for person info and remaining space
                            p._raw(b'\x1b-\x01')  # Enable single underline
                            # Print person info (will be underlined)
                            p.text(person_info)
                            # Calculate and print remaining spaces (will also be underlined)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')  # Disable underline
                            p.text("\n")
                        
                        # Show SOLO placeholder
                        label = "SOLO: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                        
                        # Show NAAC/MOV placeholder
                        label = "NAAC/MOV: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                    elif settlement.order_discount_type == 'solo_parent':
                        # Show OSCA/PWD placeholder first
                        label = "OSCA/PWD: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                        
                        # For solo parent, display on SOLO line with underline
                        first_entry = True
                        for i in range(settlement.discount_quantity or len(names)):
                            name = names[i] if i < len(names) else f"SOLO {i+1}"
                            person_id = ids[i] if i < len(ids) else ""
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                # Show solo parent on SOLO line
                                label = "SOLO: "
                                # Print label without underline
                                p.text(label)
                                first_entry = False
                            else:
                                # For subsequent entries, just add spaces to align
                                label = "      "  # 6 spaces to align with "SOLO: "
                                p.text(label)
                            
                            # Enable underline for person info and remaining space
                            p._raw(b'\x1b-\x01')  # Enable single underline
                            # Print person info (will be underlined)
                            p.text(person_info)
                            # Calculate and print remaining spaces (will also be underlined)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')  # Disable underline
                            p.text("\n")
                        
                        # Show NAAC/MOV placeholder
                        label = "NAAC/MOV: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                    elif settlement.order_discount_type in ['athlete', 'medal_of_valor']:
                        # Show OSCA/PWD placeholder first
                        label = "OSCA/PWD: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                        
                        # Show SOLO placeholder
                        label = "SOLO: "
                        remaining_space = _receipt_width() - len(label)
                        p.text(label)
                        p._raw(b'\x1b-\x01')  # Enable single underline
                        p.text(" " * remaining_space)
                        p._raw(b'\x1b-\x00')  # Disable underline
                        p.text("\n")
                        
                        # Display athlete/MOV name and ID on NAAC/MOV line, everything underlined
                        first_entry = True
                        for i in range(settlement.discount_quantity or len(names)):
                            name = names[i] if i < len(names) else (f"Athlete {i+1}" if settlement.order_discount_type == 'athlete' else f"MOV Awardee {i+1}")
                            person_id = ids[i] if i < len(ids) else ""
                            if person_id:
                                person_info = f"{name} (ID: {person_id})"
                            else:
                                person_info = f"{name}"
                            
                            if first_entry:
                                label = "NAAC/MOV: "
                                # Print label without underline
                                p.text(label)
                                first_entry = False
                            else:
                                # For subsequent entries, just add spaces to align
                                label = "          "  # 10 spaces to align with "NAAC/MOV: "
                                p.text(label)
                            
                            # Enable underline for person info and remaining space
                            p._raw(b'\x1b-\x01')  # Enable single underline
                            # Print person info (will be underlined)
                            p.text(person_info)
                            # Calculate and print remaining spaces (will also be underlined)
                            remaining_space = _receipt_width() - len(label) - len(person_info)
                            if remaining_space > 0:
                                p.text(" " * remaining_space)
                            p._raw(b'\x1b-\x00')  # Disable underline
                            p.text("\n")
            else:
                # Display placeholders if no discount
                label = "OSCA/PWD: "
                remaining_space = _receipt_width() - len(label)
                p.text(label)
                p._raw(b'\x1b-\x01')  # Enable single underline
                p.text(" " * remaining_space)
                p._raw(b'\x1b-\x00')  # Disable underline
                p.text("\n")
                
                label = "SOLO: "
                remaining_space = _receipt_width() - len(label)
                p.text(label)
                p._raw(b'\x1b-\x01')  # Enable single underline
                p.text(" " * remaining_space)
                p._raw(b'\x1b-\x00')  # Disable underline
                p.text("\n")
                
                label = "NAAC/MOV: "
                remaining_space = _receipt_width() - len(label)
                p.text(label)
                p._raw(b'\x1b-\x01')  # Enable single underline
                p.text(" " * remaining_space)
                p._raw(b'\x1b-\x00')  # Disable underline
                p.text("\n")
            
            # Add signature placeholder with full-width underline
            label = "Signature: "
            remaining_space = 40 - len(label)
            p.text(label)
            p._raw(b'\x1b-\x01')  # Enable single underline
            p.text(" " * remaining_space)  # Use spaces for underline
            p._raw(b'\x1b-\x00')  # Disable underline
            p.text("\n")
            
            # Add remarks if present (only shown if settlement has manual SI/CI number)
            if settlement and hasattr(settlement, 'manual_si_number') and settlement.manual_si_number:
                p.text("\n")
                p.text("Remarks:\n")
                # Format: "Manual SI {number} has been issued separately"
                remarks_text = f"Manual SI {settlement.manual_si_number} has been issued separately"
                # Word wrap remarks to fit the configured receipt width
                words = remarks_text.split()
                current_line = ""
                for word in words:
                    if len(current_line) + len(word) + 1 <= _receipt_width():
                        current_line += (" " if current_line else "") + word
                    else:
                        if current_line:
                            p.text(f"{current_line}\n")
                        current_line = word
                if current_line:
                    p.text(f"{current_line}\n")

            # === Card Swipe Info (for Credit/Debit Card) ===
            if settlement and settlement.payment_method == 'card' and settlement.card_swipe_json:
                try:
                    import json
                    card_info = json.loads(settlement.card_swipe_json)
                    if card_info and isinstance(card_info, dict):
                        p.text("\n")
                        p.text(_receipt_separator())
                        p.text("Card Payment Details\n")
                        p.text(_receipt_separator())
                        card_type = card_info.get('card_type', 'CARD')
                        p.text(f"Card Type: {card_type}\n")
                        card_number = card_info.get('number', '')
                        if card_number and len(card_number) >= 8:
                            masked = card_number[:4] + ' **** **** ' + card_number[-4:]
                            p.text(f"Card No.: {masked}\n")
                        elif card_number:
                            p.text(f"Card No.: {card_number}\n")
                        expiry = card_info.get('expiry', '')
                        if expiry and len(expiry) == 4:
                            p.text(f"Expiry: {expiry[2:4]}/{expiry[0:2]}\n")
                        elif expiry:
                            p.text(f"Expiry: {expiry}\n")
                except Exception:
                    pass

            # === Thank You Message ===
            thank_you = get_receipt_thank_you_message()
            if thank_you:
                p.set(align='center')
                p.text(f"{thank_you}\n")
                p.text("\n")

            # === Footer ===
            p.set(align='center')
            p.text("\n")
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32') 
            for line in get_receipt_footer():
                p.text(f"{line}\n")
            _set_body_line_spacing(p)     
            p.text("\n")

            p.cut()

        # === Print receipt ===
        print("[DEBUG] Printing Official Receipt...")
        print_receipt_copy("OFFICIAL SALES INVOICE")

        print("[DEBUG] Receipt printed successfully.")
        return True
    except Exception as e:
        print(f"[ERROR] Official receipt printing failed: {e}")
        return False

def print_order_receipt(order):
    """
    Print order receipt for the given order.

    Args:
        order (Order): The order object to print

    Returns:
        bool: True if printing was successful, False otherwise
    """
    if getattr(order, "status", None) != "pending":
        print(f"[INFO] Order slip skipped for non-pending order {getattr(order, 'order_no', '')} (status={getattr(order, 'status', '')})")
        return False

    # Skip printing in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping actual print operation")
        return True
    
    # Always print cashier copy to main printer.
    cashier_success = print_receipt(order, ReceiptType.CASHIER)

    # Kitchen copy only prints when a kitchen printer is configured.
    kitchen_success = True
    if _has_kitchen_printer():
        kitchen_success = print_receipt(order, ReceiptType.KITCHEN)
    else:
        print("[INFO] Kitchen printer not configured. Kitchen copy skipped.")

    return cashier_success and kitchen_success


def print_kitchen_order_slip(order):
    """Reprint kitchen order slip only."""
    if getattr(order, "status", None) != "pending":
        print(f"[INFO] Kitchen order slip skipped for non-pending order {getattr(order, 'order_no', '')} (status={getattr(order, 'status', '')})")
        return False

    if not _has_kitchen_printer():
        print("[INFO] Kitchen printer not configured. Kitchen order slip skipped.")
        return False
    return print_receipt(order, ReceiptType.KITCHEN)


def print_add_items_slip(order, new_items):
    """
    Print a kitchen-only add-on slip showing only the newly added items.

    Args:
        order (Order): The existing order object (for header info)
        new_items (list): List of dicts with keys: product_name, quantity, price, modifier

    Returns:
        bool: True if printing was successful, False otherwise
    """
    if getattr(order, "status", None) != "pending":
        print(f"[INFO] Add-on kitchen slip skipped for non-pending order {getattr(order, 'order_no', '')} (status={getattr(order, 'status', '')})")
        return False

    if not new_items:
        print(f"[INFO] Add-on kitchen slip skipped because no new items were provided for order {getattr(order, 'order_no', '')}")
        return False

    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping add-on slip printing")
        return False

    if not _has_kitchen_printer():
        print("[INFO] Kitchen printer IP is not configured. Skipping add-on slip.")
        return False

    try:
        with _kitchen_printer_manager.get_connection() as p:
            if p is None:
                return False

            _set_kitchen_text_mode(p)

            # Header
            p.set(align="center", bold=True)
            p._raw(b'\x1d\x21\x01')
            p.text("ADD-ON ORDER SLIP\n")
            p._raw(b'\x1d\x21\x00')
            p.set(bold=False)

            # Order info
            p.set(align="left")
            p.text(f"Order No: {order.order_no}\n")
            from datetime import datetime
            p.text(f"Date: {datetime.now().strftime('%m/%d/%Y %I:%M %p')}\n")
            if order.customer_name:
                p.text(f"Customer: {order.customer_name}\n")
            _print_kitchen_order_type_and_tables(p, order)

            # New items
            _set_kitchen_text_mode(p)
            _print_kitchen_separator(p)
            p.set(align="center", bold=True)
            p._raw(b'\x1d\x21\x11')   # Double width + double height
            p.text("ADDITIONAL ITEMS\n")
            p._raw(b'\x1d\x21\x00')
            p.set(align="left", bold=True)

            added_total = 0.0
            kitchen_item_width = _receipt_width() // 2
            for item in new_items:
                item_text = f"{format_qty(item['quantity'])} x {item['product_name']}"
                if item.get('modifier'):
                    item_text += f" - {item['modifier']}"
                _print_wrapped_item_line(p, item_text, kitchen_item_width, b'\x1d\x21\x11')
                _set_kitchen_text_mode(p)
                added_total += float(item['price']) * float(item['quantity'])

            p.set(bold=False)
            _print_kitchen_separator(p)
            p.text(f"ADD-ON TOTAL: PHP {added_total:,.2f}\n")

            _finish_order_slip(p, "*** FOR KITCHEN ***")
            return True
    except Exception as e:
        print(f"[ERROR] Add-on slip printing failed: {e}")
        return False


def print_bill_receipt(order, bill_data=None):
    """Print a bill receipt for an order with optional detailed bill data"""
    return print_receipt(order, ReceiptType.BILL, bill_data=bill_data)


def print_z_reading(z_reading_data, report_date, is_reprint=False):
    """Print Z-reading report on dot matrix printer"""
    return print_receipt(None, ReceiptType.Z_READING, z_reading_data=z_reading_data, report_date=report_date, is_reprint=is_reprint)


def print_x_reading(xreading_data, report_date, x_count=1):
    """Print X-reading (not finalized) report on dot matrix printer
    
    X-reading is just Z-reading that's not finalized yet. It uses the same format,
    data, and calculations as Z-reading, but with X-READING header and X counter.
    
    Args:
        xreading_data: Dictionary containing X-reading data (same format as Z-reading)
        report_date: Date of the X-reading report
        x_count: Current X-reading count (number of times printed)
        
    Returns:
        bool: True if printing was successful, False otherwise
    """
    # Reuse Z-reading print logic with X_READING type
    return print_receipt(None, ReceiptType.X_READING, z_reading_data=xreading_data, report_date=report_date, x_count=x_count)


def print_official_receipt(order, settlement=None, is_reprint=False):
    """Print an official receipt for an order with settlement details"""
    # Skip printing in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping official receipt print")
        return True
    
    return print_receipt(order, ReceiptType.OFFICIAL, settlement=settlement, is_reprint=is_reprint)


def _render_cancelled_items_slip(p, order, cancelled_items, reason, copy_label):
    is_kitchen_copy = str(copy_label).upper() == "KITCHEN"
    if is_kitchen_copy:
        _set_kitchen_text_mode(p)
        item_width = _receipt_width() // 2   # items print double-width
        item_size = b'\x1d\x21\x11'
        footer = "*** FOR KITCHEN ***"
    else:
        _set_standard_receipt_text(p)
        item_width = _receipt_width()
        item_size = None
        footer = "*** FOR CASHIER ***"

    def print_separator():
        if is_kitchen_copy:
            _print_kitchen_separator(p)
        else:
            p.text(_receipt_separator())

    # Header - CANCELLED style
    p.set(align="center", bold=True)
    p._raw(b'\x1d\x21\x01')
    p.text("CANCELLED ORDER SLIP\n")
    p._raw(b'\x1d\x21\x00')
    p.text(f"{str(copy_label).upper()} COPY\n")
    p.set(bold=False)

    # Order info
    p.set(align="left")
    p.text(f"Order No: {order.order_no}\n")
    from datetime import datetime
    p.text(f"Date: {datetime.now().strftime('%m/%d/%Y %I:%M %p')}\n")
    if order.customer_name:
        p.text(f"Customer: {order.customer_name}\n")
    if is_kitchen_copy:
        _print_kitchen_order_type_and_tables(p, order)
    else:
        _print_receipt_order_type_and_table(p, order, "Table(s):")
    p.text(f"Reason: {reason}\n")

    # Cancelled items
    if is_kitchen_copy:
        _set_kitchen_text_mode(p)
    print_separator()
    p.set(align="center", bold=True)
    p._raw(b'\x1d\x21\x11')
    p.text("CANCELLED ITEMS\n")
    p._raw(b'\x1d\x21\x00')
    p.set(align="left", bold=True)

    cancelled_total = 0.0
    for item in cancelled_items:
        item_text = f"{format_qty(item.modified_qty)} x {item.product_name}"
        if item.reason:
            item_text += f" ({item.reason})"
        _print_wrapped_item_line(p, item_text, item_width, item_size)
        if is_kitchen_copy:
            _set_kitchen_text_mode(p)
        cancelled_total += float(item.price) * float(item.modified_qty)

    p.set(bold=False)
    print_separator()
    p.text(f"CANCELLED TOTAL: PHP {cancelled_total:,.2f}\n")
    _finish_order_slip(p, footer)


def print_cancelled_items_slip(order, cancelled_items, reason):
    """
    Print a kitchen-style cancelled items slip showing the cancelled items.
    Similar to add-on slip format but for cancelled items.

    Args:
        order (Order): The existing order object (for header info)
        cancelled_items (list): List of OrderAuditLog objects for cancelled items
        reason (str): The cancellation reason

    Returns:
        bool: True if printing was successful, False otherwise
    """
    # Skip printing in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping cancelled items slip print")
        return True
    
    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping cancelled items slip printing")
        return False

    cashier_success = False
    kitchen_success = True

    try:
        if not _has_cashier_printer():
            print("[INFO] Cashier printer is not configured. Skipping cashier cancelled-items slip.")
        else:
            with _cashier_printer_manager.get_connection() as p:
                if p is None:
                    return False
                _render_cancelled_items_slip(p, order, cancelled_items, reason, "CASHIER")
                cashier_success = True
    except Exception as e:
        print(f"[ERROR] Cashier cancelled-items slip printing failed: {e}")

    try:
        if not _has_kitchen_printer():
            print("[INFO] Kitchen printer IP is not configured. Skipping kitchen cancelled-items slip.")
        else:
            with _kitchen_printer_manager.get_connection() as p:
                if p is None:
                    kitchen_success = False
                else:
                    _render_cancelled_items_slip(p, order, cancelled_items, reason, "KITCHEN")
                    kitchen_success = True
    except Exception as e:
        print(f"[ERROR] Kitchen cancelled-items slip printing failed: {e}")
        kitchen_success = False

    return cashier_success and kitchen_success


def _render_cancel_order_slip(p, order, modified_items, copy_label):
    is_kitchen_copy = str(copy_label).upper() == "KITCHEN"
    if is_kitchen_copy:
        _set_kitchen_text_mode(p)
    else:
        _set_standard_receipt_text(p)

    def print_separator():
        if is_kitchen_copy:
            _print_kitchen_separator(p)
        else:
            p.text(_receipt_separator())

    # === Header ===
    p._raw(b'\x12')
    p._raw(b'\x1b\x32')
    p.set(align='center', bold=True)
    for line in get_receipt_header():
        p.text(f"{line}\n")
    _set_body_line_spacing(p)

    # === Separator ===
    print_separator()
    p.set(align='center')
    p.text("***CANCELLED ORDER**\n")
    p.text(f"***{copy_label} COPY***\n")
    print_separator()
    p.set(bold=False)

    # === Order Info ===
    p.set(align='left')
    order_date = order.timestamp.strftime('%b %d, %Y (%a)')
    order_time = order.timestamp.strftime('%I:%M %p')
    
    # Get the first voided item to get the reference number
    cancel_ref = ""
    if modified_items and len(modified_items) > 0:
        cancel_ref = modified_items[0].reference_no or f"CAN-{order.id:010d}"
    
    # Get order number
    order_no = order.order_no if order.order_no else f"ORDER-{order.id:010d}"
    
    p.text(f"{order_date}\n")
    p.text(f"REF ORDER NO: {order_no}\n")
    p.text(f"CANCEL REF#: {cancel_ref}\n")
    p.text(f"ORDER TIME: {order_time}\n")
    
    # Get reason from voided items (assuming all have the same reason)
    reason = "Order cancelled"
    if modified_items and len(modified_items) > 0 and modified_items[0].reason:
        reason = modified_items[0].reason
        
    p.text(f"REASON: {reason}\n")
    
    # Cashier info
    p.text("\n")
    cashier = getattr(current_user, 'username', 'Cashier')
    p.text(f"Cashier: {cashier}\n")
    print_terminal_metadata(p)
    
    # === Separator ===
    print_separator()
    
    # === Items ===
    total_amount = 0
    item_name_width = _receipt_width()
    for item in order.items:
        qty = format_qty(item.quantity)
        price = item.price
        amount = item.price * item.quantity
        total_amount += amount

        item_text = f"{qty} x {item.product_name}"
        _print_wrapped_item_line(p, item_text, item_name_width)
        p.text(f"  Price: {price:,.2f}  Amount: {amount:,.2f}\n")
    
    # === Separator ===
    print_separator()
    
    # === Totals ===
    p.text(format_line("TOTAL:", total_amount))
    print_separator()
    
    # === Footer Message ===
    p.set(align='center')
    p.text("NO SALES INVOICE ISSUED\n")
    print_separator()
    
    # Printed time
    printed_time = datetime.now()
    printed_date = printed_time.strftime('%b %d, %Y')
    printed_time_str = printed_time.strftime('%I:%M %p')
    p.text(f"Printed: {printed_date}  {printed_time_str}\n")
    
    p.text("\n")

    # Additional information
    p.text("This slip serves as system record for\n")
    p.text("the cancellation of an order\n")
    _finish_order_slip(p, f"*** FOR {str(copy_label).upper()} ***")


def print_cancel_order_slip(order, modified_items):
    """Print cancel-order slip copies for cashier and kitchen printers."""
    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping cancel order slip printing")
        return False

    if not _has_cashier_printer():
        print("[ERROR] Main cashier printer IP is not configured. Set CASHIER_PRINTER_IPS, CASHIER_PRINTER_IP, LAN_PRINTER_IP, or PRINTER_IP.")
        return False

    cashier_success = False
    kitchen_success = True

    try:
        with _cashier_printer_manager.get_connection() as p:
            if p is None:
                return False
            _render_cancel_order_slip(p, order, modified_items, "CASHIER")
            cashier_success = True
    except Exception as e:
        print(f"[ERROR] Cancel order slip (cashier copy) printing failed: {e}")
        return False

    if _has_kitchen_printer():
        try:
            with _kitchen_printer_manager.get_connection() as p:
                if p is None:
                    kitchen_success = False
                else:
                    _render_cancel_order_slip(p, order, modified_items, "KITCHEN")
                    kitchen_success = True
        except Exception as e:
            print(f"[ERROR] Cancel order slip (kitchen copy) printing failed: {e}")
            kitchen_success = False
    else:
        print("[INFO] Kitchen printer not configured. Kitchen cancel-order copy skipped.")

    return cashier_success and kitchen_success


def print_refund_order_slip(order, refunded_items, reason):
    """Print a refund order slip for the given order.
    
    Args:
        order (Order): The refunded order object
        refunded_items (list): List of refunded items for the refunded order
        reason (str): Reason for refunding the order
        
    Returns:
        bool: True if printing was successful, False otherwise
    """
    # Skip printing in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping refund order slip print")
        return True
    
    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping refund order slip printing")
        return False
        
    if not _has_cashier_printer():
        print("[ERROR] Cashier printer is not available for refund slip printing.")
        return False
    
    try:
        with _printer_manager.get_connection() as p:
            if p is None:
                return False
                
            # Receipt text mode (honors configured print size)
            _set_standard_receipt_text(p)
            separator = _receipt_separator()
            
            # === Header ===
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32')
            p.set(align='center', bold=True)
            for line in get_receipt_header():
                p.text(f"{line}\n")
            _set_body_line_spacing(p)

            # === Separator ===
            p.text(separator)
            p.set(align='center')
            
            # Get the refund reference for the title
            refund_ref_temp = ""
            if refunded_items and len(refunded_items) > 0:
                refund_ref_temp = refunded_items[0].reference_no or f"REF-{order.id:010d}"
            # Extract just the number part for display
            refund_number = refund_ref_temp.split('-')[-1] if refund_ref_temp else ""
            
            p.set(align='center')
            p.set(bold=True)
            
            p._raw(b'\x1d\x21\x01')   # Double height for title
            p.text(f"REFUND TRANSACTION \n")
            p._raw(b'\x1d\x21\x00')   # Reset to normal size

            # Check if this refund slip has already been printed before by looking in ActivityLog
            from .models import ActivityLog
            previous_print = ActivityLog.query.filter_by(
                order_id=order.id,
                event_type='REFUND_ORDER_SLIP_PRINTED'
            ).first()
            if previous_print:
                p._raw(b'\x1d\x21\x11')   # Double height for REPRINT only
                p.text("***REPRINT***\n")
                p._raw(b'\x1d\x21\x00')   # Reset to normal size
            p.text(separator)
            p.set(bold=False)

            # === Order Info ===
            p.set(align='left')
            
            # Add reprint date and time above refund series
            reprint_datetime = datetime.now()
            p.text(f"Reprint Date: {reprint_datetime.strftime('%m/%d/%Y')}\n")
            p.text(f"Reprint Time: {reprint_datetime.strftime('%I:%M %p')}\n")
            
            order_date = order.timestamp.strftime('%b %d, %Y (%a)')
            order_time = order.timestamp.strftime('%I:%M %p')

            invoice_no = getattr(order, 'invoice_no', '') or f"INV-{order.id:010d}"
            # Extract just the number part from INV-000003
            invoice_number = invoice_no.replace('INV-', '') if invoice_no.startswith('INV-') else invoice_no
            
            p.text(f"Refund: #{refund_number}\n")
            p.text(f"{order_date}\n")
            p.text(f"REF SALES INVOICE: #{invoice_number}\n")
            # Add reference order number below reference invoice number
            order_no = getattr(order, 'order_no', '') or f"ORD-{order.id:010d}"
            # Extract just the number part from SAVORE-000003
            order_number = order_no.replace('SAVORE-', '') if order_no.startswith('SAVORE-') else order_no
            p.text(f"REF ORDER NO: #{order_number}\n")
            p.text(f"ORDER TIME: {order_time}\n")
            p.text(f"REASON: {reason}\n")
            
            # Cashier info
            p.text("\n")
            cashier = getattr(current_user, 'username', 'Cashier')
            p.text(f"Cashier: {cashier}\n")
            print_terminal_metadata(p)
            
            # === Separator ===
            p.text(separator)
            
            # === Items ===
            total_amount = 0
            for refunded_item in refunded_items:
                qty = refunded_item.modified_qty
                price = refunded_item.price
                amount = refunded_item.price * refunded_item.modified_qty
                total_amount += amount

                item_text = f"{format_qty(qty)} x {refunded_item.product_name}"
                _print_wrapped_item_line(p, item_text, _receipt_width())
                p.text(f"  Price: {price:,.2f}  Amount: {amount:,.2f}\n")
            
            # === Separator ===
            p.text(separator)
            
            # === Totals with discount breakdown (mirror original invoice) ===
            # Calculate items total from refunded items only
            items_total = total_amount
            
            # Get settlement to check for discounts
            settlement = Settlement.query.filter_by(order_id=order.id).first()
            
            p.text(format_line("TOTAL:", items_total))
            
            # Show discount breakdown (mirror original invoice) - proportional to refunded items
            proportional_discount = 0.0
            
            if settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type:
                # Calculate refund ratio for proportional calculations
                original_total = sum(item.price * item.quantity for item in order.items)
                refund_ratio = items_total / original_total if original_total > 0 else 0
                
                # Check if this is a mixed discount (comma-separated discount types)
                if ',' in settlement.order_discount_type:
                    # Mixed discount - print individual breakdown for each discount type
                    discount_types = [dt.strip() for dt in settlement.order_discount_type.split(',')]
                    
                    # Calculate per-person share
                    total_pax = getattr(settlement, 'total_no_pax', 1) or 1
                    share_per_person = items_total / total_pax if total_pax > 0 else 0
                    
                    # Handle regular discount separately if present
                    has_regular_discount = 'regular' in discount_types
                    regular_discount_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                    
                    if has_regular_discount:
                        regular_holder_portion = share_per_person
                        regular_discount_amount = regular_holder_portion * (regular_discount_percent / 100)
                        regular_discount_amount = regular_discount_amount * refund_ratio
                        proportional_discount += regular_discount_amount
                        p.text(format_line(f"LESS: {regular_discount_percent}% DISCOUNT (regular):", regular_discount_amount))
                    
                    # Process each discount type
                    for discount_type in discount_types:
                        if discount_type == 'regular':
                            continue
                        
                        # Map discount type to label
                        if discount_type == 'senior':
                            label_suffix = 'SC'
                        elif discount_type == 'pwd':
                            label_suffix = 'PWD'
                        elif discount_type == 'solo_parent':
                            label_suffix = 'SOLO'
                        elif discount_type == 'athlete':
                            label_suffix = 'NAAC'
                        elif discount_type == 'medal_of_valor':
                            label_suffix = 'MOV'
                        else:
                            label_suffix = discount_type.upper()
                        
                        discount_holder_portion = share_per_person
                        
                        if discount_type in ['senior', 'pwd', 'solo_parent']:
                            # VAT-exempt discounts
                            vat_amount = discount_holder_portion * (12 / 112)
                            vat_amount_proportional = vat_amount * refund_ratio
                            
                            discount_percent = 10 if discount_type == 'solo_parent' else 20
                            vat_exempt_base = discount_holder_portion / 1.12
                            discount_amount = vat_exempt_base * (discount_percent / 100)
                            discount_amount_proportional = discount_amount * refund_ratio
                            
                            proportional_discount += vat_amount_proportional + discount_amount_proportional
                            p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount_proportional))
                            p.text(format_line(f"LESS: {discount_percent}% DISCOUNT ({label_suffix}):", discount_amount_proportional))
                        else:
                            # NAAC/MOV discounts
                            discount_holder_portion = share_per_person
                            total_no_vat = discount_holder_portion / 1.12
                            vat_amount = discount_holder_portion * (12 / 112)
                            vat_amount_proportional = vat_amount * refund_ratio
                            vat_exempt_base = discount_holder_portion / 1.12
                            discount_amount = vat_exempt_base * 0.20
                            discount_amount_proportional = discount_amount * refund_ratio
                            
                            # For NAAC/MOV: VAT is removed then added back (net zero), only the discount counts
                            proportional_discount += discount_amount_proportional
                            p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount_proportional))
                            p.text(format_line(f"LESS: 20% DISCOUNT ({label_suffix}):", discount_amount_proportional))
                            p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount_proportional))
                else:
                    # Single discount type
                    if settlement.order_discount_type == 'regular':
                        regular_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                        discount_amount = items_total * (regular_percent / 100)
                        proportional_discount = discount_amount
                        p.text(format_line(f"LESS: {regular_percent}% DISCOUNT (regular):", discount_amount))
                    elif settlement.order_discount_type in ['senior', 'pwd', 'solo_parent']:
                        # VAT-exempt single discount
                        discount_percent = 10 if settlement.order_discount_type == 'solo_parent' else 20
                        vat_amount = items_total * (12 / 112)
                        vat_exempt_base = items_total / 1.12
                        discount_amount = vat_exempt_base * (discount_percent / 100)
                        
                        label_suffix = 'SOLO' if settlement.order_discount_type == 'solo_parent' else ('SC' if settlement.order_discount_type == 'senior' else 'PWD')
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: {discount_percent}% DISCOUNT ({label_suffix}):", discount_amount))
                        proportional_discount = vat_amount + discount_amount
                    elif settlement.order_discount_type in ['athlete', 'medal_of_valor']:
                        # NAAC/MOV discount with ADD VAT
                        label_suffix = 'NAAC' if settlement.order_discount_type == 'athlete' else 'MOV'
                        vat_amount = items_total * (12 / 112)
                        vat_exempt_base = items_total / 1.12
                        discount_amount = vat_exempt_base * 0.20
                        
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: 20% DISCOUNT ({label_suffix}):", discount_amount))
                        p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount))
                        proportional_discount = discount_amount
                    elif settlement.order_discount_type == 'oth':
                        discount_amount = getattr(settlement, 'discount_amount', 0) or 0
                        proportional_discount = discount_amount
                        p.text(format_line("LESS: OTH DISCOUNT:", discount_amount))
            
            # Calculate final total after discount
            final_total = items_total - proportional_discount
            p.text(format_line("TOTAL AMT DUE:", final_total))
            p.set(bold=False)
            p.text(separator)
            
            # === VAT Breakdown (mirror original invoice) ===
            # Always use exact settlement values for full refunds
            if settlement:
                # Full refund: use exact settlement values from original invoice
                vat_sales = settlement.vat_sales if settlement.vat_sales is not None else (final_total / 1.12)
                vat_exempt_sale = settlement.vat_exempt_sale if settlement.vat_exempt_sale is not None else 0.00
                vat_amount = settlement.vat_amount if settlement.vat_amount is not None else (final_total - vat_sales)
                zero_rated_sales = settlement.zero_rated_sales if settlement.zero_rated_sales is not None else 0.00
                total_sale = settlement.total_sale if settlement.total_sale is not None else (
                    settlement.final_total if settlement.final_total is not None else final_total
                )
            else:
                # No settlement: calculate from final total
                vat_sales = final_total / 1.12
                vat_amount = final_total - vat_sales
                vat_exempt_sale = 0.00
                zero_rated_sales = 0.00
                total_sale = final_total
            
            p.text(format_line("VAT - Sales:", vat_sales))
            p.text(format_line("VAT - Exempt Sale:", vat_exempt_sale))
            p.text(format_line("Vat Amount:", vat_amount))
            p.text(format_line("Zero Rated Sales:", zero_rated_sales))
            p.text(separator)
            
            # === Customer Information Section ===
            p.set(align='left')
            
            # Customer name field
            label = "CUSTOMER NAME"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            p.text(f"{label}: {underline}\n")

            # Customer signature field
            label = "SIGNATURE"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            p.text(f"{label}: {underline}\n")
            
            # === Signature Section ===
            p.text("\n")
            
            # Cashier signature line
            label = "Prepared by"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            cashier_name = getattr(current_user, 'username', 'Cashier')
            p.text(f"{label}: {cashier_name}\n")
            
            label = "Signature"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            p.text(f"{label}: {underline}\n")
            p.text("\n")
            
            # Manager/OIC signature line
            label = "Verified by (Manager/OIC)"
            p.text(f"{label}:\n")
            
            label = "Name"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            p.text(f"{label}: {underline}\n")
            
            label = "Signature"
            remaining_space = _receipt_width() - 2 - len(label)
            underline = "_" * remaining_space
            p.text(f"{label}: {underline}\n")
            p.text("\n")
            
            # === Footer Message ===
            p.set(align='center')
            p.text("REMARKS: REFUNDED ORDER, COPIES RETAINED FOR AUDIT\n")
            p.text(separator)
            
            # Actual audit log date and time from order_audit_logs database
            # Get the first refunded item's timestamp (all items refunded at same time)
            if refunded_items and len(refunded_items) > 0:
                audit_timestamp = refunded_items[0].timestamp
                actual_date = audit_timestamp.strftime('%b %d, %Y')
                actual_time = audit_timestamp.strftime('%I:%M %p')
            else:
                # Fallback to order timestamp if no refunded items
                actual_date = order.timestamp.strftime('%b %d, %Y')
                actual_time = order.timestamp.strftime('%I:%M %p')

            p.text(f"Date: {actual_date}\n")
            p.text(f"Time: {actual_time}\n")
            p.text("\n")
            
            # === Thank You Message ===
            thank_you = get_receipt_thank_you_message()
            if thank_you:
                p.set(align='center')
                p.text(f"{thank_you}\n")
                p.text("\n")

            # === Footer (same as sales invoice) ===
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32') 
            for line in get_receipt_footer():
                p.text(f"{line}\n")      
            _set_body_line_spacing(p)     
            p._raw(b'\x1b\x64\x05')   # Feed 5 lines so the footer clears the cutter
            p.cut()
            return True
    except Exception as e:
        print(f"[ERROR] Refund order slip printing failed: {e}")
        return False

def print_void_order_slip(order, voided_items, reason):
    """Print a void order slip for the given order.
    
    Args:
        order (Order): The voided order object
        voided_items (list): List of voided items for the voided order
        reason (str): Reason for voiding the order
        
    Returns:
        bool: True if printing was successful, False otherwise
    """
    # Skip printing in development mode
    import os
    dev_mode = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
    if dev_mode:
        print("[DEV MODE] Printer checks disabled - skipping void order slip print")
        return True
    
    if not ESCPOS_AVAILABLE:
        print("[INFO] ESCPOS library not available - skipping void order slip printing")
        return False
        
    if not _has_cashier_printer():
        print("[ERROR] Cashier printer is not available for void slip printing.")
        return False
    
    try:
        with _printer_manager.get_connection() as p:
            if p is None:
                return False
                
            # Receipt text mode (honors configured print size)
            _set_standard_receipt_text(p)
            separator = _receipt_separator()
            
            # === Header ===
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32') 
            p.set(align='center', bold=True)
            for line in get_receipt_header():
                p.text(f"{line}\n")
            _set_body_line_spacing(p)

            # === Separator ===
            p.text(separator)
            p.set(align='center')
            
            # Get the void reference for the title
            void_ref_temp = ""
            if voided_items and len(voided_items) > 0:
                void_ref_temp = voided_items[0].reference_no or f"VOID-{order.id:010d}"
            # Extract just the number part for display
            void_number = void_ref_temp.split('-')[-1] if void_ref_temp else ""
            
            p.set(align='center')
            p.set(bold=True)
            
            p._raw(b'\x1d\x21\x01')   # Double height for title
            p.text(f"VOIDED INVOICE \n")
            p._raw(b'\x1d\x21\x00')   # Reset to normal size

            # Check if this void slip has already been printed before by looking in ActivityLog
            from .models import ActivityLog
            previous_print = ActivityLog.query.filter_by(
                order_id=order.id,
                event_type='VOID_ORDER_SLIP_PRINTED'
            ).first()
            if previous_print:
                p._raw(b'\x1d\x21\x11')   # Double height for REPRINT only
                p.text("***REPRINT***\n")
                p._raw(b'\x1d\x21\x00')   # Reset to normal size

            p.text(separator)
            p.set(bold=False)

            # === Order Info ===
            p.set(align='left')
            
            # Add reprint date and time above void series
            reprint_datetime = datetime.now()
            p.text(f"Reprint Date: {reprint_datetime.strftime('%m/%d/%Y')}\n")
            p.text(f"Reprint Time: {reprint_datetime.strftime('%I:%M %p')}\n")
            
            p.set(bold=True)
            p.text(f"Void: #{void_number}\n")
            p.set(bold=False)
            order_date = order.timestamp.strftime('%b %d, %Y (%a)')
            order_time = order.timestamp.strftime('%I:%M %p')

            invoice_no = getattr(order, 'invoice_no', '') or f"INV-{order.id:010d}"
            # Extract just the number part from INV-000003
            invoice_number = invoice_no.replace('INV-', '') if invoice_no.startswith('INV-') else invoice_no
            
            p.text(f"{order_date}\n")
            p.text(f"REF SALES INVOICE: #{invoice_number}\n")
            # Add reference order number below reference invoice number
            order_no = getattr(order, 'order_no', '') or f"ORD-{order.id:010d}"
            # Extract just the number part from SAVORE-000003
            order_number = order_no.replace('SAVORE-', '') if order_no.startswith('SAVORE-') else order_no
            p.text(f"REF ORDER NO: #{order_number}\n")
            p.text(f"ORDER TIME: {order_time}\n")
            p.text(f"REASON: {reason}\n")
            
            # Cashier info
            p.text("\n")
            cashier = getattr(current_user, 'username', 'Cashier')
            p.text(f"Cashier: {cashier}\n")
            print_terminal_metadata(p)
            
            # === Separator ===
            p.text(separator)
            
            # === Items ===
            total_amount = 0
            for item in order.items:
                qty = format_qty(item.quantity)
                price = item.price
                amount = item.price * item.quantity
                total_amount += amount

                item_text = f"{qty} x {item.product_name}"
                _print_wrapped_item_line(p, item_text, _receipt_width())
                p.text(f"  Price: {price:,.2f}  Amount: {amount:,.2f}\n")
            
            # === Separator ===
            p.text(separator)
            
            # === Totals with discount breakdown (mirror original invoice) ===
            # Calculate items total
            items_total = total_amount
            
            # Get settlement to check for discounts
            settlement = Settlement.query.filter_by(order_id=order.id).first()
            
            p.text(format_line("TOTAL:", items_total))
            
            # Show discount breakdown (mirror original invoice)
            proportional_discount = 0.0
            
            if settlement and hasattr(settlement, 'order_discount_type') and settlement.order_discount_type:
                # Check if this is a mixed discount (comma-separated discount types)
                if ',' in settlement.order_discount_type:
                    # Mixed discount - print individual breakdown for each discount type
                    discount_types = [dt.strip() for dt in settlement.order_discount_type.split(',')]
                    
                    # Calculate per-person share
                    total_pax = getattr(settlement, 'total_no_pax', 1) or 1
                    share_per_person = items_total / total_pax if total_pax > 0 else 0
                    
                    # Handle regular discount separately if present
                    has_regular_discount = 'regular' in discount_types
                    regular_discount_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                    
                    if has_regular_discount:
                        regular_holder_portion = share_per_person
                        regular_discount_amount = regular_holder_portion * (regular_discount_percent / 100)
                        proportional_discount += regular_discount_amount
                        p.text(format_line(f"LESS: {regular_discount_percent}% DISCOUNT (regular):", regular_discount_amount))
                    
                    # Process each discount type
                    for discount_type in discount_types:
                        if discount_type == 'regular':
                            continue
                        
                        # Map discount type to label
                        if discount_type == 'senior':
                            label_suffix = 'SC'
                        elif discount_type == 'pwd':
                            label_suffix = 'PWD'
                        elif discount_type == 'solo_parent':
                            label_suffix = 'SOLO'
                        elif discount_type == 'athlete':
                            label_suffix = 'NAAC'
                        elif discount_type == 'medal_of_valor':
                            label_suffix = 'MOV'
                        else:
                            label_suffix = discount_type.upper()
                        
                        discount_holder_portion = share_per_person
                        
                        if discount_type in ['senior', 'pwd', 'solo_parent']:
                            # VAT-exempt discounts
                            vat_amount = discount_holder_portion * (12 / 112)
                            
                            discount_percent = 10 if discount_type == 'solo_parent' else 20
                            vat_exempt_base = discount_holder_portion / 1.12
                            discount_amount = vat_exempt_base * (discount_percent / 100)
                            
                            proportional_discount += vat_amount + discount_amount
                            p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                            p.text(format_line(f"LESS: {discount_percent}% DISCOUNT ({label_suffix}):", discount_amount))
                        else:
                            # NAAC/MOV discounts
                            discount_holder_portion = share_per_person
                            vat_amount = discount_holder_portion * (12 / 112)
                            vat_exempt_base = discount_holder_portion / 1.12
                            discount_amount = vat_exempt_base * 0.20
                            
                            # For NAAC/MOV: VAT is removed then added back (net zero), only the discount counts
                            proportional_discount += discount_amount
                            p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                            p.text(format_line(f"LESS: 20% DISCOUNT ({label_suffix}):", discount_amount))
                            p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount))
                else:
                    # Single discount type
                    if settlement.order_discount_type == 'regular':
                        regular_percent = getattr(settlement, 'regular_discount_percent', 0) or 0
                        discount_amount = items_total * (regular_percent / 100)
                        proportional_discount = discount_amount
                        p.text(format_line(f"LESS: {regular_percent}% DISCOUNT (regular):", discount_amount))
                    elif settlement.order_discount_type in ['senior', 'pwd', 'solo_parent']:
                        # VAT-exempt single discount
                        discount_percent = 10 if settlement.order_discount_type == 'solo_parent' else 20
                        vat_amount = items_total * (12 / 112)
                        vat_exempt_base = items_total / 1.12
                        discount_amount = vat_exempt_base * (discount_percent / 100)
                        
                        label_suffix = 'SOLO' if settlement.order_discount_type == 'solo_parent' else ('SC' if settlement.order_discount_type == 'senior' else 'PWD')
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: {discount_percent}% DISCOUNT ({label_suffix}):", discount_amount))
                        proportional_discount = vat_amount + discount_amount
                    elif settlement.order_discount_type in ['athlete', 'medal_of_valor']:
                        # NAAC/MOV discount with ADD VAT
                        label_suffix = 'NAAC' if settlement.order_discount_type == 'athlete' else 'MOV'
                        vat_amount = items_total * (12 / 112)
                        vat_exempt_base = items_total / 1.12
                        discount_amount = vat_exempt_base * 0.20
                        
                        p.text(format_line(f"LESS: 12% VAT ({label_suffix}):", vat_amount))
                        p.text(format_line(f"LESS: 20% DISCOUNT ({label_suffix}):", discount_amount))
                        p.text(format_line(f"ADD: 12% VAT ({label_suffix}):", vat_amount))
                        proportional_discount = discount_amount
                    elif settlement.order_discount_type == 'oth':
                        discount_amount = getattr(settlement, 'discount_amount', 0) or 0
                        proportional_discount = discount_amount
                        p.text(format_line("LESS: OTH DISCOUNT:", discount_amount))
            
            p.set(bold=True)
            final_total = items_total - proportional_discount
            p.text(format_line("TOTAL AMT DUE:", final_total))
            p.set(bold=False)
            p.text(separator)
            
            # === VAT Breakdown (mirror original invoice) ===
            if settlement:
                vat_sales = settlement.vat_sales if settlement.vat_sales is not None else order.subtotal
                vat_exempt_sale = settlement.vat_exempt_sale if settlement.vat_exempt_sale is not None else 0.00
                vat_amount = settlement.vat_amount if settlement.vat_amount is not None else order.vat
                zero_rated_sales = settlement.zero_rated_sales if settlement.zero_rated_sales is not None else 0.00
                total_sale = settlement.total_sale if settlement.total_sale is not None else (
                    settlement.final_total if settlement.final_total is not None else order.total
                )
            else:
                vat_sales = order.subtotal
                vat_exempt_sale = 0.00
                vat_amount = order.vat
                zero_rated_sales = 0.00
                total_sale = order.total
            
            p.text(format_line("VAT - Sales:", vat_sales))
            p.text(format_line("VAT - Exempt Sale:", vat_exempt_sale))
            p.text(format_line("Vat Amount:", vat_amount))
            p.text(format_line("Zero Rated Sales:", zero_rated_sales))
            p.text(separator)
            
            # === Footer Message ===
            p.set(align='center')
            p.text("REMARKS: INVOICE HAS BEEN VOIDED,\n") 
            p.text("COPIES RETAINED  FOR AUDIT\n")
            p.text(separator)
            
            # Actual audit log date and time from order_audit_logs database
            # Get the first voided item's timestamp (all items voided at same time)
            if voided_items and len(voided_items) > 0:
                audit_timestamp = voided_items[0].timestamp
                actual_date = audit_timestamp.strftime('%b %d, %Y')
                actual_time = audit_timestamp.strftime('%I:%M %p')
            else:
                # Fallback to order timestamp if no voided items
                actual_date = order.timestamp.strftime('%b %d, %Y')
                actual_time = order.timestamp.strftime('%I:%M %p')
            
            p.text(f"Date: {actual_date}\n")
            p.text(f"Time: {actual_time}\n")
            p.text("\n")
            
            # === Thank You Message ===
            thank_you = get_receipt_thank_you_message()
            if thank_you:
                p.set(align='center')
                p.text(f"{thank_you}\n")
                p.text("\n")

            # === Footer (same as sales invoice) ===
            p._raw(b'\x12')         
            p._raw(b'\x1b\x32') 
            for line in get_receipt_footer():
                p.text(f"{line}\n")      
            _set_body_line_spacing(p) 
            p._raw(b'\x1b\x64\x05')   # Feed 5 lines so the footer clears the cutter
            p.cut()
            return True
    except Exception as e:
        print(f"[ERROR] Void order slip printing failed: {e}")
        return False


def print_cancelled_void_refund_report(report_data, report_date):
    """Print cancelled, void, and refund transaction report on dot matrix printer"""
    return print_receipt(None, ReceiptType.CANCELLED_VOID_REFUND, report_data=report_data, report_date=report_date)


def _print_cancelled_void_refund_report(p, report_data, report_date):
    """Print cancelled, void, and refund transaction report on dot matrix printer"""
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")
        _set_body_line_spacing(p) 

        p.text("\n")

        # === Report Title ===
        p.set(align='center', bold=True)
        p.text("CANCELLED, VOID & REFUND REPORT\n")
        p.set(bold=False)
        p.text(f"Date: {report_date.strftime('%m/%d/%Y')}\n")
        p.text("TERMINAL NO: 01\n")
        p.text(_receipt_separator())

        p.set(align='left')
        
        # Column guide aligned with format_line_with_count columns at the configured width
        _guide_w = _receipt_width()
        _guide_lw = _guide_w // 3
        _guide_cw = _guide_w // 3 + 1
        _guide_aw = _guide_w - _guide_lw - _guide_cw
        if _guide_aw < 8:  # mirror format_line_with_count minimum
            _guide_aw = 8
            _guide_cw = _guide_w - _guide_lw - _guide_aw
        column_guide = " " * (_guide_lw + _guide_cw - 6) + "-" * 6 + " " + "-" * (_guide_aw - 1) + "\n"

        p.text(format_line_with_count("Cancelled Orders", report_data.get("Cancelled Orders", 0), report_data.get("Cancelled Amount", 0)))
        p.text(format_line_with_count("Refunded Orders", report_data.get("Refunded Orders", 0), report_data.get("Refunded Amount", 0)))
        p.text(format_line_with_count("Voided Items", report_data.get("Voided Items", 0), report_data.get("Voided Amount", 0)))
        p.text(column_guide)
        p.text(format_line_with_count("TOTAL", report_data.get("Total Count", 0), report_data.get("Total Amount", 0)))
        p.text(column_guide)
        
        # Detailed Cancelled Orders (if any)
        detailed_cancelled = report_data.get("Detailed Cancelled", [])
        if detailed_cancelled:
            p.text("\nCANCELLED ORDERS\n")
            p.text(_receipt_separator())
            for item in detailed_cancelled:
                # Format: REF# :         INV# :
                p.text(format_transaction_header("REF# :", item['reference_no'] or '', "INV# :", item['invoice_no'] or ''))
                # Format: Cashier:      TIME: 
                p.text(format_transaction_details("Cashier:", item['cashier'], "TIME:", item['time']))
                # Format: Total Qty:  Total Amount : 
                p.text(format_transaction_totals("Total Qty:", item['qty'], "Total Amount :", item['amount']))
                # Format: Reason:
                p.text(f"Reason: {item['reason']}\n")
                p.text("\n")
        
        # Detailed Refunded Orders (if any)
        detailed_refunded = report_data.get("Detailed Refunded", [])
        if detailed_refunded:
            p.text("\nREFUNDED ORDERS\n")
            p.text(_receipt_separator())
            for item in detailed_refunded:
                # Format: REF# :         INV# :
                p.text(format_transaction_header("REF# :", item['reference_no'] or '', "INV# :", item['invoice_no'] or ''))
                # Format: Cashier:      TIME: 
                p.text(format_transaction_details("Cashier:", item['cashier'], "TIME:", item['time']))
                # Format: Total Qty:  Total Amount : 
                p.text(format_transaction_totals("Total Qty:", item['qty'], "Total Amount :", item['amount']))
                # Format: Reason:
                p.text(f"Reason: {item['reason']}\n")
                p.text("\n")
        
        # Detailed Voided Items (if any)
        detailed_voided = report_data.get("Detailed Voided", [])
        if detailed_voided:
            p.text("\nVOIDED ITEMS\n")
            p.text(_receipt_separator())
            for item in detailed_voided:
                # Format: REF# :         INV# :
                p.text(format_transaction_header("REF# :", item['reference_no'] or '', "INV# :", item['invoice_no'] or ''))
                # Format: Cashier:      TIME: 
                p.text(format_transaction_details("Cashier:", item['cashier'], "TIME:", item['time']))
                # Format: Total Qty:  Total Amount : 
                p.text(format_transaction_totals("Total Qty:", item['qty'], "Total Amount :", item['amount']))
                # Format: Reason:
                p.text(f"Reason: {item['reason']}\n")
                p.text("\n")

        p.text("\n")
        p.set(align='center')
        p.text("*** END OF REPORT ***\n")
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] Cancelled/Void/Refund report printing failed: {e}")
        return False


def print_item_sales_report(report_data, from_date=None, to_date=None):
    """Print item sales report on dot matrix printer"""
    # If only one date is provided, use it as report_date for backward compatibility
    if from_date and not to_date:
        report_date = from_date
        return print_receipt(None, ReceiptType.ITEM_SALES_REPORT, report_data=report_data, report_date=report_date)
    elif from_date and to_date:
        # Pass both dates for range handling
        return print_receipt(None, ReceiptType.ITEM_SALES_REPORT, report_data=report_data, from_date=from_date, to_date=to_date)
    else:
        # Default to current date if no dates provided
        from datetime import datetime
        report_date = datetime.now()
        return print_receipt(None, ReceiptType.ITEM_SALES_REPORT, report_data=report_data, report_date=report_date)

def _print_item_sales_report(p, report_data, report_date=None, from_date=None, to_date=None):
    """Print item sales report on dot matrix printer"""
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")     
        _set_body_line_spacing(p)

        p.text("\n")

        # === Report Title ===
        p.set(align='center', bold=True)
        p.text("ITEM SALES REPORT\n")
        p.set(bold=False)
        
        # Display date range or single date
        if from_date and to_date:
            if from_date.date() == to_date.date():
                p.text(f"Date: {from_date.strftime('%m/%d/%Y')}\n")
            else:
                p.text(f"Period: {from_date.strftime('%m/%d/%Y')} - {to_date.strftime('%m/%d/%Y')}\n")
        elif report_date:
            p.text(f"Date: {report_date.strftime('%m/%d/%Y')}\n")
        else:
            from datetime import datetime
            p.text(f"Date: {datetime.now().strftime('%m/%d/%Y')}\n")
        
        p.text("TERMINAL NO: 01\n")
        p.text(_receipt_separator())

        # === Summary ===
        p.set(align='left')
        p.text(format_line_count("Total Items", len(report_data.get("sales_data", []))))
        p.text(format_line_count("Total Quantity Sold", report_data.get("totals", {}).get("quantity", 0)))
        p.text(format_line("Total Sales Amount", report_data.get("totals", {}).get("amount", 0)))
        p.text(_receipt_separator())

        # === Item Details ===
        p.text("ITEM NAME                QTY      AMOUNT\n")
        p.text(_receipt_separator())
        
        sales_data = sorted(
            report_data.get("sales_data", []),
            key=lambda x: (x.get("product_name") or "").casefold()
        )
        
        for item in sales_data:
            product_name = item.get("product_name", "")[:25]  # Limit product name length
            quantity = item.get("quantity", 0)
            amount = item.get("amount", 0)
            
            # Format the line with proper alignment
            line = f"{product_name:<25} {quantity:>3} {amount:>10.2f}\n"
            p.text(line)

        p.text(_receipt_separator())
        overall_total_amount = report_data.get("totals", {}).get("amount")
        if overall_total_amount is None:
            overall_total_amount = sum(item.get("amount", 0) for item in sales_data)
        p.set(bold=True)
        p.text(format_line("TOTAL AMOUNT:", overall_total_amount))
        p.set(bold=False)
        p.text(_receipt_separator())
        
        p.text("\n")
        p.set(align='center')
        p.text("*** END OF REPORT ***\n")
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] Item sales report printing failed: {e}")
        return False


def print_takeout_pickup_delivery_report(report_data, report_date=None):
    """Print takeout, pickup, and delivery sales report on cashier printer."""
    return print_receipt(
        None,
        ReceiptType.TAKEOUT_PICKUP_DELIVERY_REPORT,
        report_data=report_data,
        report_date=report_date
    )


def _print_takeout_pickup_delivery_report(p, report_data, report_date=None):
    """Print takeout, pickup, and delivery sales in the item-sales report style."""
    try:
        from datetime import datetime

        report_data = report_data or {}
        report_date = report_date or datetime.now()

        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Header ===
        p._raw(b'\x12')
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")
        _set_body_line_spacing(p)

        p.text("\n")

        p.set(align='center', bold=True)
        p.text("TAKEOUT / PICKUP / DELIVERY\n")
        p.text("SALES REPORT\n")
        p.set(bold=False)
        p.text(f"Date: {report_date.strftime('%m/%d/%Y')}\n")
        p.text("TERMINAL NO: 01\n")
        p.text(_receipt_separator())

        totals = report_data.get("totals", {})
        p.set(align='left')
        p.text(format_line_count("Total Transactions", totals.get("count", 0)))
        p.text(format_line("Total Sales Amount", totals.get("amount", 0)))
        p.text(_receipt_separator())

        sections = report_data.get("sections", [])
        if not sections:
            p.set(align='center')
            p.text("NO SALES FOUND\n")
        else:
            for section in sections:
                section_name = section.get("label", "ORDER TYPE")
                rows = section.get("rows", [])
                section_totals = section.get("totals", {})

                p.set(align='left', bold=True)
                p.text(f"{section_name}\n")
                p.set(bold=False)
                p.text("DATE       INVOICE NO      SALE AMT\n")
                p.text(_receipt_separator())

                if not rows:
                    p.text("No transactions\n")
                else:
                    for row in rows:
                        date_text = str(row.get("date", ""))[:10]
                        invoice_no = str(row.get("invoice_no", ""))[-14:]
                        amount = float(row.get("amount", 0) or 0)
                        p.text(f"{date_text:<10} {invoice_no:<14} {amount:>10.2f}\n")

                p.text(format_line_count("Count", section_totals.get("count", 0)))
                p.text(format_line("Subtotal", section_totals.get("amount", 0)))
                p.text(_receipt_separator())

        p.text("\n")
        p.set(align='center')
        p.text("*** END OF REPORT ***\n")
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] Takeout/Pickup/Delivery report printing failed: {e}")
        return False


def print_cashier_accountability(accountability_data, report_date):
    """Print cashier accountability report on dot matrix printer"""
    return print_receipt(None, ReceiptType.CASHIER_ACCOUNTABILITY, accountability_data=accountability_data, report_date=report_date)


def _print_cashier_accountability(p, accountability_data, report_date):
    """Print cashier accountability report on dot matrix printer"""
    try:
        # === Receipt text mode (honors configured print size) ===
        _set_standard_receipt_text(p)

        # === Header ===
        p._raw(b'\x12')         
        p._raw(b'\x1b\x32')
        p.set(align='center', bold=True)
        for line in get_receipt_header():
            p.text(f"{line}\n")
        _set_body_line_spacing(p)

        p.text("\n")

        # === Report Title (Centered, Bold, No additional info) ===
        p.set(align='center', bold=True)
        p.text("CASHIER ACCOUNTABILITY REPORT\n")
        p.set(bold=False)
        p.text(_receipt_separator())

        # === Report Date & Time (on same line) ===
        p.set(align='left')
        report_date_str = f"Report Date: {report_date.strftime('%B %d, %Y')}"
        report_time_str = f"Report Time: {accountability_data.get('Report Time', 'N/A')}"
        # Calculate spacing to right-align time
        total_width = 40
        spacing = total_width - len(report_date_str) - len(report_time_str)
        if spacing < 1:
            spacing = 1
        p.text(f"{report_date_str}\n")
        p.text(f"{report_time_str}\n")
        
        # === Start & End Date/Time ===
        p.text(f"Start Date & Time: {accountability_data.get('Start Date', 'N/A')} {accountability_data.get('Start Time', 'N/A')}\n")
        p.text(f"End Date & Time: {accountability_data.get('End Date', 'N/A')} {accountability_data.get('End Time', 'N/A')}\n")
        p.text(f"Cashier: {accountability_data.get('Cashier', 'N/A')}\n")
        p.text(_receipt_separator())
        p.text(f"Beg. OR #: {accountability_data.get('Beginning OR', '00000000000')}\n")
        p.text(f"End. OR #: {accountability_data.get('Ending OR', '00000000000')}\n")
        p.text(_receipt_separator())

        # === Payments Received ===
        p.set(bold=True)
        p.text("PAYMENTS RECEIVED\n")
        p.set(bold=False)
        p.text(format_line("CASH", accountability_data.get("Cash", 0)))
        p.text(format_line("GIFT CHECK", accountability_data.get("Gift Check", 0)))
        p.text(format_line("CHEQUE", accountability_data.get("Cheque", 0)))
        p.text(format_line("DEBIT CARD", accountability_data.get("Debit Card", 0)))
        p.text(format_line("GCASH", accountability_data.get("GCash", 0)))
        p.text(format_line("MAYA", accountability_data.get("PayMaya", 0)))
        p.text(format_line("CREDIT CARD", accountability_data.get("Visa", 0)))
        p.text(" " * (_receipt_width() - 12) + "-" * 12 + "\n")
        p.set(bold=True)
        p.text(format_line("Total Payments", accountability_data.get("Total Payments", 0)))
        p.set(bold=False)
        p.text(" " * (_receipt_width() - 12) + "-" * 12 + "\n")

        # === Void Transactions ===
        p.set(bold=True)
        p.text("VOID TRANSACTIONS\n")
        p.set(bold=False)
        p.text(format_line("Amount", accountability_data.get("Void Amount", 0)))
        p.text(format_line_count("# VOID", accountability_data.get("Void Count", 0)))
        p.text(_receipt_separator())

        # === Refund Transactions ===
        p.set(bold=True)
        p.text("REFUND TRANSACTIONS\n")
        p.set(bold=False)
        p.text(format_line("Amount", accountability_data.get("Refund Amount", 0)))
        p.text(format_line_count("# REFUND", accountability_data.get("Refund Count", 0)))
        p.text(_receipt_separator())

        # === Discounts ===
        p.set(bold=True)
        p.text("DISCOUNTS\n")
        p.set(bold=False)
        p.text(format_line_with_count("REG DISCOUNT", accountability_data.get("REG Discount Count", 0), accountability_data.get("REG Discount", 0)))
        p.text(format_line_with_count("SC DISCOUNT", accountability_data.get("SC Discount Count", 0), accountability_data.get("SC Discount", 0)))
        p.text(format_line_with_count("PWD DISCOUNT", accountability_data.get("PWD Discount Count", 0), accountability_data.get("PWD Discount", 0)))
        p.text(format_line_with_count("ATH DISCOUNT", accountability_data.get("ATH Discount Count", 0), accountability_data.get("ATH Discount", 0)))
        p.text(format_line_with_count("MOV DISCOUNT", accountability_data.get("MOV Discount Count", 0), accountability_data.get("MOV Discount", 0)))
        p.text(format_line_with_count("SOLO DISCOUNT", accountability_data.get("SOLO Discount Count", 0), accountability_data.get("SOLO Discount", 0)))
        p.text(" " * (_receipt_width() - 12) + "-" * 12 + "\n")
        p.set(bold=True)
        p.text(format_line("Total Discounts", accountability_data.get("Total Discounts", 0)))
        p.set(bold=False)
        p.text(" " * (_receipt_width() - 12) + "-" * 12 + "\n")

        # === Withdrawal ===
        p.text(format_line("WITHDRAWAL", accountability_data.get("Withdrawal", 0)))
        p.text(_receipt_separator())

        # === Transaction Summary ===
        p.set(bold=True)
        p.text("TRANSACTION SUMMARY\n")
        p.set(bold=False)
        p.text(format_line("Opening Fund", accountability_data.get("Opening Fund", 0)))
        p.text(format_line("Cash In Drawer", accountability_data.get("Cash In Drawer", 0)))
        p.text(format_line("CHEQUE", accountability_data.get("Cheque", 0)))
        p.text(format_line("CREDIT CARD", accountability_data.get("Visa", 0)))
        p.text(format_line("DEBIT CARD", accountability_data.get("Debit Card", 0)))
        p.text(format_line("MAYA", accountability_data.get("PayMaya", 0)))
        p.text(format_line("GCASH", accountability_data.get("GCash", 0)))
        p.text(format_line("GIFT CERTIFICATE", accountability_data.get("Gift Check", 0)))
        p.text(format_line("Payments Received", accountability_data.get("Total Payments", 0)))
        p.text(_receipt_separator())
        p.set(bold=True)
        short_over = accountability_data.get("Short Over", 0)
        short_over_display = "0.00" if round(short_over, 2) == 0 else f"{abs(short_over):.2f}{'+' if short_over > 0 else '-'}"
        p.text(f"SHORT/OVER: {short_over_display.rjust(total_width - 12)}\n")
        p.set(bold=False)
        p.text(_receipt_separator())

        # === Footer ===
        p.text("\n")
        p.set(align='center')
        p.text("*** END OF REPORT ***\n")
        p.text("\n\n")

        p.cut()
        return True
    except Exception as e:
        print(f"[ERROR] Cashier accountability printing failed: {e}")
        return False
