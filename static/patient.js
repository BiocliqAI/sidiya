/* ==========================================================================
   Sidiya Patient PWA — Client-side logic
   Handles: login, today view, vitals logging, medication tracking, trends
   ========================================================================== */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  let patientId = localStorage.getItem('sidiya_patient_id') || '';
  let todayData = null;

  const API = '';  // Same origin

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  async function apiFetch(path, opts = {}) {
    const res = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  }

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  function showScreen(name) {
    $$('.screen').forEach(s => s.classList.remove('active'));
    const screen = $(`#screen-${name}`);
    if (screen) screen.classList.add('active');
    $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.screen === name));
  }

  function openSheet(name) {
    const sheet = $(`#sheet-${name}`);
    if (sheet) sheet.classList.add('open');
  }

  function closeSheet(name) {
    const sheet = $(`#sheet-${name}`);
    if (sheet) sheet.classList.remove('open');
  }

  function closeAllSheets() {
    $$('.bottom-sheet').forEach(s => s.classList.remove('open'));
  }

  function showFeedback(id, text, type = 'success') {
    const el = $(`#${id}`);
    if (!el) return;
    el.textContent = text;
    el.className = `feedback-text ${type}`;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 4000);
  }

  function greetingText() {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  }

  function formatTime(t) {
    const [h, m] = t.split(':');
    const hr = parseInt(h, 10);
    const ampm = hr >= 12 ? 'PM' : 'AM';
    return `${hr % 12 || 12}:${m} ${ampm}`;
  }

  function timeSlot(t) {
    const h = parseInt(t.split(':')[0], 10);
    if (h < 12) return 'Morning';
    if (h < 17) return 'Afternoon';
    return 'Night';
  }

  // ---------------------------------------------------------------------------
  // Login
  // ---------------------------------------------------------------------------
  function initLogin() {
    if (patientId) {
      loadToday();
      return;
    }
    showScreen('login');

    $('#btn-login').addEventListener('click', async () => {
      const phone = $('#login-phone').value.trim();
      if (!phone) { showLoginError('Enter your phone number'); return; }

      $('#btn-login').disabled = true;
      $('#btn-login').textContent = 'Signing in...';

      try {
        // Look up patient by iterating provider patients API (simple approach for MVP)
        const res = await apiFetch(`/api/provider/patients`);
        const patients = res.patients || [];
        const match = patients.find(p => p.phone === phone || p.phone === phone.replace(/\s+/g, ''));

        if (match) {
          patientId = match.patient_id;
          localStorage.setItem('sidiya_patient_id', patientId);
          loadToday();
        } else {
          showLoginError('Phone number not found. Contact your care team.');
        }
      } catch (err) {
        showLoginError(err.message || 'Connection failed. Try again.');
      } finally {
        $('#btn-login').disabled = false;
        $('#btn-login').textContent = 'Sign In';
      }
    });
  }

  function showLoginError(msg) {
    const el = $('#login-error');
    el.textContent = msg;
    el.hidden = false;
  }

  // ---------------------------------------------------------------------------
  // Today View
  // ---------------------------------------------------------------------------
  async function loadToday() {
    showScreen('home');
    try {
      todayData = await apiFetch(`/api/patient/${patientId}/today`);
      renderToday(todayData);
    } catch (err) {
      console.error('Failed to load today:', err);
      if (err.message.includes('not found')) {
        localStorage.removeItem('sidiya_patient_id');
        patientId = '';
        showScreen('login');
      }
    }
  }

  function renderToday(data) {
    $('#greeting-text').textContent = greetingText();
    $('#patient-name-text').textContent = data.full_name || 'Patient';
    $('#day-badge').textContent = `Day ${data.care_plan_day}`;

    // Weight status
    const ws = $('#weight-status');
    if (data.vitals.weight_logged) {
      ws.textContent = `${data.vitals.weight_value} kg — Logged`;
      ws.className = 'task-status done';
      $('#card-weight').classList.add('done');
    } else {
      ws.textContent = 'Tap to log';
      ws.className = 'task-status';
      $('#card-weight').classList.remove('done');
    }

    // Medication status
    const meds = data.medications || [];
    const pending = meds.filter(m => m.status === 'pending').length;
    const total = meds.length;
    const ms = $('#meds-status');
    if (pending === 0 && total > 0) {
      ms.textContent = `All ${total} taken`;
      ms.className = 'task-status done';
      $('#card-meds').classList.add('done');
    } else {
      ms.textContent = `${pending} of ${total} pending`;
      ms.className = 'task-status';
      $('#card-meds').classList.remove('done');
    }

    // BP status
    const bs = $('#bp-status');
    if (data.vitals.bp_logged) {
      const v = data.vitals.bp_value;
      bs.textContent = v ? `${v.systolic}/${v.diastolic} — Logged` : 'Logged';
      bs.className = 'task-status done';
      $('#card-bp').classList.add('done');
    } else {
      bs.textContent = 'Tap to log';
      bs.className = 'task-status';
      $('#card-bp').classList.remove('done');
    }

    // Symptom check status
    const ss = $('#symptom-status');
    if (data.vitals.symptom_check_done) {
      ss.textContent = 'Completed';
      ss.className = 'task-status done';
      $('#card-symptoms').classList.add('done');
    } else {
      ss.textContent = 'Tap to check in';
      ss.className = 'task-status';
      $('#card-symptoms').classList.remove('done');
    }

    // Appointment
    const apptCard = $('#appointment-card');
    if (data.next_appointment) {
      apptCard.hidden = false;
      const dt = new Date(data.next_appointment.datetime);
      const daysUntil = Math.ceil((dt - new Date()) / (1000 * 60 * 60 * 24));
      $('#appt-detail').textContent =
        `${data.next_appointment.provider || 'Follow-up'} — ${daysUntil > 0 ? `in ${daysUntil} days` : 'Today'}`;
    } else {
      apptCard.hidden = true;
    }
  }

  // ---------------------------------------------------------------------------
  // Log Weight
  // ---------------------------------------------------------------------------
  function initWeightSheet() {
    const input = $('#weight-value');
    $('#weight-minus').addEventListener('click', () => {
      input.value = (parseFloat(input.value) - 0.1).toFixed(1);
    });
    $('#weight-plus').addEventListener('click', () => {
      input.value = (parseFloat(input.value) + 0.1).toFixed(1);
    });

    // Pre-fill with last logged weight if available
    if (todayData && todayData.vitals.weight_value) {
      input.value = todayData.vitals.weight_value;
    }

    $('#btn-save-weight').addEventListener('click', async () => {
      const val = parseFloat(input.value);
      if (isNaN(val) || val < 20 || val > 200) {
        showFeedback('weight-feedback', 'Enter a valid weight (20-200 kg)', 'danger');
        return;
      }

      $('#btn-save-weight').disabled = true;
      try {
        const res = await apiFetch(`/api/patient/${patientId}/vitals`, {
          method: 'POST',
          body: JSON.stringify({ type: 'weight', value: val }),
        });

        if (res.alert) {
          showFeedback('weight-feedback',
            `Weight logged. Alert: ${res.alert.message || 'Weight gain detected. Your care team has been notified.'}`,
            'warning');
        } else {
          showFeedback('weight-feedback', `Weight logged: ${val} kg`, 'success');
        }

        setTimeout(() => { closeSheet('weight'); loadToday(); }, 1500);
      } catch (err) {
        showFeedback('weight-feedback', err.message, 'danger');
      } finally {
        $('#btn-save-weight').disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Log BP
  // ---------------------------------------------------------------------------
  function initBPSheet() {
    $('#btn-save-bp').addEventListener('click', async () => {
      const sys = parseInt($('#bp-systolic').value, 10);
      const dia = parseInt($('#bp-diastolic').value, 10);
      if (isNaN(sys) || isNaN(dia) || sys < 60 || sys > 250 || dia < 30 || dia > 150) {
        showFeedback('bp-feedback', 'Enter valid BP values', 'danger');
        return;
      }

      $('#btn-save-bp').disabled = true;
      try {
        await apiFetch(`/api/patient/${patientId}/vitals`, {
          method: 'POST',
          body: JSON.stringify({ type: 'bp', value: { systolic: sys, diastolic: dia } }),
        });
        showFeedback('bp-feedback', `BP logged: ${sys}/${dia}`, 'success');
        setTimeout(() => { closeSheet('bp'); loadToday(); }, 1500);
      } catch (err) {
        showFeedback('bp-feedback', err.message, 'danger');
      } finally {
        $('#btn-save-bp').disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Symptom Check
  // ---------------------------------------------------------------------------
  function initSymptomSheet() {
    // "None" checkbox logic: uncheck others when "None" is checked
    $('#symptom-none').addEventListener('change', (e) => {
      if (e.target.checked) {
        $$('#symptom-list input[type="checkbox"]').forEach(cb => {
          if (cb !== e.target) cb.checked = false;
        });
      }
    });
    $$('#symptom-list input[type="checkbox"]:not(#symptom-none)').forEach(cb => {
      cb.addEventListener('change', () => {
        if (cb.checked) $('#symptom-none').checked = false;
      });
    });

    $('#btn-save-symptoms').addEventListener('click', async () => {
      const checked = [...$$('#symptom-list input:checked')].map(cb => cb.value);
      if (checked.length === 0) {
        showFeedback('symptom-feedback', 'Select at least one option', 'danger');
        return;
      }

      const symptoms = checked.includes('none') ? [] : checked;

      $('#btn-save-symptoms').disabled = true;
      try {
        const res = await apiFetch(`/api/patient/${patientId}/vitals`, {
          method: 'POST',
          body: JSON.stringify({ type: 'symptom_check', value: { symptoms } }),
        });

        if (res.alert) {
          showFeedback('symptom-feedback',
            'Your care team has been notified about your symptoms.',
            'warning');
        } else if (symptoms.length === 0) {
          showFeedback('symptom-feedback', 'Great! No symptoms reported.', 'success');
        } else {
          showFeedback('symptom-feedback', 'Symptoms logged. Thank you.', 'success');
        }

        setTimeout(() => { closeSheet('symptoms'); loadToday(); }, 2000);
      } catch (err) {
        showFeedback('symptom-feedback', err.message, 'danger');
      } finally {
        $('#btn-save-symptoms').disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Medications List
  // ---------------------------------------------------------------------------
  async function loadMedications() {
    showScreen('meds-list');
    try {
      const data = await apiFetch(`/api/patient/${patientId}/medications`);
      renderMedications(data.medications || []);
    } catch (err) {
      $('#meds-list-container').innerHTML = `<p class="error-text">${err.message}</p>`;
    }
  }

  function renderMedications(meds) {
    const container = $('#meds-list-container');
    if (!meds.length) {
      container.innerHTML = '<p style="padding:20px;color:var(--text-muted);">No medications found.</p>';
      return;
    }

    // Group by time slot
    const groups = {};
    meds.forEach(m => {
      const slot = timeSlot(m.scheduled_time);
      if (!groups[slot]) groups[slot] = [];
      groups[slot].push(m);
    });

    let html = '';
    for (const [slot, items] of Object.entries(groups)) {
      html += `<div class="med-time-group"><h3>${slot}</h3>`;
      items.forEach(m => {
        const taken = m.status === 'taken';
        const skipped = m.status === 'skipped';
        const cls = taken || skipped ? 'taken' : '';
        html += `
          <div class="med-item ${cls}" data-med="${encodeURIComponent(m.medication_name)}" data-time="${m.scheduled_time}">
            <div class="med-check ${taken ? 'checked' : ''}" role="button" tabindex="0"></div>
            <div class="med-details">
              <div class="med-name">${m.medication_name}</div>
              <div class="med-dose">${m.dose || ''} ${m.route || ''} — ${formatTime(m.scheduled_time)}</div>
              ${m.indication && m.indication !== 'unknown' ? `<div class="med-indication">${m.indication}</div>` : ''}
            </div>
            ${!taken && !skipped ? `<button class="med-skip-btn">Skip</button>` : ''}
            ${skipped ? '<span style="color:var(--warn);font-size:12px;">Skipped</span>' : ''}
          </div>`;
      });
      html += '</div>';
    }
    container.innerHTML = html;

    // Attach event listeners
    container.querySelectorAll('.med-check').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const item = e.target.closest('.med-item');
        ackMedication(item, 'taken');
      });
    });
    container.querySelectorAll('.med-skip-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const item = e.target.closest('.med-item');
        ackMedication(item, 'skipped', 'other');
      });
    });
  }

  async function ackMedication(itemEl, status, skipReason = null) {
    const medName = decodeURIComponent(itemEl.dataset.med);
    const scheduledTime = itemEl.dataset.time;

    try {
      await apiFetch(`/api/patient/${patientId}/medications/ack`, {
        method: 'POST',
        body: JSON.stringify({
          medication_name: medName,
          scheduled_time: scheduledTime,
          status,
          skip_reason: skipReason,
        }),
      });
      // Reload medications list
      loadMedications();
    } catch (err) {
      console.error('Failed to ack medication:', err);
    }
  }

  // ---------------------------------------------------------------------------
  // Trends
  // ---------------------------------------------------------------------------
  async function loadTrends() {
    showScreen('trends');
    try {
      const [weightData, bpData] = await Promise.all([
        apiFetch(`/api/patient/${patientId}/vitals/history?vital_type=weight&days=7`),
        apiFetch(`/api/patient/${patientId}/vitals/history?vital_type=bp&days=7`),
      ]);
      renderWeightChart(weightData.logs || []);
      renderBPChart(bpData.logs || []);
    } catch (err) {
      console.error('Failed to load trends:', err);
    }
  }

  function renderWeightChart(logs) {
    const canvas = $('#chart-weight');
    const ctx = canvas.getContext('2d');
    const w = canvas.parentElement.clientWidth - 40;
    canvas.width = w;
    canvas.height = 200;
    ctx.clearRect(0, 0, w, 200);

    if (!logs.length) {
      ctx.fillStyle = '#5e6d85';
      ctx.font = '14px Sora';
      ctx.fillText('No weight data yet', w / 2 - 60, 100);
      return;
    }

    const values = logs.map(l => typeof l.value === 'number' ? l.value : 0);
    const dates = logs.map(l => l.date || '');
    const min = Math.min(...values) - 1;
    const max = Math.max(...values) + 1;
    const range = max - min || 1;

    const pad = { top: 20, bottom: 30, left: 45, right: 10 };
    const plotW = w - pad.left - pad.right;
    const plotH = 200 - pad.top - pad.bottom;

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (plotH / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
      ctx.fillStyle = '#5e6d85';
      ctx.font = '11px Sora';
      ctx.fillText((max - (range / 4) * i).toFixed(1), 2, y + 4);
    }

    // Line
    ctx.strokeStyle = '#00d4aa';
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
      const y = pad.top + plotH - ((v - min) / range) * plotH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Points
    values.forEach((v, i) => {
      const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
      const y = pad.top + plotH - ((v - min) / range) * plotH;
      ctx.fillStyle = '#00d4aa';
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
    });

    // Date labels
    ctx.fillStyle = '#5e6d85';
    ctx.font = '10px Sora';
    dates.forEach((d, i) => {
      const x = pad.left + (plotW / Math.max(dates.length - 1, 1)) * i;
      ctx.fillText(d.slice(5), x - 12, 200 - 5);
    });

    // Trend summary
    if (values.length >= 2) {
      const diff = values[values.length - 1] - values[0];
      const summary = $('#weight-trend-summary');
      if (Math.abs(diff) < 0.1) {
        summary.textContent = 'Weight stable this week.';
        summary.style.color = 'var(--success)';
      } else {
        summary.textContent = `${diff > 0 ? '+' : ''}${diff.toFixed(1)} kg over ${values.length} days`;
        summary.style.color = diff > 1 ? 'var(--danger)' : diff > 0 ? 'var(--warn)' : 'var(--success)';
      }
    }
  }

  function renderBPChart(logs) {
    const canvas = $('#chart-bp');
    const ctx = canvas.getContext('2d');
    const w = canvas.parentElement.clientWidth - 40;
    canvas.width = w;
    canvas.height = 200;
    ctx.clearRect(0, 0, w, 200);

    if (!logs.length) {
      ctx.fillStyle = '#5e6d85';
      ctx.font = '14px Sora';
      ctx.fillText('No BP data yet', w / 2 - 50, 100);
      return;
    }

    const sysValues = logs.map(l => l.value?.systolic || 0);
    const diaValues = logs.map(l => l.value?.diastolic || 0);
    const allValues = [...sysValues, ...diaValues];
    const min = Math.min(...allValues) - 10;
    const max = Math.max(...allValues) + 10;
    const range = max - min || 1;

    const pad = { top: 20, bottom: 30, left: 45, right: 10 };
    const plotW = w - pad.left - pad.right;
    const plotH = 200 - pad.top - pad.bottom;

    function drawLine(values, color) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.beginPath();
      values.forEach((v, i) => {
        const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
        const y = pad.top + plotH - ((v - min) / range) * plotH;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    drawLine(sysValues, '#ff5c6c');
    drawLine(diaValues, '#00d4aa');
  }

  // ---------------------------------------------------------------------------
  // Care Plan Info
  // ---------------------------------------------------------------------------
  async function loadCarePlan() {
    showScreen('care-info');
    try {
      const data = await apiFetch(`/api/patient/${patientId}/care-plan`);
      renderCarePlan(data);
    } catch (err) {
      $('#care-info-content').innerHTML = `<p class="error-text">${err.message}</p>`;
    }
  }

  function renderCarePlan(data) {
    const plan = data.care_plan || {};
    const day = data.care_plan_day || 0;
    let phase, phaseItems;
    if (day <= 7) {
      phase = 'Days 0–7 (Recovery)';
      phaseItems = plan.phase_0_7 || [];
    } else if (day <= 30) {
      phase = 'Days 8–30 (Building Strength)';
      phaseItems = plan.phase_8_30 || [];
    } else {
      phase = 'Days 31–90 (Self-Management)';
      phaseItems = plan.phase_31_90 || [];
    }

    const phaseCard = $('#phase-current');
    phaseCard.innerHTML = `
      <p class="phase-label">Current Phase — Day ${day}</p>
      <h3>${phase}</h3>
      <ul>${phaseItems.map(i => `<li>${i}</li>`).join('')}</ul>
    `;

    const yellowZone = (data.red_flags || {}).yellow_zone || [];
    const redZone = (data.red_flags || {}).red_zone || [];

    $('#warning-signs-list').innerHTML = yellowZone.map(s => `<li>${s}</li>`).join('');
    $('#emergency-signs-list').innerHTML = redZone.map(s => `<li>${s}</li>`).join('');
  }

  // ---------------------------------------------------------------------------
  // Navigation & Event Wiring
  // ---------------------------------------------------------------------------
  function init() {
    // Task card clicks → open sheets
    $('#card-weight').addEventListener('click', () => openSheet('weight'));
    $('#card-bp').addEventListener('click', () => openSheet('bp'));
    $('#card-symptoms').addEventListener('click', () => openSheet('symptoms'));
    $('#card-meds').addEventListener('click', () => loadMedications());

    // Sheet overlays close on tap
    $$('.sheet-overlay').forEach(el => {
      el.addEventListener('click', closeAllSheets);
    });

    // Bottom nav
    $$('.nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const screen = btn.dataset.screen;
        if (screen === 'home') loadToday();
        else if (screen === 'meds-list') loadMedications();
        else if (screen === 'trends') loadTrends();
        else if (screen === 'care-info') loadCarePlan();
      });
    });

    // Back buttons
    $$('.back-btn').forEach(btn => {
      btn.addEventListener('click', () => loadToday());
    });

    // Initialize sheets
    initWeightSheet();
    initBPSheet();
    initSymptomSheet();

    // Start
    initLogin();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
