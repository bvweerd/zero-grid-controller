(function (global) {
  'use strict';

  function unwrapDiagnostics(payload) {
    if (!payload || typeof payload !== 'object') {
      throw new Error('Expected a diagnostics object');
    }
    if (payload.data && typeof payload.data === 'object') {
      return payload.data;
    }
    return payload;
  }

  function toArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function deriveSummary(data) {
    const health = data.health || {};
    const control = data.control_state || data.coordinator || {};
    const repairIssues = toArray(data.repair_issues);
    const batteries = toArray(data.batteries || health.batteries);
    const gridInputs = toArray(data.grid_inputs || health.grid_inputs);
    const calibration = health.calibration || {};
    return {
      mode: control.mode || 'unknown',
      status: control.status || 'unknown',
      gridFilteredW: control.grid_filtered_w ?? null,
      residualW: control.residual_w ?? null,
      batteryClipping: Boolean(control.battery_clipping),
      activeRepairs: repairIssues.length,
      staleInputs: gridInputs.filter((item) => item.status === 'stale').length,
      invalidInputs: gridInputs.filter((item) => item.status !== 'ok').length,
      unresponsiveBatteries: batteries.filter((item) => item.unresponsive).length,
      unreliableArrays: toArray(calibration.unreliable_arrays).length,
      safeStateApplied: Boolean(health.safe_state_applied),
    };
  }

  function buildSeries(data) {
    const cycles = toArray(data.history && data.history.control_cycle_log);
    const batteryEvents = toArray(data.history && data.history.battery_response_log);
    return {
      labels: cycles.map((row) => row.timestamp || row.cycle),
      gridRaw: cycles.map((row) => row.grid_raw_w ?? null),
      gridFiltered: cycles.map((row) => row.grid_filtered_w ?? null),
      residual: cycles.map((row) => row.residual_w ?? null),
      pidOutput: cycles.map((row) => row.pid_output_w ?? null),
      batteryResponseLabels: batteryEvents.map((row) => row.timestamp || row.battery),
      batteryCommanded: batteryEvents.map((row) => row.commanded_w ?? null),
      batteryActual: batteryEvents.map((row) => row.actual_w ?? null),
    };
  }

  function generateTips(data) {
    const tips = [];
    const health = data.health || {};
    const gridInputs = toArray(data.grid_inputs || health.grid_inputs);
    const batteries = toArray(data.batteries || health.batteries);
    const calibration = health.calibration || {};
    const modeGuard = health.mode_guard || {};
    const control = data.control_state || data.coordinator || {};

    const staleInputs = gridInputs.filter((item) => item.status === 'stale');
    if (staleInputs.length) {
      tips.push({
        level: 'warn',
        title: 'Grid sensor updates are too old',
        body: `Stale sensors: ${staleInputs.map((item) => item.entity_id).join(', ')}. Check the source integration and sensor freshness window.`,
      });
    }

    const invalidInputs = gridInputs.filter((item) => item.status === 'unavailable' || item.status === 'non_numeric');
    if (invalidInputs.length) {
      tips.push({
        level: 'err',
        title: 'Grid measurements are unusable',
        body: `Unavailable or invalid sensors: ${invalidInputs.map((item) => item.entity_id).join(', ')}. Zero Grid Controller cannot regulate without them.`,
      });
    }

    const unresponsive = batteries.filter((item) => item.unresponsive);
    if (unresponsive.length) {
      tips.push({
        level: 'warn',
        title: 'Battery response mismatch',
        body: `Check sign convention, setpoint entity and settling time for ${unresponsive.map((item) => item.name).join(', ')}.`,
      });
    }

    if (modeGuard.enabled && modeGuard.status && modeGuard.status !== 'ok') {
      tips.push({
        level: 'warn',
        title: 'Mode guard is blocking predictable control',
        body: `Mode guard status is "${modeGuard.status}". Review entity state and mapping.`,
      });
    }

    if (control.status === 'saturation') {
      tips.push({
        level: 'info',
        title: 'Controller is saturating',
        body: 'PV headroom or battery power is insufficient for the current grid error. Review output limits, deadband and available actuator capacity.',
      });
    }

    if (control.status === 'cloud_shadow') {
      tips.push({
        level: 'info',
        title: 'Cloud-shadow logic is suppressing PID action',
        body: 'This usually indicates sudden PV drops while setpoints are still settling. Verify PV power sensors if this happens frequently.',
      });
    }

    if (health.safe_state_applied) {
      tips.push({
        level: 'err',
        title: 'Fail-safe state is active',
        body: 'Normal control is currently blocked. Inspect active repair issues and the latest sensor health events first.',
      });
    }

    if (toArray(calibration.unreliable_arrays).length) {
      tips.push({
        level: 'info',
        title: 'Estimator still learning',
        body: `Unreliable arrays: ${calibration.unreliable_arrays.join(', ')}. Stable PV conditions and accurate PV power sensors improve convergence.`,
      });
    }

    if (!tips.length) {
      tips.push({
        level: 'ok',
        title: 'No obvious control problems detected',
        body: 'The current diagnostics snapshot does not show a clear blocking issue.',
      });
    }
    return tips;
  }

  function createBadgeClass(level) {
    return ({
      ok: 'badge-ok',
      info: 'badge-info',
      warn: 'badge-warn',
      err: 'badge-err',
    })[level] || 'badge-info';
  }

  function renderUI(data) {
    if (typeof document === 'undefined') {
      return;
    }
    const summary = deriveSummary(data);
    const series = buildSeries(data);
    const tips = generateTips(data);

    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('parse-error').classList.add('hidden');
    document.getElementById('summary-mode').textContent = summary.mode;
    document.getElementById('summary-status').textContent = summary.status;
    document.getElementById('summary-grid').textContent =
      summary.gridFilteredW == null ? '—' : `${summary.gridFilteredW.toFixed(1)} W`;
    document.getElementById('summary-residual').textContent =
      summary.residualW == null ? '—' : `${summary.residualW.toFixed(1)} W`;
    document.getElementById('summary-repairs').textContent = String(summary.activeRepairs);
    document.getElementById('summary-batteries').textContent = String(summary.unresponsiveBatteries);

    const healthList = document.getElementById('health-list');
    const health = data.health || {};
    const gridInputs = toArray(data.grid_inputs || health.grid_inputs);
    healthList.innerHTML = '';
    [
      ['Safe state', String(Boolean(summary.safeStateApplied))],
      ['Invalid inputs', String(summary.invalidInputs)],
      ['Unreliable arrays', String(summary.unreliableArrays)],
      ['Mode guard', (health.mode_guard && health.mode_guard.status) || 'disabled'],
    ].forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'kv-row';
      row.innerHTML = `<span class="kv-label">${label}</span><span class="kv-value">${value}</span>`;
      healthList.appendChild(row);
    });

    const inputsTable = document.getElementById('grid-input-table');
    inputsTable.innerHTML = '';
    gridInputs.forEach((item) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${item.entity_id || '—'}</td>
        <td>${item.role || '—'}</td>
        <td><span class="badge ${createBadgeClass(item.status === 'ok' ? 'ok' : item.status === 'stale' ? 'warn' : 'err')}">${item.status}</span></td>
        <td>${item.value == null ? '—' : item.value}</td>
        <td>${item.age_s == null ? '—' : item.age_s}</td>`;
      inputsTable.appendChild(row);
    });

    const batteryTable = document.getElementById('battery-table');
    batteryTable.innerHTML = '';
    toArray(data.batteries || health.batteries).forEach((item) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${item.name}</td>
        <td>${item.current_setpoint_w == null ? '—' : item.current_setpoint_w}</td>
        <td>${item.current_power_w == null ? '—' : item.current_power_w}</td>
        <td>${item.measured_response_factor == null ? '—' : item.measured_response_factor}</td>
        <td><span class="badge ${createBadgeClass(item.unresponsive ? 'err' : 'ok')}">${item.unresponsive ? 'unresponsive' : 'ok'}</span></td>`;
      batteryTable.appendChild(row);
    });

    const tipsContainer = document.getElementById('tips');
    tipsContainer.innerHTML = '';
    tips.forEach((tip) => {
      const item = document.createElement('div');
      item.className = `tip tip-${tip.level}`;
      item.innerHTML = `<strong>${tip.title}</strong><div>${tip.body}</div>`;
      tipsContainer.appendChild(item);
    });

    renderChart('chart-grid', series.labels, [
      { label: 'Grid raw W', data: series.gridRaw, borderColor: '#1d4ed8' },
      { label: 'Grid filtered W', data: series.gridFiltered, borderColor: '#0f766e' },
      { label: 'Residual W', data: series.residual, borderColor: '#c2410c' },
    ]);
    renderChart('chart-battery', series.batteryResponseLabels, [
      { label: 'Commanded W', data: series.batteryCommanded, borderColor: '#7c3aed' },
      { label: 'Actual W', data: series.batteryActual, borderColor: '#059669' },
    ]);
  }

  const charts = {};
  function renderChart(canvasId, labels, datasets) {
    if (typeof document === 'undefined' || typeof Chart === 'undefined') {
      return;
    }
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
      return;
    }
    if (charts[canvasId]) {
      charts[canvasId].destroy();
    }
    charts[canvasId] = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: datasets.map((dataset) => ({
          ...dataset,
          fill: false,
          tension: 0.15,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
      },
    });
  }

  function parseAndRender(rawText) {
    const parsed = unwrapDiagnostics(JSON.parse(rawText));
    renderUI(parsed);
    return parsed;
  }

  function initBrowser() {
    if (typeof document === 'undefined') {
      return;
    }
    const fileInput = document.getElementById('file-input');
    const textInput = document.getElementById('json-input');
    const parseBtn = document.getElementById('parse-btn');
    const dropZone = document.getElementById('drop-zone');
    const pasteToggle = document.getElementById('paste-toggle');
    const pasteArea = document.getElementById('paste-area');
    const parseError = document.getElementById('parse-error');

    function handleText(text) {
      try {
        parseAndRender(text);
      } catch (err) {
        parseError.textContent = err.message;
        parseError.classList.remove('hidden');
      }
    }

    parseBtn.addEventListener('click', () => handleText(textInput.value));
    fileInput.addEventListener('change', (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) {
        return;
      }
      file.text().then(handleText);
    });
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (event) => {
      event.preventDefault();
      dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', (event) => {
      event.preventDefault();
      dropZone.classList.remove('drag-over');
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) {
        return;
      }
      file.text().then(handleText);
    });
    pasteToggle.addEventListener('click', () => {
      pasteArea.classList.toggle('hidden');
    });
  }

  const api = {
    unwrapDiagnostics,
    deriveSummary,
    buildSeries,
    generateTips,
    parseAndRender,
    initBrowser,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.ZGCAnalyzer = api;
  if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', initBrowser);
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
