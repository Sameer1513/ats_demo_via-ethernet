# BCM-MV Live API

Runtime telemetry for automation / ATE. Recorder writes **`/mnt/live.json`** about once per second; `adc_httpd` serves it over HTTP.

## Endpoints

| Method | URL | Notes |
|--------|-----|--------|
| `GET` | `http://<board-ip>:8080/api/live` | Preferred |
| `GET` | `http://<board-ip>:8080/api/rms` | Same payload as `/api/live` |
| `GET` | `http://<board-ip>:8080/live.json` | Static file alias |

- Port is **8080**, not 80. No auth.
- **503** `{"ok":false,"error":"no live data"}` if recorder has not written yet.
- On-board path: `/mnt/live.json` (atomic `.tmp` + rename).

Config is **not** this document — use `GET/PUT /api/config` or `/mnt/config.json`. Live only embeds a short `rms.channels[]` summary.

## Channel indexing (critical)

| Array | Length | Index |
|-------|--------|--------|
| Analog `rms_*` / `peak_*` / `mode` / `channels` | **6** | `0..5` = hardware channel **id 1..6** |
| `digital` | **5** | `id` 1..5 |

Resolve signal via `rms.channels[i].signal` — do **not** hard-code positions (factory default is often CH4=`iph`, CH5=`imtr`, CH6=`vctrl`).

## Units / conversion for asserts

| Field | Unit |
|-------|------|
| `rms_v[i]`, `peak_v[i]` | **ADC volts** (raw) |
| `rms_eng[i]`, `peak_eng[i]` | **Engineering** after board cal + conversion map |

Configured mode (`rms.channels[i].mode`):

- **`dc`**: published level is signed **window mean** (keeps 4–20 mA polarity).
- **`ac`**: published level is **true RMS**; `peak_*` = max \|sample\| over the same window.

Board cal (examples):

- mV secondary (iph / imtr): `Vsec_mV ≈ Vadc_rms / 10 × 1000`
- Vctrl: `V_pri ≈ Vadc × (150 / 3.32464146)`

`rms.mode[i]` is **runtime detect** (`AC` / `DC` / `off` / `?`).  
`rms.channels[i].mode` is **configured** (`ac` / `dc`) — use this for AC vs DC expectations.

---

## Field reference

### Top-level

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` when payload was written successfully |
| `ts_ms` | int64 | Wall-clock epoch **ms** at export time (not sample timestamp) |

### `timebase`

| Field | Meaning |
|-------|---------|
| `sync_state` | Timebase sync state (uint enum) |
| `epoch_id` | Timebase epoch id |
| `maxerror_us` | Max time error (µs) |
| `seconds_since_sync` | Seconds since last sync |
| `rate_millihz` | Estimated sample rate (millihertz) |
| `frames_lost` | Cumulative lost frames |

### `rms`

| Field | Meaning |
|-------|---------|
| `rms_v[6]` | Per-CH level in **Vadc** (mean if DC cfg, true RMS if AC cfg) |
| `rms_eng[6]` | Same after conversion → eng units from channel config |
| `peak_v[6]` | Max \|Vadc\| over same window |
| `peak_eng[6]` | Peak after conversion (AC sine peaks ≈ RMS×√2; can exceed FS) |
| `mode[6]` | Runtime detect: `"AC"`, `"DC"`, `"off"`, `"?"` |
| `channels[6]` | Config snapshot per CH |

### `rms.channels[i]`

| Field | Meaning |
|-------|---------|
| `id` | 1..6 |
| `signal` | `tc1` / `tc2` / `cc` / `iph` / `imtr` / `vctrl` / `unused` |
| `name` | Display name |
| `enabled` | bool |
| `mode` | Configured `"ac"` or `"dc"` |

### `digital[j]`

| Field | Meaning |
|-------|---------|
| `id` | 1..5 (hw bit + 1) |
| `signal` | e.g. `trip`, `close`, `52a`, `52b`, … |
| `name` | Label |
| `enabled` | Config enabled |
| `active` | Live logical active (after polarity invert mask) |

### `temp`

| Field | Meaning |
|-------|---------|
| `ok` | Sensor read succeeded |
| `celsius` | Board temperature °C (only if `ok`) |
| `sensor` | e.g. `tmp1075` |

### `b7` (breaker / counters / live Iph)

| Field | Meaning |
|-------|---------|
| `breaker_state` | Live from 52A/52B: `closed` / `open` / `travelling` / `unknown` |
| `breaker_state_db` | Last persisted breaker state (falls back to live if empty) |
| `aux_input_failure` | `1` if both 52A and 52B active (aux fail) |
| `aux_input_failure_since_ms` | When aux fail started (ms epoch) |
| `last_confirmed_operation_ms` | Last confirmed operation timestamp |
| `days_since_last_operation` | Days since that op; **-1** if never |
| `trip_total` | Cumulative trip count |
| `backup_trip_total` | Backup trip count |
| `close_total` | Close count |
| `cumulative_operations_total` | All operations |
| `fault_total` | Fault events |
| `fault_interrupt_total` | Fault interrupts |
| `non_fault_interrupt_total` | Non-fault interrupts |
| `motor_run_total_ms` | Accumulated motor run time (ms) |
| `iph_rms_primary` | Iph eng RMS (`rms_eng` of iph hw); `0` if no iph channel |
| `iph_rms_primary_unit` | Iph conversion unit (e.g. `A`) |
| `last_opening_velocity_m_s` | Last opening velocity |
| `last_closing_velocity_m_s` | Last closing velocity |

### `diagnostics`

| Field | Meaning |
|-------|---------|
| `untransmitted_event_loss_total` | COMTRADE FIFO evictions still untransmitted |
| `untransmitted_event_loss_last_ms` | Last loss time |
| `storage_bytes_free` | Free bytes on `/mnt` |
| `storage_bytes_total` | Total bytes on `/mnt` |
| `storage_updated_ms` | When storage stats were refreshed |

### `health`

| Field | Values / meaning |
|-------|------------------|
| `monitoring_degraded` | bool |
| `ram` / `nvm` / `adc` | `"normal"` \| `"fault"` |
| `storage` | `"normal"` \| `"warning"` |
| `mqtt` | `"not_configured"` (MQTT not wired yet) |
| `time_sync` | `"normal"` \| `"holdover"` \| `"unsynced"` |
| `calibration` | `"not_configured"` |
| `firmware` | `"normal"` \| `"degraded"` |
| `aux_input_failure` | `0` / `1` active flag |
| `storage_low` | `0` / `1` |
| `time_unsynced` | `0` / `1` |
| `time_holdover` | `0` / `1` |

---

## Automation tips

1. Poll `/api/live` at ≤ 1 Hz; treat advancing `ts_ms` as liveness.
2. Find channel by `signal`, then index into `rms_*` / `peak_*`.
3. Assert eng with tolerance; for clean sine, peak eng ≈ RMS eng × √2.
4. After config mode / range change, wait ≥ 1–2 s for SIGHUP reload + new window.
5. Do not confuse `rms.mode[]` (detect) with `rms.channels[].mode` (config).

## Source

Payload built in [`app/main/live_export.c`](../app/main/live_export.c); served by [`app/main/httpd.c`](../app/main/httpd.c).
