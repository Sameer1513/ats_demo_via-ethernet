"""Motor CT checks on NI-9264 ao0 (imtr).

1. MTR-03 peak: sheet AC Vrms → DAQ peak = Vrms×√2; score ``peak_eng``.
2. AI-02: AC Vrms on ao0 (×√2 → peak); score ``rms_eng`` (not peak).

5 s idle gap between every step.
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


# Sheet MTR-03: DAQ AC V RMS → board A peak ±1 A.
CT_PEAK_STEPS: tuple[dict[str, Any], ...] = (
    {
        "step": 1,
        "daq_v_rms": 0.647539245,
        "expect_a": 25.0,
        "min_a": 24.0,
        "max_a": 26.0,
        "note": "Peak within +/-1% FS",
    },
    {
        "step": 2,
        "daq_v_rms": 1.29507849,
        "expect_a": 50.0,
        "min_a": 49.0,
        "max_a": 51.0,
        "note": "Peak within +/-1% FS",
    },
    {
        "step": 3,
        "daq_v_rms": 1.942617734,
        "expect_a": 75.0,
        "min_a": 74.0,
        "max_a": 76.0,
        "note": "Peak within +/-1% FS",
    },
    {
        "step": 4,
        "daq_v_rms": 2.590156979,
        "expect_a": 100.0,
        "min_a": 99.0,
        "max_a": 101.0,
        "note": "Peak within +/-1% FS",
    },
)

# AI-02: AC DAQ volts as VRMS (UI AC+VRMS → DAQ peak = V×√2); score rms_eng.
CT_MV_STEPS: tuple[dict[str, Any], ...] = (
    {
        "step": 6,
        "daq_v_rms": 0.0,
        "expect_a": 0.0,
        "min_a": -1.0,
        "max_a": 1.0,
        "note": "Within +/-1% FS for motor instantaneous measurement",
    },
    {
        "step": 7,
        "daq_v_rms": 0.91575,
        "expect_a": 25.0,
        "min_a": 24.0,
        "max_a": 26.0,
        "note": "Within +/-1% FS for motor instantaneous measurement",
    },
    {
        "step": 8,
        "daq_v_rms": 1.8315,
        "expect_a": 50.0,
        "min_a": 49.0,
        "max_a": 51.0,
        "note": "Within +/-1% FS for motor instantaneous measurement",
    },
    {
        "step": 9,
        "daq_v_rms": 2.74725,
        "expect_a": 75.0,
        "min_a": 74.0,
        "max_a": 76.0,
        "note": "Within +/-1% FS for motor instantaneous measurement",
    },
    {
        "step": 10,
        "daq_v_rms": 3.663,
        "expect_a": 100.0,
        "min_a": 99.0,
        "max_a": 101.0,
        "note": "Within +/-1% FS for motor instantaneous measurement",
    },
)

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
) -> list[dict]:
    """MTR-03 peak_eng steps, then AI-02 0–333 mV rms_eng steps."""
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
        for spec in CT_PEAK_STEPS:
            stim = _next_stimulus()
            row = _run_peak_step(
                board, fetch, stim, settle, spec, should_stop=should_stop,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)

        for spec in CT_MV_STEPS:
            stim = _next_stimulus()
            row = _run_mv_step(
                board, fetch, stim, settle, spec, should_stop=should_stop,
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
    step = int(spec["step"])
    daq_v_rms = float(spec["daq_v_rms"])
    daq_v_peak = daq_v_rms * math.sqrt(2.0)
    expect = float(spec["expect_a"])
    low = float(spec["min_a"])
    high = float(spec["max_a"])
    try:
        stimulus.write_peak_volts({"imtr": daq_v_peak}, frequency=CT_FREQUENCY_HZ)
    except Exception as exc:
        return _row(
            test_id="MTR-03",
            group="ct_peak",
            step=step,
            phase="peak",
            stimulus_value=daq_v_rms,
            unit="A peak",
            field="peak_eng",
            expect=expect,
            low=low,
            high=high,
            measured=None,
            result="FAIL",
            note=f"AO AC write failed: {exc}",
        )
    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        measured = measure(payload, "imtr", "peak_eng")
    except LiveError as exc:
        return _row(
            test_id="MTR-03",
            group="ct_peak",
            step=step,
            phase="peak",
            stimulus_value=daq_v_rms,
            unit="A peak",
            field="peak_eng",
            expect=expect,
            low=low,
            high=high,
            measured=None,
            result="FAIL",
            note=str(exc),
        )
    except MvbcmStopped:
        raise

    ok = in_limits(measured, low, high)
    note = (
        f"AC {daq_v_rms:g} Vrms @ {CT_FREQUENCY_HZ:g} Hz "
        f"(DAQ peak {daq_v_peak:g} V); "
        f"{spec.get('note') or ''}"
    ).strip()
    if not ok:
        note = f"expected [{low}, {high}] A peak, got {measured}; {note}"
    return _row(
        test_id="MTR-03",
        group="ct_peak",
        step=step,
        phase="peak",
        stimulus_value=daq_v_rms,
        unit="A peak",
        field="peak_eng",
        expect=expect,
        low=low,
        high=high,
        measured=measured,
        result="PASS" if ok else "FAIL",
        note=note,
    )


def _run_mv_step(
    board, fetch, stimulus, settle, spec: dict[str, Any], *, should_stop=None,
) -> dict:
    step = int(spec["step"])
    daq_v_rms = float(spec["daq_v_rms"])
    # Same as UI Apply Config AC + VRMS → DAQmx peak.
    daq_v_peak = daq_v_rms * math.sqrt(2.0)
    expect = float(spec["expect_a"])
    low = float(spec["min_a"])
    high = float(spec["max_a"])
    try:
        stimulus.write_peak_volts({"imtr": daq_v_peak}, frequency=CT_FREQUENCY_HZ)
    except Exception as exc:
        return _row(
            test_id="AI-02",
            group="ct_mv",
            step=step,
            phase="rms",
            stimulus_value=daq_v_rms,
            unit="A RMS",
            field="rms_eng",
            expect=expect,
            low=low,
            high=high,
            measured=None,
            result="FAIL",
            note=f"AO AC write failed: {exc}",
        )
    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        measured = measure(payload, "imtr", "rms_eng")
    except LiveError as exc:
        return _row(
            test_id="AI-02",
            group="ct_mv",
            step=step,
            phase="rms",
            stimulus_value=daq_v_rms,
            unit="A RMS",
            field="rms_eng",
            expect=expect,
            low=low,
            high=high,
            measured=None,
            result="FAIL",
            note=str(exc),
        )
    except MvbcmStopped:
        raise

    ok = in_limits(measured, low, high)
    note = (
        f"AC {daq_v_rms:g} Vrms @ {CT_FREQUENCY_HZ:g} Hz "
        f"(DAQ peak {daq_v_peak:g} V); compare rms_eng; "
        f"{spec.get('note') or ''}"
    ).strip()
    if not ok:
        note = f"expected [{low}, {high}] A RMS, got {measured}; {note}"
    return _row(
        test_id="AI-02",
        group="ct_mv",
        step=step,
        phase="rms",
        stimulus_value=daq_v_rms,
        unit="A RMS",
        field="rms_eng",
        expect=expect,
        low=low,
        high=high,
        measured=measured,
        result="PASS" if ok else "FAIL",
        note=note,
    )


def _row(
    *,
    test_id: str,
    group: str,
    step: int,
    phase: str,
    stimulus_value: float,
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
        "step": str(step),
        "signal": "imtr",
        "phase": phase,
        "stimulus_value": f"{stimulus_value:g}",
        "unit": unit,
        "measured": "" if measured is None else f"{measured:.3f}",
        "field": field,
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note or f"expect={expect:g} {unit}",
    }
