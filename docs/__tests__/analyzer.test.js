'use strict';

const {
  unwrapDiagnostics,
  deriveSummary,
  buildSeries,
  generateTips,
} = require('../analyzer');

function samplePayload() {
  return {
    data: {
      control_state: {
        mode: 'active',
        status: 'saturation',
        grid_filtered_w: 250,
        residual_w: 180,
        battery_clipping: false,
      },
      repair_issues: [{ issue_id: 'grid_sensor_stale' }],
      grid_inputs: [
        { entity_id: 'sensor.grid_import', role: 'import', status: 'stale', age_s: 18.2 },
        { entity_id: 'sensor.grid_export', role: 'export', status: 'ok', age_s: 0.2 },
      ],
      batteries: [
        { name: 'Home Battery', unresponsive: true, current_setpoint_w: -500, current_power_w: -40, measured_response_factor: 0.2 },
      ],
      health: {
        safe_state_applied: true,
        mode_guard: { enabled: true, status: 'unmapped' },
        calibration: { unreliable_arrays: ['Roof West'] },
      },
      history: {
        control_cycle_log: [
          { timestamp: '2026-04-10T10:00:00Z', grid_raw_w: 300, grid_filtered_w: 250, residual_w: 180, pid_output_w: -40 },
          { timestamp: '2026-04-10T10:00:05Z', grid_raw_w: 280, grid_filtered_w: 240, residual_w: 160, pid_output_w: -35 },
        ],
        battery_response_log: [
          { timestamp: '2026-04-10T10:00:04Z', commanded_w: -500, actual_w: -40 },
        ],
      },
    },
  };
}

describe('unwrapDiagnostics', () => {
  test('accepts HA wrapper payload', () => {
    const result = unwrapDiagnostics(samplePayload());
    expect(result.control_state.mode).toBe('active');
  });

  test('accepts raw data object', () => {
    const result = unwrapDiagnostics({ control_state: { mode: 'passive' } });
    expect(result.control_state.mode).toBe('passive');
  });
});

describe('deriveSummary', () => {
  test('derives key counts from diagnostics', () => {
    const summary = deriveSummary(unwrapDiagnostics(samplePayload()));
    expect(summary.mode).toBe('active');
    expect(summary.activeRepairs).toBe(1);
    expect(summary.staleInputs).toBe(1);
    expect(summary.unresponsiveBatteries).toBe(1);
    expect(summary.safeStateApplied).toBe(true);
  });
});

describe('buildSeries', () => {
  test('builds chart series from history logs', () => {
    const series = buildSeries(unwrapDiagnostics(samplePayload()));
    expect(series.labels).toHaveLength(2);
    expect(series.gridFiltered[0]).toBe(250);
    expect(series.batteryCommanded[0]).toBe(-500);
  });
});

describe('generateTips', () => {
  test('emits high-signal recommendations for detected faults', () => {
    const tips = generateTips(unwrapDiagnostics(samplePayload()));
    const titles = tips.map((tip) => tip.title);
    expect(titles).toContain('Grid sensor updates are too old');
    expect(titles).toContain('Battery response mismatch');
    expect(titles).toContain('Fail-safe state is active');
  });

  test('returns an ok tip when nothing stands out', () => {
    const tips = generateTips({
      control_state: { mode: 'active', status: 'active' },
      grid_inputs: [{ entity_id: 'sensor.grid', status: 'ok' }],
      batteries: [{ name: 'Battery', unresponsive: false }],
      health: { calibration: { unreliable_arrays: [] }, mode_guard: { enabled: false }, safe_state_applied: false },
    });
    expect(tips).toHaveLength(1);
    expect(tips[0].level).toBe('ok');
  });
});
