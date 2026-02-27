/* ==========================================================================
   Sidiya Provider Dashboard — Client-side logic
   Unified flow: Upload PDF → Review Extraction → Register Patient
   ========================================================================== */

(function () {
  'use strict';

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  function escapeHtml(v) {
    return String(v ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

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
  // Upload PDF
  // ---------------------------------------------------------------------------
  let currentExtractionData = null;
  let currentExtractionId = null;

  function initUpload() {
    const dropZone = $('#drop-zone');
    const pdfInput = $('#pdf-input');

    // Click to browse
    dropZone.addEventListener('click', () => pdfInput.click());

    // File selected via input
    pdfInput.addEventListener('change', () => {
      if (pdfInput.files && pdfInput.files[0]) {
        uploadPdf(pdfInput.files[0]);
      }
    });

    // Drag & drop
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file && file.type === 'application/pdf') {
        uploadPdf(file);
      } else {
        showUploadError('Please upload a PDF file.');
      }
    });

    // "Upload Another" button
    $('#btn-new-upload').addEventListener('click', resetToUpload);
  }

  async function uploadPdf(file) {
    const statusBox = $('#upload-status');
    const statusText = $('#upload-status-text');
    const progressFill = $('#progress-fill');

    // Show progress
    statusBox.hidden = false;
    statusText.textContent = `Extracting "${file.name}"... (Landing OCR + Gemini)`;
    statusText.className = '';
    progressFill.style.width = '0%';

    // Animate progress bar (fake progress while waiting)
    let progress = 0;
    const progressTimer = setInterval(() => {
      progress = Math.min(progress + Math.random() * 8, 90);
      progressFill.style.width = progress + '%';
    }, 500);

    const fd = new FormData();
    fd.append('pdf', file);

    try {
      const res = await fetch('/extract', { method: 'POST', body: fd });
      clearInterval(progressTimer);

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Extraction failed' }));
        throw new Error(err.detail || 'Extraction failed');
      }

      const data = await res.json();
      progressFill.style.width = '100%';
      statusText.textContent = 'Extraction complete!';

      currentExtractionData = data;
      currentExtractionId = data.extraction_id;

      // Short delay then show review
      setTimeout(() => showReviewStep(data), 600);

    } catch (err) {
      clearInterval(progressTimer);
      showUploadError(err.message);
    }
  }

  function showUploadError(msg) {
    const statusText = $('#upload-status-text');
    const statusBox = $('#upload-status');
    statusBox.hidden = false;
    statusText.textContent = `Error: ${msg}`;
    statusText.className = 'error-text';
    $('#progress-fill').style.width = '0%';
  }

  function resetToUpload() {
    currentExtractionData = null;
    currentExtractionId = null;

    $('#upload-step').hidden = false;
    $('#review-step').hidden = true;
    $('#upload-status').hidden = true;
    $('#pdf-input').value = '';
    $('#register-form').reset();
    $('#reg-feedback').hidden = true;
  }

  // ---------------------------------------------------------------------------
  // Review Extraction
  // ---------------------------------------------------------------------------
  function showReviewStep(data) {
    $('#upload-step').hidden = true;
    $('#review-step').hidden = false;

    const extractionId = data.extraction_id;
    $('#reg-extraction-id').value = extractionId || '';

    // Summary
    const patient = data.patient || {};
    const episode = data.clinical_episode || {};
    const encounter = data.encounter || {};
    const firstAppt = (data.follow_up?.appointments || [])[0] || {};
    const meds = data.medications?.discharge_medications || [];
    const validation = data.validation || {};

    const summaryHtml = `
      <div class="review-grid">
        <div class="review-kv"><span class="rk">Patient</span><span class="rv">${escapeHtml(patient.full_name || 'Unknown')}</span></div>
        <div class="review-kv"><span class="rk">DOB</span><span class="rv">${escapeHtml(patient.dob || 'NA')}</span></div>
        <div class="review-kv"><span class="rk">MRN</span><span class="rv">${escapeHtml(patient.mrn || 'NA')}</span></div>
        <div class="review-kv"><span class="rk">Primary Dx</span><span class="rv">${escapeHtml(episode.primary_diagnosis || 'NA')}</span></div>
        <div class="review-kv"><span class="rk">Discharge</span><span class="rv">${escapeHtml(encounter.discharge_datetime || 'NA')}</span></div>
        <div class="review-kv"><span class="rk">Follow-up</span><span class="rv">${escapeHtml(firstAppt.scheduled_datetime || 'NA')} — ${escapeHtml(firstAppt.provider_name || '')}</span></div>
        <div class="review-kv"><span class="rk">Medications</span><span class="rv">${meds.length} discharge medications</span></div>
        <div class="review-kv"><span class="rk">App Ready</span><span class="rv ${validation.ready_for_patient_app ? 'text-accent' : 'text-warn'}">${validation.ready_for_patient_app ? 'Yes' : 'No — review missing fields'}</span></div>
      </div>
    `;
    $('#review-summary').innerHTML = summaryHtml;

    // Medications table
    if (meds.length) {
      $('#review-meds').innerHTML = `
        <h4>Medications (${meds.length})</h4>
        <div class="mini-table-wrap">
          <table class="mini-table">
            <thead><tr><th>Name</th><th>Dose</th><th>Route</th><th>Frequency</th></tr></thead>
            <tbody>${meds.map(m => `
              <tr>
                <td>${escapeHtml(m.medication_name || 'NA')}</td>
                <td>${escapeHtml(m.dose || 'NA')}</td>
                <td>${escapeHtml(m.route || 'NA')}</td>
                <td>${escapeHtml(m.frequency || 'NA')}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    } else {
      $('#review-meds').innerHTML = '<p class="text-muted">No medications extracted.</p>';
    }

    // Care plan phases
    const cp = data.care_plan_90d || {};
    const phases = ['phase_0_7', 'phase_8_30', 'phase_31_90'];
    const phaseLabels = { phase_0_7: 'Days 0\u20137', phase_8_30: 'Days 8\u201330', phase_31_90: 'Days 31\u201390' };
    let cpHtml = '<h4>90-Day Care Plan</h4>';
    phases.forEach(p => {
      const items = cp[p] || [];
      if (items.length) {
        cpHtml += `<div class="cp-phase"><span class="cp-label">${phaseLabels[p]}</span><ul>${items.map(i => `<li>${escapeHtml(i)}</li>`).join('')}</ul></div>`;
      }
    });
    $('#review-care-plan').innerHTML = cpHtml;

    // Links
    if (extractionId) {
      $('#review-link-careplan').href = `/care-plan?id=${encodeURIComponent(extractionId)}`;
      $('#review-link-calendar').href = `/calendar-view?id=${encodeURIComponent(extractionId)}`;
      $('#review-link-summary').href = `/summary/${encodeURIComponent(extractionId)}`;
    }

    // Pre-fill phone if extracted
    const extractedPhone = data.extracted_details?.patient?.phone;
    if (extractedPhone) {
      $('#reg-phone').value = extractedPhone;
    }

    // Refresh extractions list
    loadExtractions();
  }

  // ---------------------------------------------------------------------------
  // Register Patient (from reviewed extraction)
  // ---------------------------------------------------------------------------
  function initRegisterForm() {
    $('#register-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const extractionId = $('#reg-extraction-id').value.trim();
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
        loadPatients();
        loadExtractions();
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
    setTimeout(() => { el.hidden = true; }, 10000);
  }

  // ---------------------------------------------------------------------------
  // Recent Extractions List
  // ---------------------------------------------------------------------------
  async function loadExtractions() {
    try {
      const data = await apiFetch('/api/extractions?limit=20');
      const items = data.items || [];
      renderExtractions(items);
    } catch (err) {
      $('#extractions-list').innerHTML = `<p class="empty-state">${escapeHtml(err.message)}</p>`;
    }
  }

  function renderExtractions(items) {
    const container = $('#extractions-list');
    if (!items.length) {
      container.innerHTML = '<p class="empty-state">No extractions yet. Upload a discharge summary above.</p>';
      return;
    }

    container.innerHTML = items.map(item => {
      const status = item.status || 'extracted';
      const statusClass = status === 'registered' ? 'status-registered' : 'status-extracted';
      const statusLabel = status === 'registered' ? 'Registered' : 'Pending Registration';
      const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';

      return `
        <div class="extraction-row ${statusClass}" data-id="${escapeHtml(item.id)}">
          <div class="extraction-info">
            <div class="extraction-name">${escapeHtml(item.patient_name || 'Unknown Patient')}</div>
            <div class="extraction-meta">${escapeHtml(item.primary_diagnosis || 'NA')} \u2014 ${escapeHtml(item.source_file_name || '')}</div>
          </div>
          <div class="extraction-date">${escapeHtml(created)}</div>
          <div class="extraction-status-badge ${statusClass}">${statusLabel}</div>
          <div class="extraction-actions">
            <a href="/care-plan?id=${encodeURIComponent(item.id)}" target="_blank" class="action-link">Care Plan</a>
            <a href="/calendar-view?id=${encodeURIComponent(item.id)}" target="_blank" class="action-link">Calendar</a>
            ${status !== 'registered' ? `<button class="btn-small btn-register-from-list" data-id="${escapeHtml(item.id)}">Register</button>` : ''}
          </div>
        </div>`;
    }).join('');

    // "Register" buttons in extraction list
    container.querySelectorAll('.btn-register-from-list').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          const record = await apiFetch(`/api/extractions/${encodeURIComponent(id)}`);
          const data = record.extraction_json || {};
          data.extraction_id = record.id;
          data.simplified_summary = record.simplified_summary;
          currentExtractionData = data;
          currentExtractionId = record.id;
          showReviewStep(data);
          $('#review-step').scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (err) {
          alert('Failed to load extraction: ' + err.message);
        }
      });
    });
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
            <div class="alert-type">${formatTrigger(a.trigger_type)} \u2014 Level ${a.level || 0}</div>
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
      container.innerHTML = '<p class="empty-state">No patients registered yet. Upload a discharge summary and register a patient above.</p>';
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
            <div class="patient-diagnosis">${p.primary_diagnosis || 'CHF'} \u2014 ${medText}</div>
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
        `Day ${todayData.care_plan_day} \u2014 Phase ${todayData.phase} \u2014 ${todayData.date}`;

      renderTodayActions(todayData);
      renderModalWeightChart(vitalsData.weight || []);
      renderComplianceSummary(vitalsData.compliance || []);

    } catch (err) {
      $('#modal-patient-name').textContent = 'Error loading patient';
      console.error(err);
    }
  }

  function renderTodayActions(data) {
    const container = $('#modal-today-actions');
    const actions = [];

    const wStatus = data.vitals.weight_logged ? 'done' : 'pending';
    const wText = data.vitals.weight_logged ? `${data.vitals.weight_value} kg` : 'Not logged';
    actions.push({ time: '07:30', icon: '\u2696\uFE0F', desc: `Weight: ${wText}`, status: wStatus });

    const bStatus = data.vitals.bp_logged ? 'done' : 'pending';
    actions.push({ time: '08:30', icon: '\uD83E\uDE7A', desc: `BP: ${bStatus === 'done' ? 'Logged' : 'Not logged'}`, status: bStatus });

    (data.medications || []).forEach(m => {
      const s = m.status === 'taken' ? 'done' : m.status === 'skipped' ? 'missed' : 'pending';
      actions.push({
        time: m.scheduled_time,
        icon: '\uD83D\uDC8A',
        desc: `${m.medication_name} (${m.dose || ''})`,
        status: s,
      });
    });

    const sStatus = data.vitals.symptom_check_done ? 'done' : 'pending';
    actions.push({ time: '19:00', icon: '\uD83D\uDCCB', desc: 'Symptom check', status: sStatus });

    actions.sort((a, b) => a.time.localeCompare(b.time));

    container.innerHTML = actions.map(a => `
      <div class="today-action">
        <span class="icon">${a.icon}</span>
        <span class="time">${a.time}</span>
        <span class="desc">${a.desc}</span>
        <span class="status-check ${a.status}">${a.status === 'done' ? '\u2713' : a.status === 'missed' ? '\u2717' : '\u2014'}</span>
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

    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    for (let i = 0; i <= 3; i++) {
      const y = pad.top + (plotH / 3) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
      ctx.fillStyle = '#5e6d85'; ctx.font = '10px Sora';
      ctx.fillText((max - (range / 3) * i).toFixed(1), 2, y + 3);
    }

    ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + (plotW / Math.max(values.length - 1, 1)) * i;
      const y = pad.top + plotH - ((v - min) / range) * plotH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

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
          <span>Wt: ${c.weight_logged ? '\u2713' : '\u2717'}</span>
          <span class="patient-compliance ${cls}">${score}%</span>
        </div>`;
    }).join('');
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  function init() {
    initUpload();
    initRegisterForm();
    loadAlerts();
    loadPatients();
    loadExtractions();

    $('#btn-refresh').addEventListener('click', () => {
      loadAlerts();
      loadPatients();
    });

    $('#btn-refresh-extractions').addEventListener('click', loadExtractions);

    // Modal close
    $('#modal-close').addEventListener('click', () => { $('#patient-modal').hidden = true; });
    $('.modal-overlay').addEventListener('click', () => { $('#patient-modal').hidden = true; });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
