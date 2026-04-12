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
    const arrays = toArray(data.arrays);
    const uncalibrated = arrays.filter((a) => a.calibration_confidence !== 'measured');
    return {
      status: data.status || 'unknown',
      gridFilteredW: data.grid_filtered_w ?? null,
      pidOutputW: data.pid_output_w ?? null,
      arrayCount: arrays.length,
      uncalibratedCount: uncalibrated.length,
      batteryCount: toArray(data.batteries).length,
    };
  }

  function generateTips(data) {
    const tips = [];
    const arrays = toArray(data.arrays);
    const config = data.config || {};

    if (data.status === 'disabled') {
      tips.push({
        level: 'warn',
        title: 'Controller is disabled',
        body: 'The enable entity is off or unavailable. Set it to on or remove it from the configuration.',
      });
    }

    if (data.status === 'deadband') {
      tips.push({
        level: 'info',
        title: 'Grid is within deadband',
        body: `Grid power is within ±${config.deadband_w ?? '?'} W. The PID integrator is frozen — no setpoint changes are made.`,
      });
    }

    if (data.grid_filtered_w == null) {
      tips.push({
        level: 'err',
        title: 'No grid measurement available',
        body: 'The grid sensor(s) are unavailable. Zero Grid Controller cannot regulate without a valid grid reading.',
      });
    }

    const uncalibrated = arrays.filter((a) => a.calibration_confidence !== 'measured');
    if (uncalibrated.length) {
      tips.push({
        level: 'info',
        title: 'Arrays not yet calibrated',
        body: `${uncalibrated.map((a) => a.name).join(', ')} use estimated W/unit. Run calibration on a sunny day for accurate PID gains.`,
      });
    }

    if (!tips.length) {
      tips.push({
        level: 'ok',
        title: 'No obvious control problems detected',
        body: 'The diagnostics snapshot does not show a clear blocking issue.',
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
    const tips = generateTips(data);

    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('parse-error').classList.add('hidden');

    document.getElementById('summary-status').textContent = summary.status;
    document.getElementById('summary-grid').textContent =
      summary.gridFilteredW == null ? '—' : `${summary.gridFilteredW.toFixed(1)} W`;
    document.getElementById('summary-pid').textContent =
      summary.pidOutputW == null ? '—' : `${summary.pidOutputW.toFixed(1)} W`;
    document.getElementById('summary-arrays').textContent = String(summary.arrayCount);

    const tipsContainer = document.getElementById('tips');
    tipsContainer.innerHTML = '';
    tips.forEach((tip) => {
      const item = document.createElement('div');
      item.className = `tip tip-${tip.level}`;
      item.innerHTML = `<strong>${tip.title}</strong><div>${tip.body}</div>`;
      tipsContainer.appendChild(item);
    });

    const pidEl = document.getElementById('pid-list');
    const pid = data.pid || {};
    const config = data.config || {};
    pidEl.innerHTML = '';
    [
      ['Kp', pid.kp ?? '—'],
      ['Ki', pid.ki ?? '—'],
      ['Kd', pid.kd ?? '—'],
      ['Integral', pid.integral ?? '—'],
      ['PID basis gain', pid.basis && pid.basis.total_w_per_unit != null ? `${pid.basis.total_w_per_unit} W/unit` : '—'],
      ['PID basis arrays', pid.basis && Array.isArray(pid.basis.included_arrays) && pid.basis.included_arrays.length ? pid.basis.included_arrays.join(', ') : '—'],
      ['Deadband', config.deadband_w != null ? `${config.deadband_w} W` : '—'],
      ['EWM alpha', config.ewm_alpha ?? '—'],
      ['Aggressiveness', config.aggressiveness ?? '—'],
      ['Enable entity', config.enable_entity || 'none'],
    ].forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'kv-row';
      row.innerHTML = `<span class="kv-label">${label}</span><span class="kv-value">${value}</span>`;
      pidEl.appendChild(row);
    });

    const arraysTable = document.getElementById('arrays-table');
    arraysTable.innerHTML = '';
    toArray(data.arrays).forEach((a) => {
      const sp = (data.setpoints || {})[a.name];
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${a.name}</td>
        <td>${a.output_type}</td>
        <td>${sp == null ? '—' : sp}</td>
        <td>${a.setpoint_min} – ${a.setpoint_max}</td>
        <td>${a.power_sensor_entity || '—'}</td>
        <td>${a.w_per_unit}</td>
        <td>${a.derived_max_power_w == null ? '—' : `${a.derived_max_power_w} W`}</td>
        <td>${a.settling_down_s == null ? '—' : `${a.settling_down_s} s`}</td>
        <td>${a.settling_up_s == null ? '—' : `${a.settling_up_s} s`}</td>
        <td><span class="badge ${createBadgeClass(a.calibration_confidence === 'measured' ? 'ok' : 'warn')}">${a.calibration_confidence}</span></td>`;
      arraysTable.appendChild(row);
    });

    const batteriesTable = document.getElementById('batteries-table');
    batteriesTable.innerHTML = '';
    toArray(data.batteries).forEach((b) => {
      const sp = (data.battery_setpoints || {})[b.name];
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${b.name}</td>
        <td>${b.max_charge_w} W</td>
        <td>${b.max_discharge_w} W</td>
        <td>${sp == null ? '—' : `${sp} W`}</td>`;
      batteriesTable.appendChild(row);
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
