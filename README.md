# Zero Grid Controller

A Home Assistant custom integration that keeps your net grid power close to **0 W** by automatically controlling PV inverter output limits — so you never export more than you need to, and never buy more than necessary.

---

## What does it do?

Zero Grid Controller continuously reads your grid power sensor and adjusts one or more PV inverter setpoints to keep the net exchange with the grid as close to zero as possible. It does this using a self-tuning PID controller: no manual configuration required for day-to-day operation.

**Signal model:**

```
grid_w = consumption − PV_delivered − battery_net
```

- Positive `grid_w` = drawing from the grid → too much consumption or too little PV
- Negative `grid_w` = exporting to the grid → too much PV
- Goal: always steer towards `grid_w = 0`

---

## Architecture — three layers for three user types

The integration is designed so that technical parameters like Kp, Ki, Kd, and settling time are **never visible** unless you explicitly enable Expert mode.

| Layer | Who it's for | What you see |
|-------|-------------|-------------|
| **Wizard** | Everyone | 4 plain-English setup steps |
| **Self-tuning** | Default after setup | One "response speed" slider |
| **Expert mode** | Advanced users | All PID + estimator parameters |

---

## Prerequisites

- Home Assistant 2024.11 or newer (subentries API required)
- A sensor entity in Home Assistant that measures your grid power exchange
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
2. Copy the `custom_components/zero_grid_controller` folder to your
   `<config>/custom_components/` directory.
3. Restart Home Assistant.

---

## Setup

Go to **Settings → Devices & Services → Add Integration** and search for **Zero Grid Controller**. The setup wizard has four short steps:

### Step 1 — Grid sensor

Select the sensor that measures your grid power exchange.

- **Single net sensor**: one sensor where positive = importing (or exporting if you invert the sign).
- **Two separate sensors**: one for import power, one for export power.

### Step 2 — PV arrays

Add one or more PV inverters you want the controller to manage:

- **Array name**: a friendly name (e.g. "Roof South").
- **Setpoint entity**: the `number.*` or `switch.*` entity that controls the inverter output limit.
- **Output type**: percentage (0–100 %), absolute Watts, or On/Off switch.
- **PV power sensor** (optional): if provided, improves cloud detection.
- **Inverter speed**: how fast your inverter reacts. This sets the settling time correctly from day one.

You can add multiple arrays. The controller distributes corrections proportionally to available headroom.

### Step 3 — Battery (optional)

If you have a home battery, configure it so the controller can account for it:

- Battery power sensor (positive = charging).
- Maximum charge and discharge power.
- Optionally: let Zero Grid Controller also write the battery target setpoint.

### Step 4 — Optimizer integration (optional)

If you use **Battery Controller** or another optimizer, you can connect its mode entity here. Map each mode value to one of:

- **Full control** (`active`): Zero Grid Controller adjusts setpoints freely.
- **Limit only** (`passive`): Only tighten limits, never open them.
- **Hands off** (`disabled`): Zero Grid Controller does nothing.

After completing the wizard, the integration starts with conservative default values and begins learning automatically.

---

## Adjusting response speed

Go to **Settings → Devices & Services → Zero Grid Controller → Configure**.

The **Response speed** slider has three positions:

| Setting | Description |
|---------|-------------|
| Cautious | Slow, stable. Suitable for slow inverters or unstable grid measurements. |
| Normal | Balanced. The recommended default. |
| Fast | Quick reaction. May oscillate on slow inverters. |

---

## How does the self-tuning work?

Think of it like a thermostat that calibrates itself.

When you first set up the integration, it uses conservative default parameters. Over time, it observes how the grid reacts to each setpoint change and uses a **Recursive Least Squares (RLS)** estimator to measure the actual system gain and response time. It then gradually adjusts the PID gains to match your specific inverter behaviour.

The `sensor.zgc_learning_status` entity shows:
- **"Learning..."** — the estimator is still collecting data (first ~30 cycles).
- **"Calibrated ✓"** — the estimator has converged and is actively auto-tuning.

The auto-tuner only runs when **Expert mode is off**. If you manually set PID gains in expert mode, the auto-tuner steps aside.

---

## Entities created

### Main device

| Entity | Enabled | Description |
|--------|---------|-------------|
| `sensor.zgc_grid_filtered_w` | Yes | Filtered grid power (W) |
| `sensor.zgc_mode` | Yes | Controller mode: active/passive/disabled/deadband/cloud_shadow/saturation |
| `sensor.zgc_learning_status` | Yes | Self-tuning status |
| `sensor.zgc_grid_raw_w` | No* | Unfiltered grid reading |
| `sensor.zgc_pid_output_w` | No* | Total PID output (W) |
| `sensor.zgc_pid_p_w` | No* | P-term (W) |
| `sensor.zgc_pid_i_w` | No* | I-term (W) |
| `sensor.zgc_pid_d_w` | No* | D-term (W) |
| `sensor.zgc_battery_clipping` | No* | Battery at max charge power |

*Disabled by default. Enable in Settings → Entities.

### Per array (sub-device)

| Entity | Enabled | Description |
|--------|---------|-------------|
| `sensor.zgc_<array>_setpoint` | Yes | Current setpoint value |
| `switch.zgc_<array>_enabled` | Yes | Enable/disable this array |
| `sensor.zgc_<array>_array_clipping` | No* | PV output pressing against the limit |
| `sensor.zgc_<array>_array_gain_k` | No* | Estimated system gain K |
| `sensor.zgc_<array>_array_calibration` | No* | Calibration confidence |

### Expert mode entities (hidden until expert mode is enabled)

| Entity | Range | Description |
|--------|-------|-------------|
| `number.zgc_kp` | 0.001–10 | Proportional gain |
| `number.zgc_ki` | 0–1 | Integral gain |
| `number.zgc_kd` | 0–1 | Derivative gain |
| `number.zgc_ewm_alpha` | 0.05–1.0 | EWM filter smoothing factor |
| `number.zgc_deadband_w` | 0–500 | Deadband (W) |
| `number.zgc_output_max_w` | 100–50000 | Maximum PID output (W) |
| `number.zgc_<array>_settling_time_s` | 2–120 | Inverter settling time (s) |
| `number.zgc_<array>_w_per_unit` | 0.1–1000 | W per setpoint unit |

---

## Services

| Service | Description |
|---------|-------------|
| `zero_grid_controller.reset_pid` | Reset the PID integrator |
| `zero_grid_controller.recalibrate` | Re-run step-response calibration |
| `zero_grid_controller.set_response_speed` | Set response speed (cautious/normal/fast) |
| `zero_grid_controller.override_setpoint` | Force a setpoint for up to 5 minutes |

---

## Integration with Battery Controller

If you use the [Battery Controller](https://github.com/bvweerd/battery_controller) integration:

1. In the Zero Grid Controller setup wizard, select **"Use an optimizer mode entity"**.
2. Select the Battery Controller mode entity (e.g. `sensor.battery_controller_mode`).
3. Map each Battery Controller mode to the corresponding Zero Grid Controller behaviour:
   - When Battery Controller is actively charging/discharging → **Full control**
   - When Battery Controller is in standby → **Full control**
   - When Battery Controller writes its own grid target → **Hands off**

This prevents the two controllers from fighting each other.

---

## Expert mode

Expert mode exposes all internal PID and estimator parameters as Home Assistant entities. Enable it via:

**Settings → Devices & Services → Zero Grid Controller → Configure → Expert mode**

> **Warning:** When expert mode is active, the online auto-tuner is disabled. You are fully responsible for the stability of the control loop. Start with Kp=0.3, Ki=0.01, Kd=0 and increase carefully.

Key parameters:

| Parameter | Effect |
|-----------|--------|
| **Kp** | Proportional response. Higher = faster reaction, more oscillation risk. |
| **Ki** | Integral action. Removes steady-state offset. Too high → oscillation. |
| **Kd** | Derivative. Dampens overshoot. Usually kept near 0. |
| **EWM alpha** | Low-pass filter strength. Lower = more smoothing but slower response. |
| **Deadband** | Grid error (W) below which the controller does nothing. |
| **Settling time** | How long to wait after each setpoint change before sending another. |
| **W per unit** | System gain. How many Watts does the grid change per unit of setpoint? |

---

## Re-running calibration

Calibration measures the actual inverter response time and gain. Run it on a sunny day for best results:

**Settings → Devices & Services → Zero Grid Controller → Configure → Re-run calibration**

Or via service:
```yaml
service: zero_grid_controller.recalibrate
data: {}
```

---

## Removal

1. Go to **Settings → Devices & Services**.
2. Find **Zero Grid Controller** and click **⋮ → Delete**.
3. If using HACS: HACS → Integrations → Zero Grid Controller → Remove.
4. Remove the `custom_components/zero_grid_controller` folder from your HA config directory.
5. Restart Home Assistant.

---

## Frequently asked questions

**Why isn't my inverter running at 100%?**

The controller is actively limiting it to keep your grid exchange near zero. If you're not producing enough to cover your consumption, the inverter will run at maximum (100%) and the controller won't restrict it further.

**What does "Learning..." mean?**

The RLS estimator needs approximately 30 update cycles to converge. During this time it uses default parameters, which are conservative. After convergence the auto-tuner takes over and optimises gains for your specific setup.

**My battery is full but the integration is still exporting to the grid.**

This is the "saturation" state. The battery is at full charge and can't absorb more. The PV arrays are generating more than consumption. In this case the controller opens the inverter limits (reduces clipping), which is correct behaviour — there is nothing left to absorb the surplus.

**The controller is oscillating.**

Reduce the response speed to "Cautious" via the options menu. If that doesn't help, enable Expert mode and reduce Kp. Also check that the settling time is set correctly for your inverter.

**I have two inverters — can I control both?**

Yes. Add a second PV array in the wizard (or via Settings → Devices & Services → Zero Grid Controller → Add entry). Each array gets its own subentry device with independent setpoint and enable switch.

**Can I use this without solar panels?**

Not in a useful way — the controller is designed to limit PV output to zero-export. It has no mechanism to increase production.

---

## Architecture notes (for developers)

- **`pid.py`** — Custom discrete PID with conditional anti-windup and per-cycle integrator freeze.
- **`estimator.py`** — Scalar RLS estimator for online system gain identification.
- **`calibrator.py`** — Async step-response measurement at setup time.
- **`coordinator.py`** — `DataUpdateCoordinator` subclass; runs every 5 s.
- **`array.py`** — `ArrayConfig` dataclass; one per PV array subentry.
- **`config_flow.py`** — Wizard flow + options flow + array subentry flow.

---

## License

[GPL-3.0](LICENSE)
