# BCM-MV Live API

Runtime telemetry for automation / ATE. Recorder writes **`/mnt/live.json`** about once per second; `adc_httpd` serves it over HTTP.

## Endpoints

| Method | URL | Notes |
|--------|-----|--------|
| `GET` | `http://<board-ip>:8080/api/version` | `ok`, `package` (first line of `/etc/bcm-mv/version`, else the built-in app version), `kernel` (`uname -r`) |
| `GET` | `http://<board-ip>:8080/api/live` | Preferred |
| `GET` | `http://<board-ip>:8080/api/rms` | Same payload as `/api/live` |
| `GET` | `http://<board-ip>:8080/api/journal` | Alarm/diagnostic history from SQLite (`limit`, optional `before_ms`) |

- Port is **8080**, not 80. No auth.
- **503** `{"ok":false,"error":"no live data"}` if recorder has not written yet.
- On-board path: `/mnt/live.json` (write `.tmp`, then rename). `adc_httpd` reads that file for `/api/live` and `/api/rms`. It does not serve it as `/live.json`. A `GET /live.json` is an ordinary static file from `/mnt/web`.

Config is **not** this document — use `GET/PUT /api/config` (`/mnt/config.json`).
Factory cal coeffs are separate: `GET/PUT /api/calibration` (`/mnt/calibration.json`);
recorder applies them when computing live `rms_eng`. Live only embeds a short `rms.channels[]` summary.

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
| `last_confirmed_operation_ms` | `counters.last_confirmed_operation_ms`. A timed-out trip or close does not move it |
| `days_since_last_operation` | Fractional days since that confirmed timestamp (4 decimal places); **-1** if none, or if the timestamp is not in the past |
| `trip_total` | Confirmed main trips only. A timed-out trip is stored and not counted |
| `backup_trip_total` | Confirmed backup trips only |
| `close_total` | Confirmed closes only |
| `cumulative_operations_total` | Confirmed trip + backup + close. Fault events are not included |
| `fault_total` | Fault events (every saved fault, not gated on aux confirmation) |
| `fault_interrupt_total` | Fault interrupts |
| `non_fault_interrupt_total` | Non-fault interrupts |
| `motor_run_total_ms` | Accumulated motor run time (ms) from **`cbm.motor_runtime`** meter (completed cycles) |
| `motor_run_count` | Completed motor runs |
| `motor_running` | `true` while a run is in progress |
| `motor_run_elapsed_ms` | Elapsed ms of current run (0 if idle) |
| `motor_run_last_ms` | Last completed run duration (ms) |
| `motor_run_avg_last_n_ms` | Mean of last N completed runs (`avg_last_n` in config) |
| `motor_run_starts_24h` | Run starts in rolling 24 h (`motor_runs` table) |
| `motor_inrush_last_peak` | Inrush peak from last completed run (imtr eng.) |
| `motor_i2t_last` | Motor ∫imtr² dt from last completed run (imtr eng.²·s) |
| `iph_i2t_total_a2s` | Cumulative **Iph contact wear** I²t (`counters.i2t_total`, A²·s); incremented on fault/trip/backup trip events |
| `iph_rms_primary` | Iph eng RMS (`rms_eng` of iph hw); `0` if no iph channel |
| `iph_rms_primary_unit` | Iph conversion unit (e.g. `A`) |
| `last_opening_velocity_m_s` | Last opening velocity (m/s). Counter value, or, when that is 0 and `stroke_length_m` &gt; 0, the newest recent trip/backup estimate |
| `last_closing_velocity_m_s` | Last closing velocity (m/s). Same rule, from the newest recent close |

Per-event timing and RMS tables use **`operations`** (below). `last_event_*` fields were removed from live export.

### `operations` (RMS event tables)

| Child | Meaning |
|-------|---------|
| `summary` | Live breaker state, last operation time, days since, last est. velocities, current `stroke_length_m` from config |
| `recent` | Up to **5** newest events (`ORDER BY ts_ms DESC`), including timed-out trip/close/backup rows. The object has no `end_not_confirmed` field |
| `totals` | Lifetime counts. Trip, close, backup, and cumulative match the confirmed-only `b7` totals. `fault` counts every saved fault |

#### `operations.summary`

| Field | Meaning |
|-------|---------|
| `breaker_state` | Same as `b7.breaker_state` (live 52A/52B) |
| `last_operation_ms` | Same value as `b7.last_confirmed_operation_ms` |
| `days_since_last_operation` | Same value as `b7.days_since_last_operation` |
| `last_opening_velocity_m_s` | Same value as `b7.last_opening_velocity_m_s` |
| `last_closing_velocity_m_s` | Same value as `b7.last_closing_velocity_m_s` |
| `stroke_length_m` | `cbm.installation.stroke_length_m` at export time |

#### `operations.recent[]`

| Field | Meaning |
|-------|---------|
| `event_num` | COMTRADE index from `comtrade_base` (e.g. `event_00160` → **160**), not SQLite `id` |
| `ts_ms` | Event timestamp (epoch ms) |
| `rec_slot` | `trip` / `backup_trip` / `close` / … |
| `trigger_reason` | Recorder trigger string |
| `trip_time_ms` | Trip cmd → **52A↓** (trip/backup); `0` otherwise |
| `trip_travel_ms` | **52A↓** → **52B↑** (trip/backup); `0` otherwise |
| `total_trip1_ms` | Trip cmd → **52B↑** when `rec_slot` = `trip`; else `0` |
| `total_trip2_ms` | Backup cmd → **52B↑** when `rec_slot` = `backup_trip`; else `0` |
| `close_time_ms` | Close cmd → **52B↓** on close; `0` otherwise |
| `close_travel_ms` | **52B↓** → **52A↑** on close; `0` otherwise |
| `total_close_ms` | Close cmd → **52A↑** on close; `0` otherwise |
| `travel_ms` | Total operation time snapshot at save (`total_trip1_ms`, `total_trip2_ms`, or `total_close_ms`); used for velocity |
| `arcing_time_ms` | Arc duration when 52A fell inside the capture; otherwise `0` |
| `iph_peak` | Maximum absolute Iph over the capture (`events.iph_peak`) |
| `iph_rms` | Iph RMS over the same capture (`events.iph_rms`) |
| `i2t_increment` | I²t stored for the event; `0` when 52A did not fall inside the capture |
| `stroke_length_m` | Stroke snapshot at save (v19+); else current config at export |
| `est_velocity_m_s` | `stroke_length_m / (travel_ms / 1000)` when both &gt; 0; else `0` |

**UI column rules (RMS recent table):** Trip/close **time** = `trip_time_ms` or `close_time_ms`; **Travel** = `trip_travel_ms` or `close_travel_ms`; **Total** = `total_trip1_ms` / `total_trip2_ms` / `total_close_ms`. **Fault peak** is `iph_peak` on a `fault` row. **Arc** and **I²t** stay blank when `arcing_time_ms` and `i2t_increment` are 0. Est. velocity uses `travel_ms` (total).

#### `operations.totals`

| Field | Meaning |
|-------|---------|
| `trip` | `trip_total` |
| `close` | `close_total` |
| `backup_trip` | `backup_trip_total` |
| `cumulative` | `cumulative_operations_total` |
| `fault` | `fault_total` |

### `diagnostics`

| Field | Meaning |
|-------|---------|
| `untransmitted_event_loss_total` | COMTRADE FIFO evictions still untransmitted |
| `untransmitted_event_loss_last_ms` | Last loss time |
| `storage_bytes_free` | Free bytes on `/mnt` |
| `storage_bytes_total` | Total bytes on `/mnt` |
| `storage_updated_ms` | When storage stats were refreshed |
| `stored_event_count` | Events whose COMTRADE file is still on disk (`comtrade_files_deleted = 0`). Not the raw SQLite row count |

### `health`

| Field | Values / meaning |
|-------|------------------|
| `monitoring_degraded` | bool |
| `ram` / `nvm` / `adc` | `"normal"` \| `"fault"` |
| `storage` | `"normal"` \| `"warning"` |
| `mqtt` | `"not_configured"` (MQTT not wired yet) |
| `time_sync` | `"normal"` \| `"holdover"` \| `"unsynced"` |
| `calibration` | `"not_configured"` \| `"partial"` \| `"valid"` \| `"fault"` (corrupt / out-of-range cal) |
| `cal_fault` | `0` / `1` — CRC or range fault on `calibration.json` |
| `firmware` | `"normal"` \| `"degraded"` |
| `aux_input_failure` | `0` / `1` active flag |
| `storage_low` | `0` / `1` |
| `time_unsynced` | `0` / `1` |
| `time_holdover` | `0` / `1` |

---

## `GET /api/journal`

Reads **`diagnostic_log`** (same DB as recorder, `-d` on `adc_httpd`).

| Query | Default | Max |
|-------|---------|-----|
| `limit` | 50 | 200 |
| `before_ms` | (none) | pagination: rows with `ts_ms` &lt; value |

Response:

```json
{
  "ok": true,
  "items": [
    {
      "id": 1,
      "ts_ms": 1728036000123,
      "severity": "fault|warning|alarm|info",
      "action": "raised|cleared|event",
      "code": "aux_input_failure",
      "message": "52A/52B same state"
    }
  ]
}
```

**503** `{"ok":false,"error":"no database"}` when httpd has no DB. **500** `db read failed` or `oom` if the query cannot be built.

The feed is `diagnostic_log` only. Operation saves are inserted there too, including a timed-out trip/close/backup, with codes `event.trip`, `event.backup_trip`, `event.close`, `event.fault`, `event.motor_run`. The message says the event completed; it does not repeat `end_not_confirmed`.

Other codes: CBM `cbm.opening_time`, …; RMS `rms.<signal>.high` / `.low`; aux assert message is `52A/52B same state`; startup partial-COMTRADE removal is `comtrade_partial_discarded`. Per-channel ADC saturation is `channel_saturated` (`<signal> saturated. Make sure the connection is proper.`, then `<signal> recovered from saturation`). Open, short, and reverse use this same warning.

---

## Automation tips

1. Poll `/api/live` at ≤ 1 Hz; treat advancing `ts_ms` as liveness.
2. Find channel by `signal`, then index into `rms_*` / `peak_*`.
3. Assert eng with tolerance; for clean sine, peak eng ≈ RMS eng × √2.
4. After config mode / range change, wait ≥ 1–2 s for SIGHUP reload + new window.
5. Do not confuse `rms.mode[]` (detect) with `rms.channels[].mode` (config).

## Source

Payload built in [`app/main/live_export.c`](../app/main/live_export.c); served by [`app/main/httpd.c`](../app/main/httpd.c).
