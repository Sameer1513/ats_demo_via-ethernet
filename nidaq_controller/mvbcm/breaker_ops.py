"""Trip/close breaker ops scored against board ``b7`` counters.

Applies one trip then one close on the wet DO lines (same timing as the
web Apply trip/close). Before each apply, snapshot the last EVENT NO from
Live Recent Events. After apply, wait ``EVENT_SETTLE_S`` (12 s), then
check ``trip_total`` / ``close_total`` each increased by 1 on
``GET /api/live``. When ``backup_trip_coil`` is mapped it pulses with trip
and ``backup_trip_total`` is scored as its own row.

Event check uses Live RECENT EVENTS (LAST 5): ``operations.recent[]``
on the same ``GET /api/live`` snapshot. EVENT NO is ``event_num``;
EVENT REASON is ``rec_slot`` / ``trigger_reason``. After a trip, EVENT NO
must be last+1 with ``trip`` and last+2 with ``backup_trip``. Close is
last+1 with ``close``. Backup is polled up to ``BACKUP_EVENT_WAIT_S``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nidaq_controller.mvbcm.live import LiveError, fetch_live


class MvbcmStopped(Exception):
    """User requested stop of an in-flight MV BCM run."""


def _raise_if_stopped(should_stop) -> None:
    if should_stop is not None and should_stop():
        raise MvbcmStopped("stopped by user")


def _sleep_interruptible(seconds: float, should_stop=None) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _raise_if_stopped(should_stop)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _ensure_daq_path() -> None:
    """daq_io modules import as ``core.*`` / ``constants`` (web-server layout)."""
    root = Path(__file__).resolve().parents[1]
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)

# Same defaults as ui/templates/index.html TRIP_LINE_MAP (wet DI section).
DEFAULT_LINE_MAP = {
    "trip_coil": "port2/line1",          # P2.1 Ch1 DI_13
    "backup_trip_coil": "port5/line1",   # P5.1 Ch2 DI_14
    "switch_52a": "port5/line0",         # P5.0 Ch4 DI_16
    "switch_52b": "port4/line7",         # P4.7 Ch6 DI_18
    "close_coil": "port2/line0",         # P2.0 Ch3 DI_15
}

REQUIRED_CHANNEL_KEYS = ("trip_coil", "switch_52a", "switch_52b", "close_coil")
OPTIONAL_CHANNEL_KEYS = ("backup_trip_coil",)
CHANNEL_KEYS = REQUIRED_CHANNEL_KEYS + OPTIONAL_CHANNEL_KEYS

# Wait after each trip/close apply before scoring last EVENT NO + reason and b7.
EVENT_SETTLE_S = 12.0
COUNTER_SETTLE_S = EVENT_SETTLE_S  # backward-compat alias
# Backup COMTRADE row lags the trip row; poll this long after the 12 s trip score.
BACKUP_EVENT_WAIT_S = 10.0
# Gap between finished trip scoring and the close apply (minus backup poll already waited).
TRIP_TO_CLOSE_GAP_S = 10.0

DEFAULT_CHANNELS_FILE = Path(__file__).resolve().parent / "trip_channels.json"


def load_channels_json(path: Path | str) -> dict[str, str]:
    """Load channel map from JSON (4 required keys + optional backup)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("channels file must be a JSON object")
    missing = [key for key in REQUIRED_CHANNEL_KEYS if not data.get(key)]
    if missing:
        raise ValueError(f"channels file missing keys: {', '.join(missing)}")
    out = {key: str(data[key]) for key in REQUIRED_CHANNEL_KEYS}
    for key in OPTIONAL_CHANNEL_KEYS:
        raw = (data.get(key) or "").strip()
        if raw:
            out[key] = str(data[key])
    return out


def default_trip_channels() -> dict[str, str]:
    """Load ``trip_channels.json`` (required for trip/close mapping)."""
    if not DEFAULT_CHANNELS_FILE.is_file():
        raise FileNotFoundError(
            f"Trip channel map not found: {DEFAULT_CHANNELS_FILE}"
        )
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
    for key in REQUIRED_CHANNEL_KEYS:
        raw = mapping.get(key) or ""
        hit = _match_line(known, device_name, raw)
        if not hit:
            raise RuntimeError(
                f"Could not resolve {key}={raw!r} on {device_name}. "
                "Pass --trip-channels with full DAQmx paths."
            )
        resolved[key] = hit
    for key in OPTIONAL_CHANNEL_KEYS:
        raw = (mapping.get(key) or "").strip()
        if not raw:
            continue
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


def b7_totals(payload: dict) -> tuple[int, int, int]:
    """Return ``(trip_total, close_total, backup_trip_total)`` from live."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    try:
        return (
            int(b7["trip_total"]),
            int(b7["close_total"]),
            int(b7["backup_trip_total"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveError(f"b7 trip/close/backup totals missing: {exc}") from exc


def b7_event_stamp(payload: dict) -> int:
    """``b7.last_confirmed_operation_ms`` (0 if missing)."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    try:
        return int(b7.get("last_confirmed_operation_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveError(f"b7.last_confirmed_operation_ms invalid: {exc}") from exc


def _norm_event_text(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _coerce_event_num(val: Any) -> int | None:
    if val in (None, "") or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        digits = "".join(ch for ch in val if ch.isdigit())
        if digits:
            return int(digits)
    return None


def _event_id(item: dict) -> int | None:
    """EVENT NO from ``operations.recent[].event_num`` (COMTRADE index)."""
    for key in ("event_num", "event_no", "event_id", "eventNo", "evt_id"):
        eid = _coerce_event_num(item.get(key))
        if eid is not None:
            return eid
    return None


def _event_reason(item: dict) -> str:
    trig = item.get("trigger_reason")
    if isinstance(trig, str) and trig.strip():
        return trig.strip()
    slot = item.get("rec_slot")
    if isinstance(slot, str) and slot.strip():
        return slot.strip()
    for key in ("event_reason", "reason", "evt_reason"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _event_kind(item: dict) -> str | None:
    slot = str(item.get("rec_slot") or "").strip().lower()
    if slot in ("trip", "backup_trip", "close"):
        return slot
    reason = _event_reason(item)
    for line in (reason.splitlines() or [reason]):
        blob = _norm_event_text(line)
        if not blob:
            continue
        if blob.startswith("backuptrip") or blob.startswith("backtrip"):
            return "backup_trip"
        if blob.startswith("close"):
            return "close"
        if blob.startswith("trip"):
            return "trip"
    return None


def _entries_from_pool(pool: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in pool:
        if not isinstance(item, dict):
            continue
        eid = _event_id(item)
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        out.append({
            "id": eid,
            "kind": _event_kind(item),
            "reason": _event_reason(item),
            "raw": item,
        })
    out.sort(key=lambda row: int(row["id"]))
    return out


def _parse_event_entries(payload: dict | None) -> list[dict[str, Any]]:
    """Live RECENT EVENTS (LAST 5): ``operations.recent[]`` only."""
    if not isinstance(payload, dict):
        return []
    ops = payload.get("operations")
    if not isinstance(ops, dict):
        return []
    recent = ops.get("recent")
    if not isinstance(recent, list):
        return []
    return _entries_from_pool(recent)


def live_event_entries(
    payload: dict | None,
    *,
    board: str | None = None,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """EVENT NO + kind from GET /api/live ``operations.recent``."""
    del board, timeout
    return _parse_event_entries(payload)


def wait_counter(
    fetch: Callable[[str], dict],
    board: str,
    *,
    trip_expect: int | None = None,
    close_expect: int | None = None,
    backup_expect: int | None = None,
    timeout_s: float = 10.0,
    poll_s: float = 0.5,
    should_stop=None,
) -> tuple[int, int, int, int, dict | None]:
    """Poll live until the expected counter(s) match, or timeout.

    Returns ``(trip, close, backup, last_confirmed_operation_ms, payload)``.
    """
    deadline = time.monotonic() + timeout_s
    last = (-1, -1, -1)
    last_stamp = 0
    last_payload: dict | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        _raise_if_stopped(should_stop)
        try:
            payload = fetch(board)
            last_payload = payload
            last = b7_totals(payload)
            last_stamp = b7_event_stamp(payload)
            trip_ok = trip_expect is None or last[0] == trip_expect
            close_ok = close_expect is None or last[1] == close_expect
            backup_ok = backup_expect is None or last[2] == backup_expect
            if trip_ok and close_ok and backup_ok:
                return (*last, last_stamp, payload)
        except LiveError as exc:
            last_error = exc
        time.sleep(poll_s)
    if last_error is not None and last == (-1, -1, -1):
        raise last_error
    return (*last, last_stamp, last_payload)


def _event_at(entries: list[dict[str, Any]], eid: int) -> dict[str, Any] | None:
    for item in entries:
        if int(item["id"]) == eid:
            return item
    return None


def _backup_ready(
    payload: dict | None,
    *,
    last_no: int,
    backup_expect: int | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        _trip, _close, backup = b7_totals(payload)
    except LiveError:
        return False
    if backup_expect is not None and backup != backup_expect:
        return False
    hit = _event_at(live_event_entries(payload), last_no + 2)
    return hit is not None and hit.get("kind") == "backup_trip"


def _wait_backup_event(
    fetch: Callable[[str], dict],
    board: str,
    *,
    last_no: int,
    backup_expect: int | None,
    timeout_s: float,
    should_stop=None,
    start_payload: dict | None = None,
    poll_s: float = 0.5,
) -> dict | None:
    """Poll until EVENT NO last+2 is ``backup_trip`` and the total moved."""
    payload = start_payload
    if _backup_ready(payload, last_no=last_no, backup_expect=backup_expect):
        return payload
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        _raise_if_stopped(should_stop)
        try:
            payload = fetch(board)
            if _backup_ready(
                payload, last_no=last_no, backup_expect=backup_expect,
            ):
                return payload
        except LiveError as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_s, remaining))
    if payload is None and last_error is not None:
        raise last_error
    return payload


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
    settle: float | None = None,
    timeout: float = 5.0,
    cycles: int = 1,
    fetch=None,
    controller_factory=None,
    module=None,
    on_row=None,
    cleanup: bool = True,
    should_stop=None,
) -> list[dict]:
    """Apply trip then close ``cycles`` times; score ``b7`` counters.

    Uses ``DigitalIOController.apply_breaker_operation`` — the same method
    as the web Apply trip / Apply close buttons.
    Waits ``settle`` (default ``EVENT_SETTLE_S`` = 12 s) after each apply
    before checking last EVENT NO, EVENT REASON, and counters.
    ``should_stop`` raises ``MvbcmStopped`` between cycles when true.
    """
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if settle is None:
        settle = EVENT_SETTLE_S
    _ensure_daq_path()
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    resolved = channels or default_trip_channels()
    prefer = device or device_name_from_channels(resolved)
    if module is None:
        module = find_digital_module(prefer)
    # Re-resolve against the module so short or full paths both work.
    resolved = resolve_channels(module, resolved)

    if controller_factory is None:
        controller_factory = open_breaker_controller
    controller = controller_factory(module)

    score_backup = "backup_trip_coil" in resolved
    rows: list[dict] = []
    try:
        for cycle in range(1, cycles + 1):
            _raise_if_stopped(should_stop)
            trip_rows, backup_wait = _run_trip_cycle(
                board, fetch, controller, resolved, settle,
                cycle=cycle, cycles=cycles, score_backup=score_backup,
                should_stop=should_stop,
            )
            for row in trip_rows:
                rows.append(row)
                if on_row is not None:
                    on_row(row)
            _raise_if_stopped(should_stop)
            _sleep_interruptible(
                max(0.0, TRIP_TO_CLOSE_GAP_S - backup_wait), should_stop,
            )
            _raise_if_stopped(should_stop)
            close_rows = _run_close_cycle(
                board, fetch, controller, resolved, settle,
                cycle=cycle, cycles=cycles,
                should_stop=should_stop,
            )
            for row in close_rows:
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


def _event_fail_rows(
    cycle_tag: str, cycle: int, exc: Exception, *, score_backup: bool = False,
) -> list[dict]:
    rows = [
        _result_row(
            "trip", "trip_total", None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle,
        ),
    ]
    if score_backup:
        rows.append(
            _result_row(
                "backup_trip", "backup_trip_total", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            )
        )
    rows.append(
        _result_row(
            "trip", "event_created", None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
        )
    )
    if score_backup:
        rows.append(
            _result_row(
                "backup_trip", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            )
        )
    return rows


def _last_event_no(entries: list[dict[str, Any]]) -> int:
    return max((int(item["id"]) for item in entries), default=0)


def _new_events(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    last_no = _last_event_no(before)
    return [item for item in after if int(item["id"]) > last_no]


def _score_kind_event_row(
    *,
    operation: str,
    kind: str,
    cycle: int,
    cycle_tag: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    board: str | None = None,
    expect_offset: int = 1,
) -> dict:
    """PASS if EVENT NO last+offset exists and ``rec_slot`` is ``kind``."""
    del board
    last_no = _last_event_no(before)
    expect_no = last_no + expect_offset
    after_no = _last_event_no(after)
    hit = _event_at(after, expect_no)
    kind_ok = hit is not None and hit.get("kind") == kind
    created_all = _new_events(before, after)
    reasons = [
        f"#{item['id']} {item.get('kind') or '?'} {item.get('reason') or ''}".strip()
        for item in created_all
    ]
    measured = int(hit["id"]) if hit else after_no
    note = (
        f"{cycle_tag}; last_event={last_no} expect {kind} #{expect_no}; "
        f"after_max={after_no}; new=[{'; '.join(reasons) or 'none'}]"
    )
    ok = kind_ok
    if not after:
        ok = False
        note = f"live has no recent events; {note}"
    elif hit is None:
        ok = False
        note = f"missing event #{expect_no}; {note}"
    elif not kind_ok:
        ok = False
        note = (
            f"#{expect_no} kind {hit.get('kind') or '?'} "
            f"not {kind}; {note}"
        )
    return _result_row(
        operation, "event_created", last_no, expect_no,
        measured, "PASS" if ok else "FAIL", note, cycle=cycle,
        phase="event", unit="event",
    )


def _run_trip_cycle(
    board, fetch, controller, channels, settle,
    *, cycle: int, cycles: int, score_backup: bool, should_stop=None,
) -> tuple[list[dict], float]:
    """Trip at 12 s, then poll up to 30 s for backup_trip in operations.recent."""
    cycle_tag = f"cycle={cycle}/{cycles}"
    try:
        before_payload = fetch(board)
        before_trip, before_close, before_backup = b7_totals(before_payload)
    except LiveError as exc:
        return _event_fail_rows(cycle_tag, cycle, exc, score_backup=score_backup), 0.0

    before_events = live_event_entries(before_payload, board=board)
    last_no = _last_event_no(before_events)
    trip_expect = before_trip + 1
    backup_expect = before_backup + 1 if score_backup else None
    try:
        apply_operation(controller, channels, "trip")
    except Exception as exc:
        rows = [
            _result_row(
                "trip", "trip_total", before_trip, trip_expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
        ]
        if score_backup:
            rows.append(
                _result_row(
                    "backup_trip", "backup_trip_total", before_backup, backup_expect,
                    None, "FAIL", f"{cycle_tag}; {exc}", cycle=cycle,
                )
            )
        rows.append(
            _result_row(
                "trip", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            )
        )
        if score_backup:
            rows.append(
                _result_row(
                    "backup_trip", "event_created", None, None, None, "FAIL",
                    f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
                )
            )
        return rows, 0.0

    _sleep_interruptible(max(0.0, settle), should_stop)
    try:
        after_payload = fetch(board)
        got_trip, got_close, got_backup = b7_totals(after_payload)
    except LiveError as exc:
        return _event_fail_rows(cycle_tag, cycle, exc, score_backup=score_backup), 0.0

    after_events = live_event_entries(after_payload, board=board)

    note = (
        f"{cycle_tag}; settle={settle:.0f}s; last_event={last_no}; "
        f"before_trip={before_trip} before_backup={before_backup} "
        f"after_trip={got_trip} after_backup={got_backup} after_close={got_close}"
    )
    trip_ok = got_trip == trip_expect
    trip_note = note if trip_ok else f"expected {trip_expect}, got {got_trip}; {note}"
    rows = [
        _result_row(
            "trip", "trip_total", before_trip, trip_expect, got_trip,
            "PASS" if trip_ok else "FAIL", trip_note, cycle=cycle,
        )
    ]
    rows.append(
        _score_kind_event_row(
            operation="trip", kind="trip",
            cycle=cycle, cycle_tag=cycle_tag,
            before=before_events, after=after_events, board=board,
            expect_offset=1,
        )
    )

    backup_wait = 0.0
    if score_backup:
        t0 = time.monotonic()
        try:
            after_payload = _wait_backup_event(
                fetch, board,
                last_no=last_no,
                backup_expect=backup_expect,
                timeout_s=BACKUP_EVENT_WAIT_S,
                should_stop=should_stop,
                start_payload=after_payload,
            )
        except LiveError as exc:
            rows.append(
                _result_row(
                    "backup_trip", "backup_trip_total", before_backup, backup_expect,
                    None, "FAIL", f"{cycle_tag}; {exc}", cycle=cycle,
                )
            )
            rows.append(
                _result_row(
                    "backup_trip", "event_created", None, None, None, "FAIL",
                    f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
                )
            )
            return rows, time.monotonic() - t0
        backup_wait = time.monotonic() - t0
        if not isinstance(after_payload, dict):
            rows.append(
                _result_row(
                    "backup_trip", "backup_trip_total", before_backup, backup_expect,
                    None, "FAIL", f"{cycle_tag}; no live after backup wait",
                    cycle=cycle,
                )
            )
            rows.append(
                _result_row(
                    "backup_trip", "event_created", None, None, None, "FAIL",
                    f"{cycle_tag}; no live after backup wait",
                    cycle=cycle, phase="event", unit="event",
                )
            )
            return rows, backup_wait
        got_trip, got_close, got_backup = b7_totals(after_payload)
        after_events = live_event_entries(after_payload, board=board)
        backup_ok = got_backup == backup_expect
        backup_note = (
            f"{cycle_tag}; backup_wait={backup_wait:.0f}s; last_event={last_no}; "
            f"before_backup={before_backup} after_backup={got_backup}"
        )
        if not backup_ok:
            backup_note = f"expected {backup_expect}, got {got_backup}; {backup_note}"
        rows.append(
            _result_row(
                "backup_trip", "backup_trip_total", before_backup, backup_expect,
                got_backup, "PASS" if backup_ok else "FAIL", backup_note,
                cycle=cycle,
            )
        )
        rows.append(
            _score_kind_event_row(
                operation="backup_trip", kind="backup_trip",
                cycle=cycle, cycle_tag=cycle_tag,
                before=before_events, after=after_events, board=board,
                expect_offset=2,
            )
        )
    return rows, backup_wait


def _run_close_cycle(
    board, fetch, controller, channels, settle,
    *, cycle: int = 1, cycles: int = 1, should_stop=None,
) -> list[dict]:
    """Snapshot last EVENT NO → close → wait 12 s → score increment + reason + total."""
    cycle_tag = f"cycle={cycle}/{cycles}"
    try:
        before_payload = fetch(board)
        before_trip, before_close, before_backup = b7_totals(before_payload)
    except LiveError as exc:
        return [
            _result_row(
                "close", "close_total", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    before_events = live_event_entries(before_payload, board=board)
    last_no = _last_event_no(before_events)
    expect = before_close + 1
    try:
        apply_operation(controller, channels, "close")
    except Exception as exc:
        return [
            _result_row(
                "close", "close_total", before_close, expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    _sleep_interruptible(max(0.0, settle), should_stop)
    try:
        after_payload = fetch(board)
        got_trip, got_close, got_backup = b7_totals(after_payload)
        note = (
            f"{cycle_tag}; settle={settle:.0f}s; last_event={last_no}; "
            f"before={before_close} "
            f"after_trip={got_trip} after_backup={got_backup} after_close={got_close}"
        )
    except LiveError as exc:
        return [
            _result_row(
                "close", "close_total", before_close, expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    close_ok = got_close == expect
    if not close_ok:
        note = f"expected {expect}, got {got_close}; {note}"
    after_events = live_event_entries(after_payload, board=board)
    return [
        _result_row(
            "close", "close_total", before_close, expect, got_close,
            "PASS" if close_ok else "FAIL", note, cycle=cycle,
        ),
        _score_kind_event_row(
            operation="close", kind="close",
            cycle=cycle, cycle_tag=cycle_tag,
            before=before_events, after=after_events, board=board,
            expect_offset=1,
        ),
    ]


def _result_row(
    operation, signal, before, expect, measured, result, note, *,
    cycle: int = 1, phase: str = "counter", unit: str = "count",
) -> dict:
    return {
        "group": "trip_close",
        "test_id": "B7-OPS",
        "step": f"{operation}-{cycle}",
        "signal": signal,
        "phase": phase,
        "stimulus_value": "1",
        "unit": unit,
        "measured": "" if measured is None else str(int(measured)),
        "field": "b7",
        "min": "" if expect is None else str(int(expect)),
        "max": "" if expect is None else str(int(expect)),
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note if before is None else (note or f"before={before}"),
    }
