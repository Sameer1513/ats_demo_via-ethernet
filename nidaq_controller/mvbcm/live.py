"""Read the board live API and pick the engineering field for a DUT unit."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# DUT unit text from the sheet, compared after collapsing whitespace
# and upper-casing. More units are more entries, not a new reader.
DUT_FIELD = {
    "A": "rms_eng",
    "A RMS": "rms_eng",
    "A PEAK": "peak_eng",
}


class LiveError(Exception):
    """The live payload cannot be scored."""


def fetch_live(board: str, timeout: float = 5.0) -> dict:
    """GET ``{board}/api/live`` and require ``ok`` true."""
    url = board.rstrip("/") + "/api/live"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LiveError(f"GET {url} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LiveError(f"GET {url} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LiveError(f"GET {url} did not return an object")
    if payload.get("ok") is not True:
        raise LiveError(f"live ok is not true: {payload.get('ok')!r}")
    return payload


def field_for_unit(dut_unit: str) -> str:
    """Map a sheet DUT unit onto a key under ``rms``."""
    key = " ".join((dut_unit or "").strip().upper().split())
    try:
        return DUT_FIELD[key]
    except KeyError as exc:
        raise LiveError(f"no live field mapped for DUT unit {dut_unit!r}") from exc


def measure(payload: dict, signal: str, field: str) -> float:
    """Return ``rms[field]`` for the channel whose ``signal`` matches."""
    rms = payload.get("rms")
    if not isinstance(rms, dict):
        raise LiveError("live payload has no rms object")
    channels = rms.get("channels")
    if not isinstance(channels, list):
        raise LiveError("live payload has no rms.channels")
    index = None
    for i, channel in enumerate(channels):
        if not isinstance(channel, dict):
            continue
        if str(channel.get("signal", "")).lower() == signal.lower():
            index = i
            break
    if index is None:
        raise LiveError(f"signal {signal!r} not in live channels")
    values = rms.get(field)
    if not isinstance(values, list) or index >= len(values):
        raise LiveError(f"field {field} missing at index {index}")
    try:
        return float(values[index])
    except (TypeError, ValueError) as exc:
        raise LiveError(f"field {field}[{index}] is not a number") from exc


def in_limits(measured: float, low: float, high: float) -> bool:
    """Inclusive window."""
    return low <= measured <= high
