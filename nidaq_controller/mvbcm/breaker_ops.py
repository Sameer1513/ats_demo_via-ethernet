"""Trip/close breaker ops scored against board ``b7`` counters.

Applies one trip then one close on the wet DO lines (same timing as the
web Apply trip/close), then checks ``trip_total`` / ``close_total`` each
increased by 1 on ``GET /api/live``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nidaq_controller.mvbcm.live import LiveError, fetch_live


def _ensure_daq_path() -> None:
    """daq_io modules import as ``core.*`` / ``constants`` (web-server layout)."""
    root = Path(__file__).resolve().parents[1]
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)

# Same defaults as ui/templates/index.html TRIP_LINE_MAP (wet DI section).
DEFAULT_LINE_MAP = {
    "trip_coil": "port2/line1",   # P2.1 Ch1 DI_13
    "switch_52a": "port5/line0",  # P5.0 Ch4 DI_16
    "switch_52b": "port4/line7",  # P4.7 Ch6 DI_18
    "close_coil": "port2/line0",  # P2.0 Ch3 DI_15
}

CHANNEL_KEYS = ("trip_coil", "switch_52a", "switch_52b", "close_coil")

DEFAULT_CHANNELS_FILE = Path(__file__).with_name("trip_channels.example.json")


def load_channels_json(path: Path | str) -> dict[str, str]:
    """Load a four-key channel map from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("channels file must be a JSON object")
    missing = [key for key in CHANNEL_KEYS if not data.get(key)]
    if missing:
        raise ValueError(f"channels file missing keys: {', '.join(missing)}")
    return {key: str(data[key]) for key in CHANNEL_KEYS}


def default_trip_channels() -> dict[str, str] | None:
    """Load ``trip_channels.example.json`` when present."""
    if not DEFAULT_CHANNELS_FILE.is_file():
        return None
    return load_channels_json(DEFAULT_CHANNELS_FILE)


def device_name_from_channels(channels: dict[str, str]) -> str | None:
    """Device name from the first full DAQmx path (e.g. Dev9/...)."""
    for key in CHANNEL_KEYS:
        raw = (channels.get(key) or "").strip()
        if "/" in raw:
            return raw.split("/", 1)[0]
    return None


def find_digital_module(device_name: str | None = None):
    """Return ModuleInfo for a digital module (USB-6509 or named device)."""
    _ensure_daq_path()
    from core.device_manager import DeviceManager

    manager = DeviceManager()
    devices = manager.discover_devices()
    candidates = []
    for device in devices:
        modules = device.modules or [device]
        for module in modules:
            do_channels = list(getattr(module, "do_channels", None) or [])
            if not do_channels:
                continue
            name = getattr(module, "name", "") or ""
            product = (getattr(module, "product_type", "") or "").upper()
            if device_name and name != device_name and device.name != device_name:
                continue
            score = len(do_channels)
            if "6509" in product or "6509" in name.upper():
                score += 1000
            candidates.append((score, module))
    if device_name and not candidates:
        raise RuntimeError(f"No digital module matching {device_name!r}")
    if not candidates:
        raise RuntimeError(
            "No digital-output module found. Pass --trip-device "
            "(e.g. Dev1) or a --trip-channels JSON with full paths."
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def resolve_channels(
    module,
    line_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map role keys to full physical DO channel names on ``module``."""
    mapping = line_map or DEFAULT_LINE_MAP
    known = list(getattr(module, "do_channels", None) or [])
    if not known:
        raise RuntimeError(f"{module.name} has no digital output channels")
    device_name = module.name
    resolved: dict[str, str] = {}
    for key in CHANNEL_KEYS:
        raw = mapping.get(key) or ""
        hit = _match_line(known, device_name, raw)
        if not hit:
            raise RuntimeError(
                f"Could not resolve {key}={raw!r} on {device_name}. "
                "Pass --trip-channels with full DAQmx paths."
            )
        resolved[key] = hit
    return resolved


def _match_line(known: list[str], device_name: str, raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text in known:
        return text
    lower = text.lower().replace("\\", "/")
    # Accept P2.1 / p2.1 style
    if lower.startswith("p") and "." in lower and "/" not in lower:
        try:
            port_s, line_s = lower[1:].split(".", 1)
            lower = f"port{int(port_s)}/line{int(line_s)}"
        except ValueError:
            pass
    if "/" not in lower:
        lower = f"{device_name.lower()}/{lower}"
    elif not lower.startswith(device_name.lower() + "/") and lower.count("/") == 1:
        # portN/lineM
        lower = f"{device_name.lower()}/{lower}"
    for ch in known:
        if ch.lower() == lower or ch.lower().endswith("/" + lower.split("/", 1)[-1]):
            return ch
        if ch.lower().endswith(lower if lower.startswith("/") else "/" + "/".join(lower.split("/")[-2:])):
            return ch
    suffix = "/".join(lower.split("/")[-2:])
    for ch in known:
        if ch.lower().endswith("/" + suffix) or ch.lower().endswith(suffix):
            return ch
    return None


def b7_totals(payload: dict) -> tuple[int, int]:
    """Return ``(trip_total, close_total)`` from a live payload."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    try:
        return int(b7["trip_total"]), int(b7["close_total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveError(f"b7 trip/close totals missing: {exc}") from exc


def wait_counter(
    fetch: Callable[[str], dict],
    board: str,
    *,
    trip_expect: int | None = None,
    close_expect: int | None = None,
    timeout_s: float = 10.0,
    poll_s: float = 0.5,
) -> tuple[int, int]:
    """Poll live until the expected counter(s) match, or timeout."""
    deadline = time.monotonic() + timeout_s
    last = (-1, -1)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = fetch(board)
            last = b7_totals(payload)
            trip_ok = trip_expect is None or last[0] == trip_expect
            close_ok = close_expect is None or last[1] == close_expect
            if trip_ok and close_ok:
                return last
        except LiveError as exc:
            last_error = exc
        time.sleep(poll_s)
    if last_error is not None and last == (-1, -1):
        raise last_error
    return last


def apply_operation(controller, channels: dict[str, str], operation: str) -> dict[str, Any]:
    """Drive one trip or close through ``DigitalIOController``."""
    return controller.apply_breaker_operation(channels, operation)


def open_breaker_controller(module):
    """Build a DigitalIOController for the given module."""
    _ensure_daq_path()
    from core.task_manager import TaskManager
    from daq_io.digital_io import DigitalIOController

    return DigitalIOController(TaskManager(), module)


def run_trip_close_test(
    board: str,
    *,
    device: str | None = None,
    channels: dict[str, str] | None = None,
    settle: float = 3.0,
    timeout: float = 5.0,
    cycles: int = 1,
    fetch=None,
    controller_factory=None,
    module=None,
    on_row=None,
    cleanup: bool = True,
) -> list[dict]:
    """Apply trip then close ``cycles`` times; score ``b7`` counters.

    Uses ``DigitalIOController.apply_breaker_operation`` — the same method
    as the web Apply trip / Apply close buttons.
    """
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    _ensure_daq_path()
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    resolved = channels or default_trip_channels()
    prefer = device or (device_name_from_channels(resolved) if resolved else None)
    if module is None:
        module = find_digital_module(prefer)
    if resolved is None:
        resolved = resolve_channels(module)
    else:
        # Re-resolve against the module so short or full paths both work.
        resolved = resolve_channels(module, resolved)

    if controller_factory is None:
        controller_factory = open_breaker_controller
    controller = controller_factory(module)

    rows: list[dict] = []
    try:
        for cycle in range(1, cycles + 1):
            for operation, counter_key in (
                ("trip", "trip_total"),
                ("close", "close_total"),
            ):
                row = _run_one(
                    board, fetch, controller, resolved,
                    operation, counter_key, settle,
                    cycle=cycle, cycles=cycles,
                )
                rows.append(row)
                if on_row is not None:
                    on_row(row)
    finally:
        if cleanup:
            try:
                controller.cleanup()
            except Exception:
                pass
    return rows


def _run_one(
    board, fetch, controller, channels, operation, counter_key, settle,
    *, cycle: int = 1, cycles: int = 1,
) -> dict:
    cycle_tag = f"cycle={cycle}/{cycles}"
    try:
        before_trip, before_close = b7_totals(fetch(board))
    except LiveError as exc:
        return _result_row(
            operation, counter_key, None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle,
        )

    before = before_trip if counter_key == "trip_total" else before_close
    expect = before + 1
    note = ""
    try:
        apply_operation(controller, channels, operation)
    except Exception as exc:
        return _result_row(
            operation, counter_key, before, expect, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle,
        )

    time.sleep(max(0.0, settle))
    try:
        if counter_key == "trip_total":
            got_trip, got_close = wait_counter(
                fetch, board, trip_expect=expect, timeout_s=max(10.0, settle + 5.0),
            )
            got = got_trip
        else:
            got_trip, got_close = wait_counter(
                fetch, board, close_expect=expect, timeout_s=max(10.0, settle + 5.0),
            )
            got = got_close
        note = (
            f"{cycle_tag}; before={before} "
            f"after_trip={got_trip} after_close={got_close}"
        )
    except LiveError as exc:
        return _result_row(
            operation, counter_key, before, expect, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle,
        )

    result = "PASS" if got == expect else "FAIL"
    if result == "FAIL":
        note = f"expected {expect}, got {got}; {note}"
    return _result_row(
        operation, counter_key, before, expect, got, result, note, cycle=cycle,
    )


def _result_row(
    operation, signal, before, expect, measured, result, note, *, cycle: int = 1,
) -> dict:
    return {
        "group": "trip_close",
        "test_id": "B7-OPS",
        "step": f"{operation}-{cycle}",
        "signal": signal,
        "phase": "counter",
        "stimulus_value": "1",
        "unit": "count",
        "measured": "" if measured is None else str(int(measured)),
        "field": "b7",
        "min": "" if expect is None else str(int(expect)),
        "max": "" if expect is None else str(int(expect)),
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note if before is None else (note or f"before={before}"),
    }
