"""AC voltage on an NI-9264.

The fixture steps the card output down by the channel scale (11 for iph),
so the card is driven at ``sheet_mV / 1000 * scale`` volts RMS. The waveform
is a sine, and the DAQmx amplitude is the peak.

Values passed to :meth:`write` are millivolts RMS at the board input.
"""

from __future__ import annotations

import math

import numpy as np

from nidaq_controller.constants import AC_SAMPLE_RATE, AC_WAVEFORM_SAMPLES, DEFAULT_VOLTAGE_RANGE
from nidaq_controller.mvbcm.channel_map import MappedChannel


class VoltageStimulus:
    """Continuous sine output. One task holds every channel in the group."""

    def __init__(self, device_name: str, channels: tuple[MappedChannel, ...] | list[MappedChannel]):
        if not channels:
            raise ValueError("voltage group has no channels")
        if not device_name:
            raise ValueError("voltage group has no device name")
        import nidaqmx

        self._channels = list(channels)
        self._signals = [channel.signal for channel in channels]
        physical = [
            channel.ni_channel
            if "/" in channel.ni_channel
            else f"{device_name}/{channel.ni_channel}"
            for channel in channels
        ]
        low, high = DEFAULT_VOLTAGE_RANGE
        self._low = low
        self._high = high
        self._task = nidaqmx.Task()
        self._started = False
        self._timed = False
        try:
            self._task.ao_channels.add_ao_voltage_chan(
                ", ".join(physical),
                min_val=low,
                max_val=high,
            )
        except Exception as exc:
            self._task.close()
            raise RuntimeError(
                f"Could not open voltage outputs on {device_name}: {exc}. "
                "Stop any analog output already running on this module."
            ) from exc

    def write(self, millivolts_rms_by_signal: dict[str, float], frequency: float = 50.0) -> None:
        """Drive each channel. Missing signals are written as 0 V."""
        if frequency <= 0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        peaks = []
        for channel in self._channels:
            millivolts = float(millivolts_rms_by_signal.get(channel.signal, 0.0))
            rms_volts = (millivolts / 1000.0) * channel.scale
            peak = rms_volts * math.sqrt(2.0)
            if peak < self._low or peak > self._high:
                raise ValueError(
                    f"{channel.signal} {millivolts} mV RMS x {channel.scale:g} "
                    f"is {peak:.3f} V peak, outside "
                    f"[{self._low:g}, {self._high:g}] V"
                )
            peaks.append(peak)
        wave = _sine(frequency, peaks)
        self._arm_clock()
        if self._started:
            try:
                self._task.stop()
            except Exception:
                pass
            self._started = False
        self._task.write(wave, auto_start=True)
        self._started = True

    def idle(self) -> None:
        """Drive every channel in the task to 0 V."""
        self.write({}, frequency=50.0)

    def close(self) -> None:
        """Zero the outputs and release the module."""
        task = getattr(self, "_task", None)
        if task is None:
            return
        try:
            try:
                self.idle()
            except Exception:
                pass
            if self._started:
                try:
                    task.stop()
                except Exception:
                    pass
        finally:
            task.close()
            self._task = None
            self._started = False
            self._timed = False

    def _arm_clock(self) -> None:
        if self._timed:
            return
        import nidaqmx

        self._task.timing.cfg_samp_clk_timing(
            rate=AC_SAMPLE_RATE,
            sample_mode=nidaqmx.constants.AcquisitionType.CONTINUOUS,
            samps_per_chan=AC_WAVEFORM_SAMPLES,
        )
        self._task.out_stream.regen_mode = (
            nidaqmx.constants.RegenerationMode.ALLOW_REGENERATION
        )
        self._timed = True


def _sine(frequency: float, peaks: list[float]) -> np.ndarray:
    """One buffer with an integer number of cycles at 50 Hz and 60 Hz."""
    sample_count = AC_WAVEFORM_SAMPLES
    sample_rate = AC_SAMPLE_RATE
    ticks = np.arange(sample_count) / sample_rate
    rows = [
        peak * np.sin(2.0 * math.pi * frequency * ticks)
        for peak in peaks
    ]
    if len(rows) == 1:
        return rows[0]
    return np.vstack(rows)


def find_9264() -> str:
    """Name of the first module whose product type contains ``9264``."""
    from nidaq_controller.core.device_manager import DeviceManager

    manager = DeviceManager()
    found: list[str] = []
    for device in manager.discover_devices():
        modules = device.modules or [device]
        for module in modules:
            product = (getattr(module, "product_type", "") or "").upper()
            if "9264" in product:
                found.append(module.name)
    if not found:
        raise RuntimeError("No NI-9264 module found.")
    return found[0]
