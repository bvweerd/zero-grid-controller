---
# Project Architecture

## Directory structure
- `custom_components/zero_grid_controller/` — all integration source code
- `tests/` — pytest test suite
- `manifest.json` — HA integration metadata (domain, version, dependencies)
- `hacs.json` — HACS distribution metadata

## Core modules
- `coordinator.py` — `ZeroGridCoordinator` (`DataUpdateCoordinator`), central update loop (5 s interval), owns PID and estimator state
- `pid.py` — Discrete PID controller with conditional anti-windup and per-cycle integrator freeze
- `estimator.py` — Scalar Recursive Least Squares (RLS) estimator for online system gain identification
- `calibrator.py` — Async step-response calibration routine (measures inverter gain and settling time)
- `array.py` — `ArrayConfig` dataclass, one instance per PV array subentry
- `config_flow.py` — 4-step setup wizard, options flow, and array subentry flow

## Sub-entry model
The integration uses HA sub-entries (`sub_entries=true`). The main entry holds global config (grid sensor, PID params); each PV array is a subentry with its own device. Battery and optimizer mode are optional subentries.

## External dependencies
- `homeassistant` — all HA core APIs
- `pytest-homeassistant-custom-component` — test infrastructure
