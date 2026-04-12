# Zero Grid Controller

A Home Assistant custom integration that keeps your net grid power close to **0 W** by automatically controlling PV inverter output limits — so you never export more than you need to, and never buy more than necessary.

---

## What does it do?

Zero Grid Controller continuously reads your grid power sensor and adjusts one or more PV inverter setpoints to keep the net exchange with the grid as close to zero as possible.

**Signal model:**

```
grid_w = consumption − PV_delivered − battery_net
```

- Positive `grid_w` = drawing from grid → too much consumption or too little PV
- Negative `grid_w` = exporting to grid → too much PV
- Goal: always steer towards `grid_w = 0`

**Control priority:**

1. When exporting (grid < 0): charge batteries first, then curtail PV arrays
2. When importing (grid > 0): open PV arrays first, discharge batteries only when PV is already at maximum

---

## Prerequisites

- Home Assistant 2024.11 or newer (subentries API required)
- A sensor entity that measures your grid power exchange
- At least one PV inverter controllable via a `number` or `switch` entity

---

## Installation

### Via HACS (recommended)

1. Open **HACS** → **Integrations** → **⋮** → **Custom repositories**.
2. Add `https://github.com/bvweerd/Zero-Grid-Controller` as an **Integration**.
3. Search for **Zero Grid Controller** and click **Download**.
4. Restart Home Assistant.

### Manual installation

1. Download or clone this repository.
2. Copy the `custom_components/zero_grid_controller` folder to your `<config>/custom_components/` directory.
3. Restart Home Assistant.

---

## Setup

Go to **Settings → Devices & Services → Add Integration** and search for **Zero Grid Controller**.

### Main settings

- **Grid import sensors**: one or more power sensors that measure how much power you draw from the grid.
- **Grid export sensors**: one or more power sensors that measure how much power you deliver to the grid.
- **Enable switch** (optional): an `input_boolean` or `switch` that must be on for the controller to be active.
- **Deadband**: grid error below this value (W) is ignored — the controller does nothing.
- **EWM filter alpha**: smoothing factor for the grid signal. Lower = smoother but slower.
- **Control aggressiveness**: how aggressively PID gains are set after calibration (cautious / normal / fast).

### Adding PV arrays (subentries)

After setup, go to the integration card and choose **Add entry → PV array**:

**Step 1: type selection**
- **Array name**: a friendly name (e.g. "Roof South").
- **Output type**: percentage (0–100 %), Watts (absolute limit), or On/Off switch.

**Step 2a — numeric output:**
- **Setpoint entity**: the `number.*` or `input_number.*` entity that sets the power limit.
- **Setpoint min / max**: the valid range for the setpoint entity.

**Step 2b — switch output:**
- **Switch entity**: the `switch.*` entity to toggle.
- **Turn on threshold**: grid import above this value (W) turns the switch on.
- **Turn off threshold**: grid export above this value (W) turns the switch off.
- **Minimum on/off time**: debounce time in seconds before switching again.

You can add multiple arrays. The controller distributes corrections proportionally to available headroom.

### Adding a battery (subentry)

Go to the integration card and choose **Add entry → Battery**:

- **Battery name**: a friendly name.
- **Battery power sensor**: negative = charging, positive = discharging.
- **Maximum charge power**: maximum W the battery can absorb.
- **Maximum discharge power**: maximum W the battery can deliver.
- **Battery setpoint entity**: the `number.*` entity to command the battery target power.

---

## Adjusting settings

Go to **Settings → Devices & Services → Zero Grid Controller → Configure**.

The **Control aggressiveness** setting has three positions:

| Setting | Description |
|---------|-------------|
| Cautious | Slow, stable. Suitable for slow inverters or noisy grid measurements. |
| Normal | Balanced. The recommended default. |
| Fast | Quick reaction. May oscillate on slow inverters. |

Changes take effect immediately without restart.

---

## Calibration

Calibration measures the actual inverter response with a per-array power sensor and then auto-computes global PID gains from the combined calibrated numeric arrays.

For numeric arrays, add an **Array power sensor** in the array subentry. The calibrator uses that sensor directly and no longer infers array response from net grid power alone.

Run calibration when the inverter is producing stably enough that a `30% → 20% → 30%` step is visible on the array power sensor:

1. Go to the controller device.
2. Press the **Recalibrate all arrays** button.

Or via service:
```yaml
service: zero_grid_controller.recalibrate
data: {}
```

Calibration runs one array at a time. For each numeric array it:
1. Moves the array to a midpoint workpoint at `30%`.
2. Waits for the array power sensor to stabilise.
3. Steps down to `20%` and measures the downwards response.
4. Steps back up to `30%` and measures the upwards response.
5. Restores the original setpoint.
6. Computes `w_per_unit`, a derived maximum power estimate, and a conservative settling time based on the slowest direction.

Switch arrays are skipped — they have no intermediate setpoint to measure.

Global PID gains are recomputed once per calibration run from the total calibrated numeric plant. A single array calibration result no longer overwrites the global PID on its own.

Calibration confidence is shown in the diagnostics analyzer. Arrays showing `estimated` still use fallback W/unit values and benefit most from running calibration.

---

## Entities

### Main device

| Entity | Description |
|--------|-------------|
| `sensor.*_grid_raw_w` | Unfiltered grid power (W) |
| `sensor.*_grid_filtered_w` | EWM-filtered grid power (W) |
| `sensor.*_pid_output_w` | PID output (W) |
| `sensor.*_status` | Controller status: `active` / `deadband` / `disabled` |
| `number.*_deadband_w` | Deadband (W) |
| `number.*_ewm_alpha` | EWM filter alpha |
| `button.*_reset_pid` | Reset PID integrator |
| `button.*_recalibrate` | Re-run step-response calibration |

### Per PV array (sub-device)

| Entity | Description |
|--------|-------------|
| `sensor.*_setpoint` | Current setpoint value for numeric arrays |
| `binary_sensor.*_setpoint` | Current commanded on/off state for switch arrays |

### Per battery (sub-device)

| Entity | Description |
|--------|-------------|
| `sensor.*_setpoint` | Current commanded setpoint (W) |

---

## Services

| Service | Description |
|---------|-------------|
| `zero_grid_controller.reset_pid` | Reset the PID integrator and derivative history |
| `zero_grid_controller.recalibrate` | Re-run step-response calibration for the configured instance; `entry_id` is optional |

---

## Diagnostics

Download a diagnostics snapshot via **Settings → Devices & Services → Zero Grid Controller → ⋮ → Download diagnostics**.

Open it in the [online analyzer](https://bvweerd.github.io/Zero-Grid-Controller/) to inspect:

- Current control status and grid measurements
- Per-array setpoints, power sensor, W/unit, derived max power, and calibration confidence
- Downward and upward settling times from bidirectional midpoint calibration
- Battery setpoints and capacity
- PID gains, integrator state, and the calibrated numeric arrays currently used as the PID basis
- Actionable recommendations

---

## Frequently asked questions

**Why isn't my inverter running at 100%?**

The controller is actively limiting it to keep grid export near zero. If consumption exceeds PV production, the inverter runs at its maximum and the controller does not restrict it further.

**My inverter responds slowly and the controller oscillates.**

Switch aggressiveness to **Cautious** via **Configure**. If that is not enough, run calibration — it will measure the actual settling time and adjust gains accordingly.

**I have two inverters — can I control both?**

Yes. Add a second PV array via **Add entry → PV array**. Each array gets its own sub-device with an independent setpoint sensor.

**Can I disable the controller temporarily?**

Set an **Enable switch** in the main settings. Turning the switch off puts the controller in safe state (numeric arrays at maximum, batteries at zero).

**When does the battery discharge?**

Only when all numeric PV arrays are already at their maximum setpoint and the grid is still importing. The battery is the last resort for import, not the first.

---

## Architecture notes (for developers)

- **`pid.py`** — Discrete PID with conditional anti-windup and per-cycle integrator freeze.
- **`calibrator.py`** — Async midpoint calibration with direct array power sensors; measures W/unit, derived max power, and directional settling times.
- **`coordinator.py`** — `DataUpdateCoordinator` subclass; runs every 5 s. Owns the 8-step control loop.
- **`array.py`** — `ArrayConfig` dataclass; one per PV array subentry.
- **`battery.py`** — `BatteryConfig` dataclass; one per battery subentry.
- **`config_flow.py`** — Main flow + array subentry flow (numeric/switch) + battery subentry flow.

---

## License

[GPL-3.0](LICENSE)
