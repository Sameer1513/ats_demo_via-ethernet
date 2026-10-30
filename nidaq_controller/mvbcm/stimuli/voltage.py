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
        self._write_peaks(peaks, frequency)

    def write_peak_volts(
        self, volts_peak_by_signal: dict[str, float], frequency: float = 50.0,
    ) -> None:
        """Drive each channel as a sine. Values are card volts **peak** (UI Direct AC).

        ``MappedChannel.scale`` is ignored — same as Apply Config AC / direct.
        Missing signals are written as 0 V.
        """
        if frequency <= 0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        peaks = []
        for channel in self._channels:
            peak = float(volts_peak_by_signal.get(channel.signal, 0.0))
            if peak < 0:
                raise ValueError(f"{channel.signal} peak V must not be negative")
            if peak > self._high:
                raise ValueError(
                    f"{channel.signal} {peak:g} V peak is outside "
                    f"[{self._low:g}, {self._high:g}] V"
                )
            peaks.append(peak)
        self._write_peaks(peaks, frequency)

    def write_harmonic_mv(
        self,
        millivolts_rms_by_signal: dict[str, float],
        frequency: float = 50.0,
        h3_pct: float = 10.0,
    ) -> None:
        """H1 = sheet mV RMS × scale; H3 = ``h3_pct`` of H1 peak. Missing = 0 V."""
        if frequency <= 0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        if h3_pct < 0:
            raise ValueError(f"h3_pct must not be negative, got {h3_pct}")
        peaks = []
        for channel in self._channels:
            millivolts = float(millivolts_rms_by_signal.get(channel.signal, 0.0))
            rms_volts = (millivolts / 1000.0) * channel.scale
            peak = rms_volts * math.sqrt(2.0)
            if peak < 0 or peak * (1.0 + h3_pct / 100.0) > self._high:
                raise ValueError(
                    f"{channel.signal} {millivolts} mV RMS x {channel.scale:g} "
                    f"+ H3 {h3_pct:g}% is outside [{self._low:g}, {self._high:g}] V"
                )
            peaks.append(peak)
        wave = _sine_plus_h3(frequency, peaks, h3_pct)
        wave = np.clip(wave, self._low, self._high)
        self._write_wave(wave)

    def write_harmonic_from_peaks(
        self,
        millivolts_rms_by_signal: dict[str, float],
        h3_peak_volts_by_signal: dict[str, float],
        frequency: float = 50.0,
    ) -> None:
        """H1 from sheet mV RMS × scale; H3 is card volts **peak** (from live peak_eng)."""
        self.write_harmonics(
            millivolts_rms_by_signal, {3: h3_peak_volts_by_signal}, frequency,
        )

    def write_harmonics(
        self,
        millivolts_rms_by_signal: dict[str, float],
        overtones: dict[int, dict[str, float]],
        frequency: float = 50.0,
    ) -> None:
        """H1 from sheet mV RMS × scale; each order is card volts **peak** per signal."""
        h1_by_signal = {}
        for channel in self._channels:
            millivolts = float(millivolts_rms_by_signal.get(channel.signal, 0.0))
            h1_by_signal[channel.signal] = (
                (millivolts / 1000.0) * channel.scale * math.sqrt(2.0)
            )
        self.write_harmonics_card(h1_by_signal, overtones, frequency)

    def write_harmonics_card(
        self,
        h1_peak_by_signal: dict[str, float],
        overtones: dict[int, dict[str, float]],
        frequency: float = 50.0,
    ) -> None:
        """H1 and overtones are card volts **peak** per signal."""
        if frequency <= 0:
            raise ValueError(f"frequency must be positive, got {frequency}")
        h1_peaks = []
        for channel in self._channels:
            h1 = float(h1_peak_by_signal.get(channel.signal, 0.0))
            if h1 < 0:
                raise ValueError(f"{channel.signal} H1 peak V must not be negative")
            h1_peaks.append(h1)
        ordered: list[tuple[int, list[float]]] = []
        for order in sorted(overtones):
            n = int(order)
            if n < 2:
                raise ValueError(f"harmonic order must be >= 2, got {n}")
            peaks_n = []
            for channel in self._channels:
                amp = float(overtones[order].get(channel.signal, 0.0))
                if amp < 0:
                    raise ValueError(f"{channel.signal} H{n} peak V must not be negative")
                peaks_n.append(amp)
            ordered.append((n, peaks_n))
        for index, h1 in enumerate(h1_peaks):
            extra = sum(peaks_n[index] for _n, peaks_n in ordered)
            if h1 + extra > self._high:
                raise ValueError(
                    f"{self._channels[index].signal} H1 {h1:.3f} V + "
                    f"harmonics {extra:.3f} V peak is outside "
                    f"[{self._low:g}, {self._high:g}] V"
                )
        wave = _sine_harmonics(frequency, h1_peaks, ordered)
        wave = np.clip(wave, self._low, self._high)
        self._write_wave(wave)

    def _write_peaks(self, peaks: list[float], frequency: float) -> None:
        wave = _sine(frequency, peaks)
        self._write_wave(wave)

    def _write_wave(self, wave) -> None:
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
        self.write_peak_volts({}, frequency=50.0)

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


def _sine_plus_h3(frequency: float, peaks: list[float], h3_pct: float) -> np.ndarray:
    """H1 sine plus integer H3 at ``h3_pct`` of H1 peak."""
    h3_peaks = [peak * (h3_pct / 100.0) for peak in peaks]
    return _sine_harmonics(frequency, peaks, [(3, h3_peaks)])


def _sine_harmonics(
    frequency: float,
    h1_peaks: list[float],
    overtones: list[tuple[int, list[float]]],
) -> np.ndarray:
    """H1 sine plus integer overtones with independent card peaks."""
    sample_count = AC_WAVEFORM_SAMPLES
    sample_rate = AC_SAMPLE_RATE
    ticks = np.arange(sample_count) / sample_rate
    rows = []
    for index, h1 in enumerate(h1_peaks):
        fund = h1 * np.sin(2.0 * math.pi * frequency * ticks)
        for order, peaks_n in overtones:
            amp = peaks_n[index] if index < len(peaks_n) else 0.0
            if amp > 0:
                fund = fund + amp * np.sin(
                    2.0 * math.pi * float(order) * frequency * ticks
                )
        rows.append(fund)
    if len(rows) == 1:
        return rows[0]
    return np.vstack(rows)


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
