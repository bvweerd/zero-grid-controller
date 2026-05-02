# Zero Grid Controller
Home Assistant custom integration that keeps net grid power at 0 W by auto-controlling PV inverter output limits via a self-tuning PID controller.

## Commands
- Tests: `python -m pytest tests/ -v`
- Lint: `python -m ruff check custom_components/ tests/`
- Type check: `python -m mypy custom_components/`
- Install deps: `pip install -r requirements_test.txt` (if present) or `pip install pytest-homeassistant-custom-component`

## Structure
- `custom_components/zero_grid_controller/` — integration code
- `tests/` — pytest tests (asyncio_mode=auto)
- `manifest.json` — domain=`zero_grid_controller`, version=`0.1.0`
- `hacs.json` — HACS metadata

## Key modules
- `coordinator.py` — `DataUpdateCoordinator` subclass, runs every 5 s; HA lifecycle glue only
- `control_engine.py` — stateful pure control logic (PID, EWM, distribution, hysteresis); no HA imports
- `sensor_reader.py` — `SensorReader` protocol for decoupled HA state access
- `pid.py` — discrete PID with anti-windup and per-cycle integrator freeze
- `calibrator.py` — async step-response calibration
- `config_flow.py` — wizard + options + array/battery/load subentry flows
- `array.py` — `ArrayConfig` dataclass (one per PV array subentry)
- `battery.py` — `BatteryConfig` dataclass (one per battery subentry)
- `load.py` — `LoadConfig` dataclass (one per controllable load subentry)

## HA conventions
- Use `async def` for all platform setup and I/O operations
- `_LOGGER = logging.getLogger(__name__)` in every module
- Config entries via `config_flow.py`; sub-entries via `sub_entries=true`
- Type hints required on all public functions (mypy strict)
- Ruff: line-length=88, rules E/F/W/I/UP/B/C4/SIM, E501 ignored

## Test conventions
- Framework: `pytest-homeassistant-custom-component`
- Use `hass` fixture for `HomeAssistant` instance
- Use `MockConfigEntry` from `pytest_homeassistant_custom_component.common`
- Add `auto_enable_custom_integrations` autouse fixture per test module

## Compaction: always preserve
- List of modified files
- Test error messages and tracebacks
- Current domain name and version
