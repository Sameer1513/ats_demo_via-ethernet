"""Motor CT checks on NI-9264 ao0 (imtr).

1. MTR-03 peak: 0–100% of 2.590156979 Vrms, 10% steps; score ``peak_eng``.
2. AI-02 RMS: 0–100% of 3.663 Vrms, 10% steps; score ``rms_eng``.

±1 A. 50 Hz. 5 s idle gap between every step.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from nidaq_controller.mvbcm.breaker_ops import (
    MvbcmStopped,
    _raise_if_stopped,
    _sleep_interruptible,
)
from nidaq_controller.mvbcm.channel_map import MappedChannel
from nidaq_controller.mvbcm.live import LiveError, fetch_live, in_limits, measure
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus, find_9264


def _read_settled(fetch, board: str, settle: float, should_stop=None):
    from nidaq_controller.mvbcm.run_mvbcm import _read_settled as _impl
    return _impl(fetch, board, settle, should_stop=should_stop)


_PEAK_FS_VRMS = 2.590156979
_RMS_FS_VRMS = 3.663
_IMTR_FS_A = 100.0
_IMTR_TOL_A = 1.0


def _pct_steps(fs_vrms: float) -> tuple[dict[str, Any], ...]:
    steps = []
    for percent in range(0, 101, 10):
        expected = _IMTR_FS_A * percent / 100.0
        steps.append({
            "step": f"{percent}pct",
            "percent": percent,
            "daq_v_rms": fs_vrms * percent / 100.0,
            "expect_a": expected,
            "min_a": expected - _IMTR_TOL_A,
            "max_a": expected + _IMTR_TOL_A,
        })
    return tuple(steps)


CT_PEAK_STEPS = _pct_steps(_PEAK_FS_VRMS)
CT_MV_STEPS = _pct_steps(_RMS_FS_VRMS)
_IMTR_CHANNEL = MappedChannel("ao0", "imtr", "Motor", ("MTR-03", "AI-02"), "V")
CT_SETTLE_S = 3.0
CT_STEP_GAP_S = 5.0
CT_FREQUENCY_HZ = 50.0


def run_ct_peak_test(
    board: str,
    *,
    device: str | None = None,
    settle: float = CT_SETTLE_S,
    timeout: float = 5.0,
    fetch=None,
    on_row=None,
    should_stop=None,
    include_peak: bool = True,
    include_rms: bool = True,
) -> list[dict]:
    """MTR-03 peak_eng steps and/or AI-02 rms_eng steps."""
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    device_name = device or find_9264()
    rows: list[dict] = []
    stimulus: VoltageStimulus | None = None
    step_index = 0

    def _next_stimulus() -> VoltageStimulus:
        nonlocal stimulus, step_index
        _raise_if_stopped(should_stop)
        if step_index > 0:
            if stimulus is not None:
                try:
                    stimulus.idle()
                except Exception:
                    pass
                try:
                    stimulus.close()
                except Exception:
                    pass
                stimulus = None
            _sleep_interruptible(CT_STEP_GAP_S, should_stop)
        stimulus = VoltageStimulus(device_name, [_IMTR_CHANNEL])
        step_index += 1
        return stimulus

    try:
        if include_peak:
            for spec in CT_PEAK_STEPS:
                row = _run_peak_step(
                    board, fetch, _next_stimulus(), settle, spec,
                    should_stop=should_stop,
                )
                rows.append(row)
                if on_row is not None:
                    on_row(row)

        if include_rms:
            for spec in CT_MV_STEPS:
                row = _run_mv_step(
                    board, fetch, _next_stimulus(), settle, spec,
                    should_stop=should_stop,
                )
                rows.append(row)
                if on_row is not None:
                    on_row(row)
    except MvbcmStopped:
        raise
    finally:
        if stimulus is not None:
            try:
                stimulus.close()
            except Exception:
                pass
    return rows


def _run_peak_step(
    board, fetch, stimulus, settle, spec: dict[str, Any], *, should_stop=None,
) -> dict:
    return _run_level(
        board, fetch, stimulus, settle, spec,
        test_id="MTR-03",
        group="ct_peak",
        phase="peak",
        unit="A peak",
        field="peak_eng",
        should_stop=should_stop,
    )


def _run_mv_step(
    board, fetch, stimulus, settle, spec: dict[str, Any], *, should_stop=None,
) -> dict:
    return _run_level(
        board, fetch, stimulus, settle, spec,
        test_id="AI-02",
        group="ct_mv",
        phase="rms",
        unit="A RMS",
        field="rms_eng",
        should_stop=should_stop,
    )


def _run_level(
    board, fetch, stimulus, settle, spec, *,
    test_id, group, phase, unit, field, should_stop=None,
) -> dict:
    step = str(spec["step"])
    daq_v_rms = float(spec["daq_v_rms"])
    daq_v_peak = daq_v_rms * math.sqrt(2.0)
    expect = float(spec["expect_a"])
    low = float(spec["min_a"])
    high = float(spec["max_a"])
    try:
        stimulus.write_peak_volts({"imtr": daq_v_peak}, frequency=CT_FREQUENCY_HZ)
    except Exception as exc:
        return _row(
            test_id, group, step, phase, daq_v_rms, unit, field,
            expect, low, high, None, "FAIL", f"AO AC write failed: {exc}",
        )
    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        measured = measure(payload, "imtr", field)
    except LiveError as exc:
        return _row(
            test_id, group, step, phase, daq_v_rms, unit, field,
            expect, low, high, None, "FAIL", str(exc),
        )
    except MvbcmStopped:
        raise

    ok = in_limits(measured, low, high)
    note = (
        f"AC {daq_v_rms:g} Vrms @ {CT_FREQUENCY_HZ:g} Hz "
        f"(DAQ peak {daq_v_peak:g} V); compare {field}"
    )
    if not ok:
        note = f"expected [{low}, {high}] {unit}, got {measured}; {note}"
    return _row(
        test_id, group, step, phase, daq_v_rms, unit, field,
        expect, low, high, measured, "PASS" if ok else "FAIL", note,
    )


def _row(
    test_id: str,
    group: str,
    step: str,
    phase: str,
    daq_v_rms: float,
    unit: str,
    field: str,
    expect: float,
    low: float,
    high: float,
    measured: float | None,
    result: str,
    note: str,
) -> dict:
    return {
        "group": group,
        "test_id": test_id,
        "step": step,
        "signal": "imtr",
        "phase": phase,
        "stimulus_value": f"{daq_v_rms:g}",
        "unit": unit,
        "measured": "" if measured is None else f"{measured:.3f}",
        "field": field,
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note or f"expect={expect:g} {unit}",
    }
