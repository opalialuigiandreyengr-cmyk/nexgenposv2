import logging
import os
import sys
from logging.handlers import RotatingFileHandler


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
CONSOLE_FORMAT = "%(levelname)s:%(name)s:%(message)s"

ROUTINE_ACCESS_PATTERNS = (
    " /static/",
    " /stream_orders ",
    " /queue_status ",
    " /pending_rlc_count ",
    " /network_status ",
    " /check_missing_eod_dates ",
    " /check_printer_status ",
)


class RoutineAccessFilter(logging.Filter):
    def filter(self, record):
        if record.levelno < logging.WARNING and record.name.startswith((
            "unified_background_processor",
            "rlc_apps",
            "website.orders.sse",
        )):
            return False
        if record.name != "werkzeug" or record.levelno >= logging.WARNING:
            return True
        message = record.getMessage()
        return not any(pattern in message for pattern in ROUTINE_ACCESS_PATTERNS)


class PathAccessFilter(logging.Filter):
    def __init__(self, *patterns):
        super().__init__()
        self.patterns = patterns

    def filter(self, record):
        if record.name != "werkzeug":
            return False
        message = record.getMessage()
        return any(pattern in message for pattern in self.patterns)


class LoggerNameFilter(logging.Filter):
    def __init__(self, *prefixes):
        super().__init__()
        self.prefixes = prefixes

    def filter(self, record):
        return record.name.startswith(self.prefixes)


def _make_file_handler(logs_dir, filename, level=logging.INFO, filter_obj=None):
    os.makedirs(logs_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(logs_dir, filename),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    if filter_obj is not None:
        handler.addFilter(filter_obj)
    return handler


def _default_logs_dir(base_dir=None):
    if os.environ.get("POS_LOG_DIR"):
        return os.environ["POS_LOG_DIR"]

    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local_app_data, ".nexgen_pos", "logs")

    if base_dir is None:
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, "logs")


def _can_write_to_directory(path):
    try:
        os.makedirs(path, exist_ok=True)
        test_path = os.path.join(path, ".write_test")
        with open(test_path, "w", encoding="utf-8") as test_file:
            test_file.write("ok")
        os.remove(test_path)
        return True
    except OSError:
        return False


def configure_logging(base_dir=None):
    """Keep the terminal readable and send noisy categories to separate files."""
    if getattr(configure_logging, "_configured", False):
        return

    if base_dir is None:
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    logs_dir = _default_logs_dir(base_dir)
    if not _can_write_to_directory(logs_dir):
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        logs_dir = os.path.join(local_app_data, ".nexgen_pos", "logs")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    console_handler.addFilter(RoutineAccessFilter())
    root.addHandler(console_handler)

    root.addHandler(_make_file_handler(logs_dir, "app.log"))

    access_handler = _make_file_handler(logs_dir, "access.log")
    logging.getLogger("werkzeug").addHandler(access_handler)

    sse_handler = _make_file_handler(
        logs_dir,
        "sse.log",
        filter_obj=PathAccessFilter(" /stream_orders "),
    )
    logging.getLogger("werkzeug").addHandler(sse_handler)
    logging.getLogger("website.orders").addHandler(
        _make_file_handler(logs_dir, "sse.log", filter_obj=LoggerNameFilter("website.orders.sse"))
    )

    background_handler = _make_file_handler(
        logs_dir,
        "background.log",
        filter_obj=LoggerNameFilter("unified_background_processor", "rlc_apps"),
    )
    root.addHandler(background_handler)

    orders_handler = _make_file_handler(
        logs_dir,
        "orders.log",
        filter_obj=LoggerNameFilter("website.orders"),
    )
    root.addHandler(orders_handler)

    slow_request_handler = _make_file_handler(
        logs_dir,
        "slow_requests.log",
        filter_obj=LoggerNameFilter("website.performance"),
    )
    root.addHandler(slow_request_handler)

    configure_logging._configured = True


def clear_log_files(base_dir=None):
    """Safely truncate active log files and delete backup logs in the logs directory after End of Day."""
    try:
        logs_dir = _default_logs_dir(base_dir)
        if not os.path.exists(logs_dir):
            return True, "Logs directory does not exist."

        cleared_count = 0
        for item in os.listdir(logs_dir):
            filepath = os.path.join(logs_dir, item)
            if not os.path.isfile(filepath):
                continue

            if item.endswith((".1", ".2", ".3", ".4", ".5")):
                try:
                    os.remove(filepath)
                    cleared_count += 1
                except OSError:
                    pass
            elif item.endswith(".log"):
                try:
                    with open(filepath, "w", encoding="utf-8"):
                        pass
                    cleared_count += 1
                except OSError:
                    pass

        return True, f"Successfully cleared {cleared_count} log files."
    except Exception as e:
        return False, f"Failed to clear log files: {e}"

