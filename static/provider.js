/* ==========================================================================
   Sidiya Provider Dashboard — Client-side logic
   ========================================================================== */

(function () {
  'use strict';

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  async function apiFetch(path, opts = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Register Patient
  // ---------------------------------------------------------------------------
  function initRegisterForm() {
    $('#register-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const extractionId = parseInt($('#reg-extraction-id').value, 10);
      const phone = $('#reg-phone').value.trim();
      const caregiverPhone = $('#reg-caregiver-phone').value.trim() || null;
      const nursePhone = $('#reg-nurse-phone').value.trim() || null;

      if (!extractionId || !phone) {
        showRegFeedback('Extraction ID and phone are required.', 'error');
        return;
      }

      try {
        const res = await apiFetch('/api/patients/register', {
          method: 'POST',
          body: JSON.stringify({
            extraction_id: extractionId,
            phone,
            caregiver_phone: caregiverPhone,
            nurse_phone: nursePhone,
          }),
        });
        const counts = res.reminder_rules_created || {};
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        showRegFeedback(
          `Registered ${res.full_name} (ID: ${res.patient_id}). Created ${total} reminder rules.`,
          'success'
        );
        $('#register-form').reset();
        loadPatients();
      } catch (err) {
        showRegFeedback(err.message, 'error');
      }
    });
  }

  function showRegFeedback(msg, type) {
    const el = $('#reg-feedback');
    el.textContent = msg;
    el.className = `reg-feedback ${type}`;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 8000);
  }

  // ---------------------------------------------------------------------------
  // Alerts
  // ---------------------------------------------------------------------------
  async function loadAlerts() {
    try {
      const data = await apiFetch('/api/provider/alerts');
      const alerts = data.alerts || [];
      const section = $('#alerts-section');
      const list = $('#alerts-list');
      const count = $('#alert-count');

      if (!alerts.length) {
        section.hidden = true;
        return;
      }

      section.hidden = false;
      count.textContent = alerts.length;

      list.innerHTML = alerts.map(a => `
        <div class="alert-item" data-id="${a.id}">
          <div class="alert-info">
            <div class="alert-patient">${a.patient_name || 'Unknown'}</div>
            <div class="alert-type">${formatTrigger(a.trigger_type)} — Level ${a.level || 0}</div>
          </div>
          <button class="btn-ack" data-esc-id="${a.id}">Acknowledge</button>
        </div>
      `).join('');

      list.querySelectorAll('.btn-ack').forEach(btn => {
        btn.addEventListener('click', async () => {
          try {
            await apiFetch(`/api/provider/alerts/${btn.dataset.escId}/ack`, { method: 'POST' });
            btn.closest('.alert-item').remove();
            const remaining = list.querySelectorAll('.alert-item').length;
            count.textContent = remaining;
            if (!remaining) section.hidden = true;
          } catch (err) {
            console.error('Failed to ack alert:', err);
          }
        });
      });
    } catch (err) {
      console.error('Failed to load alerts:', err);
    }
  }

  function formatTrigger(type) {
    const map = {
      missed_weight: 'Missed weight log',
      missed_medication: 'Missed medication',
      weight_spike_24h: 'Weight spike (24h)',
      weight_spike_7d: 'Weight spike (7d)',
      red_flag: 'Red-flag symptom',
      consecutive_missed_weight: 'Multiple missed weight days',
    };
    return map[type] || type;
  }

  // ---------------------------------------------------------------------------
  // Patient List
  // ---------------------------------------------------------------------------
  async function loadPatients() {
    try {
      const data = await apiFetch('/api/provider/patients');
      const patients = data.patients || [];
      renderPatients(patients);
    } catch (err) {
      $('#patient-list').innerHTML = `<p class="empty-state">${err.message}</p>`;
    }
  }

  function renderPatients(patients) {
    const container = $('#patient-list');

    if (!patients.length) {
      container.innerHTML = '<p class="empty-state">No patients registered yet. Use the form above to register a patient from an extraction.</p>';
      return;
    }

    container.innerHTML = patients.map(p => {
      const compliance = p.today_compliance;
      const score = compliance ? Math.round(compliance.compliance_score * 100) : 0;
      const medText = compliance
        ? `${compliance.medications_taken || 0}/${compliance.medications_expected || 0} meds`
        : 'No data';

      return `
        <div class="patient-row" data-patient-id="${p.patient_id}">
          <div class="status-dot ${p.status}"></div>
          <div>
            <div class="patient-name">${p.full_name || 'Unknown'}</div>
            <div class="patient-diagnosis">${p.primary_diagnosis || 'CHF'} — ${medText}</div>
          </div>
          <div class="patient-day">Day ${p.care_plan_day}</div>
          <div class="patient-compliance ${p.status}">${score}%</div>
          <div class="patient-alerts">${p.open_alerts ? `${p.open_alerts} alert${p.open_alerts > 1 ? 's' : ''}` : ''}</div>
        </div>`;
    }).join('');

    container.querySelectorAll('.patient-row').forEach(row => {
      row.addEventListener('click', () => openPatientDetail(row.dataset.patientId));
    });
  }

  // ---------------------------------------------------------------------------
  // Patient Detail Modal
  // ---------------------------------------------------------------------------
  async function openPatientDetail(patientId) {
    const modal = $('#patient-modal');
    modal.hidden = false;

    try {
      const [todayData, vitalsData] = await Promise.all([
        apiFetch(`/api/patient/${patientId}/today`),
        apiFetch(`/api/provider/patient/${patientId}/vitals?days=7`),
      ]);

      $('#modal-patient-name').textContent = todayData.full_name || 'Patient';
      $('#modal-patient-meta').textContent =
        `Day ${todayData.care_plan_day} — Phase ${todayData.phase} — ${todayData.date}`;

      // Today's actions
      renderTodayActions(todayData);

      // Weight chart
      renderModalWeightChart(vitalsData.weight || []);

      // Compliance summary
      renderComplianceSummary(vitalsData.compliance || []);

    } catch (err) {
      $('#modal-patient-name').textContent = 'Error loading patient';
      console.error(err);
    }
  }

  function renderTodayActions(data) {
    const container = $('#modal-today-actions');
    const actions = [];

    // Weight
    const wStatus = data.vitals.weight_logged ? 'done' : 'pending';
    const wText = data.vitals.weight_logged ? `${data.vitals.weight_value} kg` : 'Not logged';
    actions.push({ time: '07:30', icon: '⚖️', desc: `Weight: ${wText}`, status: wStatus });

    // BP
    const bStatus = data.vitals.bp_logged ? 'done' : 'pending';
    actions.push({ time: '08:30', icon: '🩺', desc: `BP: ${bStatus === 'done' ? 'Logged' : 'Not logged'}`, status: bStatus });

    // Medications
    (data.medications || []).forEach(m => {
      const s = m.status === 'taken' ? 'done' : m.status === 'skipped' ? 'missed' : 'pending';
      actions.push({
        time: m.scheduled_time,
        icon: '💊',
        desc: `${m.medication_name} (${m.dose || ''})`,
        status: s,
      });
    });

    // Symptom check
    const sStatus = data.vitals.symptom_check_done ? 'done' : 'pending';
    actions.push({ time: '19:00', icon: '📋', desc: 'Symptom check', status: sStatus });

    actions.sort((a, b) => a.time.localeCompare(b.time));

    container.innerHTML = actions.map(a => `
      <div class="today-action">
        <span class="icon">${a.icon}</span>
        <span class="time">${a.time}</span>
        <span class="desc">${a.desc}</span>
        <span class="status-check ${a.status}">${a.status === 'done' ? '✓' : a.status === 'missed' ? '✗' : '—'}</span>
      </div>
    `).join('');
  }

  function renderModalWeightChart(logs) {
    const canvas = $('#modal-weight-chart');
    const ctx = canvas.getContext('2d');
    const w = canvas.parentElement.clientWidth - 20;
    canvas.width = w;
    canvas.height = 180;
    ctx.clearRect(0, 0, w, 180);

    if (!logs.length) {
      ctx.fillStyle = '#5e6d85';
      ctx.font = '14px Sora';
      ctx.fillText('No weight data', w / 2 - 45, 90);
      return;
    }

    const values = logs.map(l => typeof l.value === 'number' ? l.value : 0);
    const dates = logs.map(l => l.date || '');
    const min = Math.min(...values) - 1;
    const max = Math.max(...values) + 1;
    const range = max - min || 1;

    const pad = { top: 15, bottom: 25, left: 40, right: 10 };
    const plotW = w - pad.left - pad.right;
    const plotH = 180 - pad.top - pad.bottom;

    // Grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    for (let i = 0; i <= 3; i++) {
      const y = pad.top + (plotH / 3) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
      ctx.fillStyle = '#5e6d85'; ctx.font = '10px Sora';
      ctx.fillText((max - (range / 3) * i).toFixed(1), 2, y + 3);
    }

    // Line
    ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
      const y = pad.top + plotH - ((v - min) / range) * plotH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Points + labels
    values.forEach((v, i) => {
      const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
      const y = pad.top + plotH - ((v - min) / range) * plotH;
      ctx.fillStyle = '#00d4aa';
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#5e6d85'; ctx.font = '9px Sora';
      ctx.fillText(dates[i]?.slice(5) || '', x - 10, 180 - 3);
    });
  }

  function renderComplianceSummary(compliance) {
    const container = $('#modal-compliance');
    if (!compliance.length) {
      container.innerHTML = '<p style="color:var(--text-muted);font-size:14px;">No compliance data yet.</p>';
      return;
    }

    container.innerHTML = compliance.map(c => {
      const score = Math.round((c.compliance_score || 0) * 100);
      const cls = score >= 70 ? 'good' : score >= 40 ? 'at_risk' : 'critical';
      return `
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
          <span>${c.date}</span>
          <span>Meds: ${c.medications_taken || 0}/${c.medications_expected || 0}</span>
          <span>Wt: ${c.weight_logged ? '✓' : '✗'}</span>
          <span class="patient-compliance ${cls}">${score}%</span>
        </div>`;
    }).join('');
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  function init() {
    initRegisterForm();
    loadAlerts();
    loadPatients();

    $('#btn-refresh').addEventListener('click', () => {
      loadAlerts();
      loadPatients();
    });

    // Modal close
    $('#modal-close').addEventListener('click', () => { $('#patient-modal').hidden = true; });
    $('.modal-overlay').addEventListener('click', () => { $('#patient-modal').hidden = true; });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
