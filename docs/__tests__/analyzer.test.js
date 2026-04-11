'use strict';

const {
  unwrapDiagnostics,
  deriveSummary,
  generateTips,
} = require('../analyzer');

function samplePayload() {
  return {
    data: {
      status: 'active',
      grid_raw_w: -3716.0,
      grid_filtered_w: -3813.3,
      pid_output_w: 2855.5,
      setpoints: { Zuid: 0.0, West: 45.0 },
      battery_setpoints: { 'Thuis accu': -2500.0 },
      pid: { kp: 0.0026, ki: 0.00013, kd: 0.0, integral: 12847.3 },
      arrays: [
        {
          name: 'Zuid',
          output_type: 'percent',
          setpoint_min: 0.0,
          setpoint_max: 100.0,
          w_per_unit: 38.5,
          settling_time_s: 12,
          calibration_confidence: 'measured',
        },
        {
          name: 'West',
          output_type: 'percent',
          setpoint_min: 0.0,
          setpoint_max: 100.0,
          w_per_unit: 10.0,
          settling_time_s: 15,
          calibration_confidence: 'estimated',
        },
      ],
      batteries: [{ name: 'Thuis accu', max_charge_w: 3600, max_discharge_w: 3600 }],
      config: { deadband_w: 20.0, ewm_alpha: 0.3, aggressiveness: 'normal', enable_entity: null },
    },
  };
}

describe('unwrapDiagnostics', () => {
  test('accepts HA wrapper payload', () => {
    const result = unwrapDiagnostics(samplePayload());
    expect(result.status).toBe('active');
  });

  test('accepts raw data object', () => {
    const result = unwrapDiagnostics({ status: 'deadband' });
    expect(result.status).toBe('deadband');
  });

  test('throws on non-object input', () => {
    expect(() => unwrapDiagnostics(null)).toThrow();
  });
});

describe('deriveSummary', () => {
  test('derives key counts from diagnostics', () => {
    const summary = deriveSummary(unwrapDiagnostics(samplePayload()));
    expect(summary.status).toBe('active');
    expect(summary.gridFilteredW).toBeCloseTo(-3813.3);
    expect(summary.arrayCount).toBe(2);
    expect(summary.uncalibratedCount).toBe(1);
    expect(summary.batteryCount).toBe(1);
  });

  test('handles missing arrays gracefully', () => {
    const summary = deriveSummary({ status: 'disabled' });
    expect(summary.arrayCount).toBe(0);
    expect(summary.uncalibratedCount).toBe(0);
  });
});

describe('generateTips', () => {
  test('emits tip for disabled controller', () => {
    const tips = generateTips({ status: 'disabled', arrays: [], config: {} });
    const titles = tips.map((t) => t.title);
    expect(titles).toContain('Controller is disabled');
  });

  test('emits tip for uncalibrated arrays', () => {
    const tips = generateTips(unwrapDiagnostics(samplePayload()));
    const titles = tips.map((t) => t.title);
    expect(titles).toContain('Arrays not yet calibrated');
  });

  test('emits tip for missing grid measurement', () => {
    const tips = generateTips({ status: 'disabled', grid_filtered_w: null, arrays: [], config: {} });
    const titles = tips.map((t) => t.title);
    expect(titles).toContain('No grid measurement available');
  });

  test('returns ok tip when everything is fine', () => {
    const tips = generateTips({
      status: 'active',
      grid_filtered_w: -5.0,
      arrays: [{ name: 'Zuid', calibration_confidence: 'measured' }],
      config: { deadband_w: 20 },
    });
    expect(tips).toHaveLength(1);
    expect(tips[0].level).toBe('ok');
  });

  test('emits tip for deadband status', () => {
    const tips = generateTips({
      status: 'deadband',
      grid_filtered_w: 3.0,
      arrays: [],
      config: { deadband_w: 20 },
    });
    const titles = tips.map((t) => t.title);
    expect(titles).toContain('Grid is within deadband');
  });
});
