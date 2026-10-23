"""DC / AC current on an NI-9266.

One task holds every channel in the group. The channel list comes from
the map, so a later trio on the same module is more entries, not a new task
shape. Values passed to :meth:`write` are milliamps. DAQmx receives amps.
"""

from __future__ import annotations

import math

import numpy as np

from nidaq_controller.constants import (
    AC_SAMPLE_RATE,
    AC_WAVEFORM_SAMPLES,
    DEFAULT_CURRENT_RANGE,
    MILLIAMPS_TO_AMPS,
)
from nidaq_controller.mvbcm.channel_map import MappedChannel


class CurrentStimulus:
    """Software-timed current output. The level holds after each write."""

    def __init__(self, device_name: str, channels: tuple[MappedChannel, ...] | list[MappedChannel]):
        if not channels:
            raise ValueError("current group has no channels")
        if not device_name:
            raise ValueError("current group has no device name")
        import nidaqmx

        self._signals = [channel.signal for channel in channels]
        physical = [
            channel.ni_channel
            if "/" in channel.ni_channel
            else f"{device_name}/{channel.ni_channel}"
            for channel in channels
        ]
        low, high = DEFAULT_CURRENT_RANGE
        self._task = nidaqmx.Task()
        self._started = False
        try:
            self._task.ao_channels.add_ao_current_chan(
                ", ".join(physical),
                min_val=low,
                max_val=high,
            )
        except Exception as exc:
            self._task.close()
            raise RuntimeError(
                f"Could not open current outputs on {device_name}: {exc}. "
                "Stop any analog output already running on this module."
            ) from exc

    def write(self, milliamps_by_signal: dict[str, float], frequency: float = 50.0) -> None:
        """Set each channel. Missing signals are written as 0 mA."""
        amps = []
        low, high = DEFAULT_CURRENT_RANGE
        for signal in self._signals:
            milliamps = float(milliamps_by_signal.get(signal, 0.0))
            value = milliamps / MILLIAMPS_TO_AMPS
            if value < low or value > high:
                raise ValueError(
                    f"{signal} {milliamps} mA is outside "
                    f"[{low * MILLIAMPS_TO_AMPS:g}, {high * MILLIAMPS_TO_AMPS:g}] mA"
                )
            amps.append(value)
        # On-demand AO writes one sample and then the task is no longer
        # running, so the next write must auto-start again. If a device
        # leaves the task running, fall back to a plain write.
        try:
            self._task.write(amps, auto_start=True)
        except Exception as exc:
            message = str(exc).lower()
            if "running" not in message and "-200479" not in message:
                raise
            self._task.write(amps, auto_start=False)
        self._started = True

    def idle(self) -> None:
        """Drive every channel in the task to 0 mA."""
        self.write({})

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


class AcCurrentStimulus:
    """Continuous AC current sine on an NI-9266 (same path as UI Apply Config AC).

    ``write`` takes milliamps **peak** (direct). Waveform is clipped to the
    module current range (typically 0–20 mA).
    """

    def __init__(self, device_name: str, channels: tuple[MappedChannel, ...] | list[MappedChannel]):
        if not channels:
            raise ValueError("current group has no channels")
        if not device_name:
            raise ValueError("current group has no device name")
        import nidaqmx

        self._signals = [channel.signal for channel in channels]
        physical = [
            channel.ni_channel
            if "/" in channel.ni_channel
            else f"{device_name}/{channel.ni_channel}"
            for channel in channels
        ]
        low, high = DEFAULT_CURRENT_RANGE
        self._low = low
        self._high = high
        self._task = nidaqmx.Task()
        self._started = False
        self._timed = False
        try:
            self._task.ao_channels.add_ao_current_chan(
                ", ".join(physical),
                min_val=low,
                max_val=high,
            )
        except Exception as exc:
            self._task.close()
            raise RuntimeError(
                f"Could not open AC current outputs on {device_name}: {exc}. "
                "Stop any analog output already running on this module."
            ) from exc

    def write(self, milliamps_peak_by_signal: dict[str, float], frequency: float = 50.0) -> None:
        """Drive each channel as a sine. Missing signals are written as 0 mA."""
        if frequency <= 0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        peaks_a = []
        for signal in self._signals:
            milliamps = float(milliamps_peak_by_signal.get(signal, 0.0))
            if milliamps < 0:
                raise ValueError(f"{signal} peak mA must not be negative")
            peak_a = milliamps / MILLIAMPS_TO_AMPS
            if peak_a > self._high:
                raise ValueError(
                    f"{signal} {milliamps} mA peak is outside "
                    f"[0, {self._high * MILLIAMPS_TO_AMPS:g}] mA"
                )
            peaks_a.append(peak_a)
        wave = _sine_current(frequency, peaks_a)
        wave = np.clip(wave, self._low, self._high)
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
        """Drive every channel to 0 mA."""
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


def _sine_current(frequency: float, peaks_a: list[float]) -> np.ndarray:
    sample_count = AC_WAVEFORM_SAMPLES
    sample_rate = AC_SAMPLE_RATE
    ticks = np.arange(sample_count) / sample_rate
    rows = [
        peak * np.sin(2.0 * math.pi * frequency * ticks)
        for peak in peaks_a
    ]
    if len(rows) == 1:
        return rows[0]
    return np.vstack(rows)


def find_9266() -> str:
    """Name of the first module whose product type contains ``9266``."""
    from nidaq_controller.core.device_manager import DeviceManager

    manager = DeviceManager()
    found: list[str] = []
    for device in manager.discover_devices():
        modules = device.modules or [device]
        for module in modules:
            product = (getattr(module, "product_type", "") or "").upper()
            if "9266" in product:
                found.append(module.name)
    if not found:
        raise RuntimeError("No NI-9266 module found. Pass --device.")
    return found[0]
