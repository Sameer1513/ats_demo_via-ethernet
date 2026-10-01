"""
Four-channel wet-DI trip and close waveforms.

Trip (breaker closed -> open):

    trip_coil    10 ms high pulse
    switch_52a   high, falls on the trip-coil falling edge
    switch_52b   low, rises opening_travel after 52A falls
    close_coil   stays low

Close (breaker open -> closed), the mirror:

    close_coil   10 ms high pulse
    switch_52b   high, falls on the close-coil falling edge
    switch_52a   low, rises closing_travel after 52B falls
    trip_coil    stays low

Wet digital input modules have no sample clock. Capture polls one
on-demand DI task, with a 200 ms pre-trigger ring and a 300 ms tail.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from constants import (
    TRIP_ARM_TIMEOUT_S,
    TRIP_OPENING_TRAVEL_S,
    TRIP_OPERATION_MAX_S,
    TRIP_POST_TRIGGER_S,
    TRIP_PRE_TRIGGER_S,
    TRIP_PULSE_S,
    TRIP_WAVEFORM_RATE_HZ,
)
from core.task_manager import normalize_physical_channel

CHANNEL_KEYS: Tuple[str, ...] = (
    "trip_coil",
    "switch_52a",
    "switch_52b",
    "close_coil",
)

CHANNEL_LABELS: Dict[str, str] = {
    "trip_coil": "TRIP COIL",
    "switch_52a": "52A SWITCH",
    "switch_52b": "52B SWITCH",
    "close_coil": "CLOSE COIL",
}

# pulse: channel that pulses. fall: contact that drops when the pulse ends.
# rise: contact that comes up after travel. idle: stays low.
OPERATIONS: Dict[str, Dict[str, Any]] = {
    "trip": {
        "pulse_index": 0,
        "fall_index": 1,
        "rise_index": 2,
        "idle_index": 3,
        "pulse_mark": "TRIP",
        "rise_mark": "52B",
        "travel_label": "OPENING TRAVEL",
        "fall_caption": "52A FALL",
        "rise_caption": "52B RISE",
        "idle_ok": "CLOSE LOW",
        "idle_bad": "CLOSE WENT HIGH",
        "pulse_name": "trip coil",
        "rise_name": "52B",
    },
    "close": {
        "pulse_index": 3,
        "fall_index": 2,
        "rise_index": 1,
        "idle_index": 0,
        "pulse_mark": "CLOSE",
        "rise_mark": "52A",
        "travel_label": "CLOSING TRAVEL",
        "fall_caption": "52B FALL",
        "rise_caption": "52A RISE",
        "idle_ok": "TRIP LOW",
        "idle_bad": "TRIP WENT HIGH",
        "pulse_name": "close coil",
        "rise_name": "52A",
    },
}


class OperationStopped(Exception):
    """Loop stop requested while a capture was armed or recording."""


def trip_spec() -> Dict[str, float]:
    """Nominal timing shared by trip and close, in milliseconds."""
    pre_ms = TRIP_PRE_TRIGGER_S * 1000.0
    pulse_ms = TRIP_PULSE_S * 1000.0
    travel_ms = TRIP_OPENING_TRAVEL_S * 1000.0
    post_ms = TRIP_POST_TRIGGER_S * 1000.0
    operation_ms = pulse_ms + travel_ms
    return {
        "pre_trigger_ms": pre_ms,
        "trip_pulse_ms": pulse_ms,
        "close_pulse_ms": pulse_ms,
        "opening_travel_ms": travel_ms,
        "closing_travel_ms": travel_ms,
        "operation_time_ms": operation_ms,
        "operation_time_max_ms": TRIP_OPERATION_MAX_S * 1000.0,
        "post_trigger_ms": post_ms,
        "event_duration_ms": pre_ms + operation_ms + post_ms,
    }


def build_trip_waveform(
    sample_rate_hz: float = TRIP_WAVEFORM_RATE_HZ,
) -> Dict[str, Any]:
    """Ideal trip record. Close coil stays low."""
    return _build_operation_waveform("trip", sample_rate_hz)


def build_close_waveform(
    sample_rate_hz: float = TRIP_WAVEFORM_RATE_HZ,
) -> Dict[str, Any]:
    """
    Ideal close record.

    Close coil pulses 10 ms at 200 ms. 52B falls on that falling edge.
    52A rises 100 ms later. Trip coil stays low.
    """
    return _build_operation_waveform("close", sample_rate_hz)


def _build_operation_waveform(operation: str, sample_rate_hz: float) -> Dict[str, Any]:
    op = _operation(operation)
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")

    pre_n = int(round(TRIP_PRE_TRIGGER_S * sample_rate_hz))
    pulse_n = int(round(TRIP_PULSE_S * sample_rate_hz))
    travel_n = int(round(TRIP_OPENING_TRAVEL_S * sample_rate_hz))
    post_n = int(round(TRIP_POST_TRIGGER_S * sample_rate_hz))
    pulse_rise_i = pre_n
    pulse_fall_i = pre_n + pulse_n
    contact_rise_i = pulse_fall_i + travel_n
    n = contact_rise_i + post_n + 1
    t_s = np.arange(n, dtype=np.float64) / sample_rate_hz

    columns = [np.zeros(n, dtype=np.int8) for _ in CHANNEL_KEYS]
    columns[op["pulse_index"]][pulse_rise_i:pulse_fall_i] = 1
    columns[op["fall_index"]][:pulse_fall_i] = 1
    columns[op["rise_index"]][contact_rise_i:] = 1

    traces = {key: columns[i] for i, key in enumerate(CHANNEL_KEYS)}
    payload = _pack_waveform(t_s, traces, sample_rate_hz, mode="preview", operation=operation)
    payload["triggered"] = True
    payload["warnings"] = []
    return payload


def measure_trip_timing(
    t_s: np.ndarray,
    traces: Dict[str, np.ndarray],
) -> Dict[str, Optional[float]]:
    """Trip edge times in ms from the start of the record."""
    return _measure(t_s, traces, "trip")


def acquire_trip_waveform(
    channels: Dict[str, str],
    arm_timeout_s: float = TRIP_ARM_TIMEOUT_S,
    pre_trigger_s: float = TRIP_PRE_TRIGGER_S,
    post_trigger_s: float = TRIP_POST_TRIGGER_S,
    operation_max_s: float = TRIP_OPERATION_MAX_S,
    stop_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Capture one trip. Trigger is the trip-coil rising edge. Done when 52B rises."""
    return _acquire(
        "trip", channels, arm_timeout_s, pre_trigger_s, post_trigger_s,
        operation_max_s, stop_event,
    )


def acquire_close_waveform(
    channels: Dict[str, str],
    arm_timeout_s: float = TRIP_ARM_TIMEOUT_S,
    pre_trigger_s: float = TRIP_PRE_TRIGGER_S,
    post_trigger_s: float = TRIP_POST_TRIGGER_S,
    operation_max_s: float = TRIP_OPERATION_MAX_S,
    stop_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Capture one close. Trigger is the close-coil rising edge. Done when 52A rises."""
    return _acquire(
        "close", channels, arm_timeout_s, pre_trigger_s, post_trigger_s,
        operation_max_s, stop_event,
    )


def _acquire(
    operation: str,
    channels: Dict[str, str],
    arm_timeout_s: float,
    pre_trigger_s: float,
    post_trigger_s: float,
    operation_max_s: float,
    stop_event: Optional[threading.Event],
) -> Dict[str, Any]:
    op = _operation(operation)
    ordered = _require_channels(channels)
    pulse_i = int(op["pulse_index"])
    rise_i = int(op["rise_index"])

    try:
        import nidaqmx
    except ImportError as exc:
        raise RuntimeError("NI-DAQmx is not installed") from exc

    samples: List[Tuple[float, Tuple[bool, bool, bool, bool]]] = []
    warnings: List[str] = []

    try:
        with nidaqmx.Task() as task:
            task.di_channels.add_di_chan(",".join(ordered))
            task.start()

            pre: deque = deque()
            prev: Optional[Tuple[bool, bool, bool, bool]] = None
            triggered = False
            t_pulse: Optional[float] = None
            t_rise: Optional[float] = None
            arm_deadline = time.perf_counter() + max(0.5, float(arm_timeout_s))

            while True:
                if stop_event is not None and stop_event.is_set():
                    raise OperationStopped()
                now = time.perf_counter()
                bits = _read_bits(task, 4)
                if not triggered:
                    pre.append((now, bits))
                    while len(pre) > 1 and (now - pre[0][0]) > pre_trigger_s:
                        pre.popleft()
                    if prev is not None and (not prev[pulse_i]) and bits[pulse_i]:
                        triggered = True
                        t_pulse = now
                        samples = list(pre)
                    elif now >= arm_deadline:
                        raise TimeoutError(
                            f"No {op['pulse_name']} rising edge within {arm_timeout_s:.0f}s"
                        )
                else:
                    samples.append((now, bits))
                    if t_rise is None and prev is not None and (not prev[rise_i]) and bits[rise_i]:
                        t_rise = now
                    assert t_pulse is not None
                    if t_rise is not None and (now - t_rise) >= post_trigger_s:
                        break
                    if (now - t_pulse) >= (operation_max_s + post_trigger_s):
                        warnings.append(
                            f"{op['rise_name']} did not rise within operation time max "
                            f"({operation_max_s:.1f}s); record stopped"
                        )
                        break
                prev = bits
    except (TimeoutError, OperationStopped):
        raise
    except Exception as exc:
        raise RuntimeError(f"Wet DI waveform read failed: {exc}") from exc

    if len(samples) < 2:
        raise RuntimeError("Capture returned no samples")

    t0 = samples[0][0]
    t_s = np.array([s[0] - t0 for s in samples], dtype=np.float64)
    columns = list(zip(*(s[1] for s in samples)))
    traces = {
        key: np.asarray(columns[i], dtype=np.int8)
        for i, key in enumerate(CHANNEL_KEYS)
    }

    period_ms = float(np.median(np.diff(t_s)) * 1000.0)
    rate = 1000.0 / period_ms if period_ms > 0 else 0.0
    if period_ms > 2.0:
        warnings.append(
            f"Median sample period is {period_ms:.2f} ms. "
            "A 10 ms coil pulse will only be a few samples on software-timed wet DI."
        )

    t_s, traces = _decimate(t_s, traces, max_points=4000)
    payload = _pack_waveform(t_s, traces, rate, mode="capture", operation=operation)
    payload["triggered"] = True
    payload["warnings"] = warnings
    payload["channels_used"] = {key: ordered[i] for i, key in enumerate(CHANNEL_KEYS)}
    payload["timing"]["sample_period_ms"] = round(period_ms, 3)
    return payload


def _operation(name: str) -> Dict[str, Any]:
    try:
        return OPERATIONS[name]
    except KeyError as exc:
        raise ValueError("operation must be trip or close") from exc


def _measure(
    t_s: np.ndarray,
    traces: Dict[str, np.ndarray],
    operation: str,
) -> Dict[str, Optional[float]]:
    op = _operation(operation)
    t_s = np.asarray(t_s, dtype=np.float64)
    columns = [np.asarray(traces[key]).astype(bool) for key in CHANNEL_KEYS]
    pulse = columns[int(op["pulse_index"])]
    falling = columns[int(op["fall_index"])]
    rising = columns[int(op["rise_index"])]

    rise_i = _edge_index(pulse, rising=True)
    fall_i = _edge_index(pulse, rising=False, start=rise_i if rise_i is not None else 0)
    contact_fall_i = _edge_index(falling, rising=False, start=rise_i or 0)
    rise_start = contact_fall_i if contact_fall_i is not None else (fall_i or 0)
    contact_rise_i = _edge_index(rising, rising=True, start=rise_start)

    def at(idx: Optional[int]) -> Optional[float]:
        if idx is None:
            return None
        return float(t_s[idx] * 1000.0)

    def delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return b - a

    pulse_rise_ms = at(rise_i)
    pulse_fall_ms = at(fall_i)
    contact_fall_ms = at(contact_fall_i)
    contact_rise_ms = at(contact_rise_i)
    return {
        "pulse_rise_ms": pulse_rise_ms,
        "pulse_fall_ms": pulse_fall_ms,
        "pulse_ms": delta(pulse_rise_ms, pulse_fall_ms),
        "contact_fall_ms": contact_fall_ms,
        "contact_rise_ms": contact_rise_ms,
        "travel_ms": delta(contact_fall_ms, contact_rise_ms),
        "operation_time_ms": delta(pulse_rise_ms, contact_rise_ms),
    }


def _require_channels(channels: Dict[str, str]) -> List[str]:
    ordered: List[str] = []
    for key in CHANNEL_KEYS:
        name = normalize_physical_channel((channels or {}).get(key))
        if not name:
            raise ValueError(f"Missing DI channel for {key}")
        ordered.append(name)
    if len(set(ordered)) != 4:
        raise ValueError("Waveform needs 4 different digital input lines")
    return ordered


def _read_bits(task: Any, n: int) -> Tuple[bool, ...]:
    data = task.read(number_of_samples_per_channel=1, timeout=1.0)
    if n == 1:
        value = data[0] if isinstance(data, (list, tuple)) else data
        return (bool(value),)
    if isinstance(data, np.ndarray):
        flat = data.reshape(-1)
        return tuple(bool(flat[i]) for i in range(n))
    return tuple(bool(data[i]) for i in range(n))


def _edge_index(samples: np.ndarray, rising: bool, start: int = 0) -> Optional[int]:
    if len(samples) < 2:
        return None
    start = max(0, start)
    prev = bool(samples[start])
    for i in range(start + 1, len(samples)):
        cur = bool(samples[i])
        if rising and (not prev) and cur:
            return i
        if (not rising) and prev and (not cur):
            return i
        prev = cur
    return None


def _decimate(
    t_s: np.ndarray,
    traces: Dict[str, np.ndarray],
    max_points: int,
) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    n = len(t_s)
    if n <= max_points:
        return t_s, traces
    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[-1] = True
    for samples in traces.values():
        changed = np.diff(samples.astype(np.int8)) != 0
        keep[1:] |= changed
    step = max(1, n // max_points)
    keep[::step] = True
    idx = np.flatnonzero(keep)
    return t_s[idx], {k: v[idx] for k, v in traces.items()}


def _pack_waveform(
    t_s: np.ndarray,
    traces: Dict[str, np.ndarray],
    sample_rate_hz: float,
    mode: str,
    operation: str,
) -> Dict[str, Any]:
    op = _operation(operation)
    measured = _measure(t_s, traces, operation)
    spec = trip_spec()
    timing = dict(spec)
    timing.update({k: _round(v) for k, v in measured.items()})
    timing["event_duration_ms"] = _round(float(t_s[-1] * 1000.0))
    idle = np.asarray(traces[CHANNEL_KEYS[int(op["idle_index"])]])
    timing["idle_stayed_low"] = not bool(np.any(idle))

    if operation == "trip":
        timing["trip_rise_ms"] = timing["pulse_rise_ms"]
        timing["trip_fall_ms"] = timing["pulse_fall_ms"]
        timing["trip_pulse_ms"] = timing["pulse_ms"]
        timing["switch_52a_fall_ms"] = timing["contact_fall_ms"]
        timing["switch_52b_rise_ms"] = timing["contact_rise_ms"]
        timing["opening_travel_ms"] = timing["travel_ms"]
        timing["close_coil_stayed_low"] = timing["idle_stayed_low"]
    else:
        timing["close_rise_ms"] = timing["pulse_rise_ms"]
        timing["close_fall_ms"] = timing["pulse_fall_ms"]
        timing["close_pulse_ms"] = timing["pulse_ms"]
        timing["switch_52b_fall_ms"] = timing["contact_fall_ms"]
        timing["switch_52a_rise_ms"] = timing["contact_rise_ms"]
        timing["closing_travel_ms"] = timing["travel_ms"]
        timing["trip_coil_stayed_low"] = timing["idle_stayed_low"]

    return {
        "operation": operation,
        "mode": mode,
        "sample_rate_hz": round(float(sample_rate_hz), 3),
        "t_ms": [round(float(t) * 1000.0, 3) for t in t_s],
        "channels": [
            {
                "key": key,
                "label": CHANNEL_LABELS[key],
                "samples": traces[key].astype(int).tolist(),
            }
            for key in CHANNEL_KEYS
        ],
        "spec": spec,
        "timing": timing,
        "display": {
            "pulse_mark": op["pulse_mark"],
            "rise_mark": op["rise_mark"],
            "travel_label": op["travel_label"],
            "fall_caption": op["fall_caption"],
            "rise_caption": op["rise_caption"],
            "idle_ok": op["idle_ok"],
            "idle_bad": op["idle_bad"],
        },
    }


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 3)
