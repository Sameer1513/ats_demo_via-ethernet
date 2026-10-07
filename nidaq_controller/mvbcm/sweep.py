"""Finer stimulus grids than the five-point rows in the workbook.

Current walks 4 mA through 20 mA in 1 mA steps. Voltage walks 0% through
100% of 333 mV in 10% steps, at 50 Hz and again at 60 Hz.

Engineering value is the same straight line as the sheet endpoints.
The pass window is a fixed fraction of full scale, not of the reading:

- coil channels (tc1, tc2, cc): 4 mA = 0 A, 20 mA = 50 A, ±0.25 A
- imtr: 4 mA = 0 A, 20 mA = 100 A, ±1 A
- iph: 0% = 0 A, 100% = 20 A, ±0.2 A
"""

from __future__ import annotations

from nidaq_controller.mvbcm.channel_map import MappedChannel
from nidaq_controller.mvbcm.sheet import Step

_MA_START = 4
_MA_STOP = 20
_COIL_FS_A = 50.0
_COIL_TOL_A = 0.25
_IMTR_FS_A = 100.0
_IMTR_TOL_A = 1.0
_IPH_FS_MV = 333.0
_IPH_FS_A = 20.0
_IPH_TOL_A = 0.2
_IPH_FREQ_HZ = (50.0, 60.0)


def steps_for_sweep(channel: MappedChannel) -> list[Step] | None:
    """Generated steps for a swept channel, or None to use the workbook."""
    if channel.sweep == "coil_ma":
        return _milliamp_steps(channel, _COIL_FS_A, _COIL_TOL_A, "A")
    if channel.sweep == "imtr_ma":
        return _milliamp_steps(channel, _IMTR_FS_A, _IMTR_TOL_A, "A")
    if channel.sweep == "iph_pct":
        return _percent_steps(channel)
    if channel.sweep:
        raise ValueError(f"Unknown sweep {channel.sweep!r} on {channel.signal}")
    return None


def _milliamp_steps(
    channel: MappedChannel,
    amps_at_20_ma: float,
    tolerance_a: float,
    dut_unit: str,
) -> list[Step]:
    steps: list[Step] = []
    span = _MA_STOP - _MA_START
    for milliamp in range(_MA_START, _MA_STOP + 1):
        expected = (milliamp - _MA_START) / span * amps_at_20_ma
        steps.append(_step(
            channel,
            label=str(milliamp),
            condition=f"Inject {milliamp} mA",
            value=float(milliamp),
            unit="mA",
            expected=expected,
            dut_unit=dut_unit,
            tolerance=tolerance_a,
        ))
    return steps


def _percent_steps(channel: MappedChannel) -> list[Step]:
    steps: list[Step] = []
    for frequency in _IPH_FREQ_HZ:
        for percent in range(0, 101, 10):
            millivolts = _IPH_FS_MV * percent / 100.0
            expected = _IPH_FS_A * percent / 100.0
            steps.append(_step(
                channel,
                label=f"{percent}pct-{frequency:g}Hz",
                condition=f"Apply {frequency:g} Hz sinusoidal load-current signal at {percent}% FS",
                value=millivolts,
                unit="mV RMS",
                expected=expected,
                dut_unit="A RMS",
                tolerance=_IPH_TOL_A,
            ))
    return steps


def _step(
    channel: MappedChannel,
    *,
    label: str,
    condition: str,
    value: float,
    unit: str,
    expected: float,
    dut_unit: str,
    tolerance: float,
) -> Step:
    test_id = channel.test_ids[0] if channel.test_ids else channel.signal
    return Step(
        row=0,
        test_id=test_id,
        objective=channel.sheet_label,
        step=label,
        condition=condition,
        input_value=value,
        input_unit=unit,
        expected=expected,
        dut_unit=dut_unit,
        min_accepted=expected - tolerance,
        max_accepted=expected + tolerance,
    )
