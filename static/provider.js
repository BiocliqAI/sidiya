/* ==========================================================================
   Sidiya — Clinical Command Center
   Tab-based dashboard: Command Center | Patient Onboarding | Analytics
   ========================================================================== */
(function(){
'use strict';
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);
const esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

async function api(path,opts={}){
  const res=await fetch(path,{headers:{'Content-Type':'application/json',...opts.headers},...opts});
  if(!res.ok){const e=await res.json().catch(()=>({detail:res.statusText}));throw new Error(e.detail||'Request failed');}
  return res.json();
}

/* ══════════ Tab Navigation ══════════ */
function initTabs(){
  $$('.tab-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      $$('.tab-btn').forEach(b=>{b.classList.remove('active');b.setAttribute('aria-selected','false');});
      $$('.tab-panel').forEach(p=>p.classList.remove('active'));
      btn.classList.add('active');
      btn.setAttribute('aria-selected','true');
      const panel=$('#panel-'+btn.dataset.tab);
      if(panel)panel.classList.add('active');
      if(btn.dataset.tab==='analytics')loadAnalytics();
    });
  });
}

/* ══════════ TIME HELPERS ══════════ */
function timeAgo(isoStr){
  if(!isoStr)return '';
  const d=new Date(isoStr);if(isNaN(d))return '';
  const s=Math.floor((Date.now()-d.getTime())/1000);
  if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago';
}
const triggerLabel={
  missed_weight:'Missed weight log',missed_medication:'Missed medication',
  weight_spike_24h:'Weight spike (24h)',weight_spike_7d:'Weight spike (7d)',
  red_flag:'Red-flag symptom',consecutive_missed_weight:'Consecutive missed weight',
};
function fmtTrigger(t){return triggerLabel[t]||t||'Alert';}

/* ══════════ KPI Stats ══════════ */
async function loadStats(){
  try{
    const s=await api('/api/provider/stats');
    $('#kpi-patients').textContent=s.total_patients;
    $('#kpi-compliance').textContent=Math.round(s.avg_compliance*100)+'%';
    const av=$('#kpi-alerts-val');
    av.textContent=s.open_alerts;
    if(s.open_alerts>0){
      $('#kpi-alerts-sub').textContent=s.critical_alerts+' critical \u00B7 '+s.warning_alerts+' warning';
    }
    $('#kpi-appts').textContent=s.upcoming_appointments;
    // Tab badge
    const badge=$('#tab-badge-alerts');
    if(s.open_alerts>0){badge.textContent=s.open_alerts;badge.hidden=false;}
    else badge.hidden=true;
  }catch(e){console.error('Stats failed:',e);}
}

/* ══════════ Alert Sidebar ══════════ */
let allAlerts=[];
let alertFilter='all';

async function loadAlerts(){
  try{
    const d=await api('/api/provider/alerts');
    allAlerts=(d.alerts||[]).map(a=>{
      a._severity=a.level>=2?'critical':a.level>=1?'warning':'info';
      return a;
    });
    allAlerts.sort((a,b)=>(b.level||0)-(a.level||0));
    renderAlerts();
  }catch(e){$('#alert-queue').innerHTML='<div class="sidebar-empty">Failed to load alerts</div>';}
}

function renderAlerts(){
  const q=$('#alert-queue');
  let filtered=allAlerts;
  if(alertFilter==='critical')filtered=allAlerts.filter(a=>a._severity==='critical');
  if(alertFilter==='open')filtered=allAlerts.filter(a=>a.status==='open');
  if(!filtered.length){q.innerHTML='<div class="sidebar-empty">No alerts matching filter</div>';return;}
  q.innerHTML=filtered.map(a=>`
    <div class="alert-card severity-${a._severity}" data-id="${a.id}">
      <div class="ac-top">
        <span class="ac-patient">${esc(a.patient_name||'Unknown')}</span>
        <span class="ac-time">${timeAgo(a.created_at)}</span>
      </div>
      <div class="ac-trigger">${esc(fmtTrigger(a.trigger_type))}</div>
      <span class="ac-level lv-${Math.min(a.level||0,2)}">Level ${a.level||0}</span>
      <div class="ac-actions">
        <button class="ac-btn ac-btn-resolve" data-esc-id="${a.id}">Resolve</button>
        <button class="ac-btn" data-esc-id="${a.id}" data-quick-ack="1">Quick Ack</button>
      </div>
    </div>
  `).join('');

  q.querySelectorAll('.ac-btn-resolve').forEach(btn=>{
    btn.addEventListener('click',e=>{e.stopPropagation();openAlertDrawer(btn.dataset.escId);});
  });
  q.querySelectorAll('[data-quick-ack]').forEach(btn=>{
    btn.addEventListener('click',async e=>{
      e.stopPropagation();
      try{
        await api(`/api/provider/alerts/${btn.dataset.escId}/ack`,{method:'POST'});
        allAlerts=allAlerts.filter(a=>a.id!==btn.dataset.escId);
        renderAlerts();loadStats();
      }catch(err){console.error(err);}
    });
  });
}

function initAlertFilters(){
  $$('.filter-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      $$('.filter-btn').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      alertFilter=btn.dataset.filter;
      renderAlerts();
    });
  });
}

/* ══════════ Alert Drawer (Rich Resolution) ══════════ */
function openAlertDrawer(escId){
  $('#drawer-esc-id').value=escId;
  const alert=allAlerts.find(a=>a.id===escId);
  if(alert)$('#drawer-title').textContent='Resolve: '+fmtTrigger(alert.trigger_type)+' \u2014 '+(alert.patient_name||'');
  $('#alert-drawer').hidden=false;
}

function initAlertDrawer(){
  $('#drawer-close').addEventListener('click',()=>{$('#alert-drawer').hidden=true;});
  $('#drawer-overlay').addEventListener('click',()=>{$('#alert-drawer').hidden=true;});
  $$('.da-btn').forEach(btn=>{
    btn.addEventListener('click',async()=>{
      const escId=$('#drawer-esc-id').value;
      const note=$('#drawer-note-text').value.trim();
      try{
        await api(`/api/provider/alerts/${escId}/resolve`,{
          method:'POST',
          body:JSON.stringify({resolution_type:btn.dataset.action,action_taken:btn.textContent.trim(),note:note||null}),
        });
        allAlerts=allAlerts.filter(a=>a.id!==escId);
        renderAlerts();loadStats();
        $('#alert-drawer').hidden=true;
        $('#drawer-note-text').value='';
      }catch(err){alert('Failed: '+err.message);}
    });
  });
}

/* ══════════ Patient Grid ══════════ */
let allPatients=[];
let currentDetailId=null;

async function loadPatients(){
  try{
    const d=await api('/api/provider/patients');
    allPatients=d.patients||[];
    renderPatients(allPatients);
  }catch(e){$('#patient-grid').innerHTML='<div class="sidebar-empty">'+esc(e.message)+'</div>';}
}

function renderPatients(patients){
  const g=$('#patient-grid');
  if(!patients.length){g.innerHTML='<div class="sidebar-empty">No patients registered yet.</div>';return;}
  g.innerHTML=patients.map(p=>{
    const comp=p.today_compliance;
    const score=comp?Math.round(comp.compliance_score*100):0;
    const cls=p.status;
    const day=p.care_plan_day||0;
    const phase=day<=7?'0-7':day<=30?'8-30':'31-90';
    const phaseClass='p-'+phase.replace('-','-');
    const wt=comp?.weight_logged;
    const mt=comp?(comp.medications_taken||0)+'/'+(comp.medications_expected||0):'—';
    return `
    <div class="patient-card" data-pid="${p.patient_id}">
      <div class="pc-dot ${cls}"></div>
      <div class="pc-info">
        <div class="pc-name">${esc(p.full_name||'Unknown')}</div>
        <div class="pc-sub">
          <span>${esc(p.primary_diagnosis||'CHF')}</span>
          <span>Day ${day}</span>
          <span>Meds: ${mt}</span>
        </div>
      </div>
      <div class="pc-right">
        <span class="pc-phase ${phaseClass}">${phase}</span>
        <div class="pc-vitals">
          <span class="pc-vital ${wt?'done':'pending'}" title="Weight">\u2696</span>
        </div>
        <span class="pc-score ${cls}">${score}%</span>
        ${p.open_alerts?`<span class="pc-alerts-badge">${p.open_alerts} alert${p.open_alerts>1?'s':''}</span>`:''}
      </div>
    </div>`;
  }).join('');
  g.querySelectorAll('.patient-card').forEach(card=>{
    card.addEventListener('click',()=>openPatientDetail(card.dataset.pid));
  });
}

function initPatientSearch(){
  $('#patient-search').addEventListener('input',e=>{
    const q=e.target.value.toLowerCase().trim();
    if(!q){renderPatients(allPatients);return;}
    renderPatients(allPatients.filter(p=>(p.full_name||'').toLowerCase().includes(q)||(p.primary_diagnosis||'').toLowerCase().includes(q)));
  });
}

/* ══════════ Patient Detail Panel ══════════ */
async function openPatientDetail(pid){
  currentDetailId=pid;
  const panel=$('#patient-detail');
  panel.hidden=false;
  // Force reflow for animation
  panel.offsetHeight;

  try{
    const [today,vitals,escHist,notes]=await Promise.all([
      api(`/api/patient/${pid}/today`),
      api(`/api/provider/patient/${pid}/vitals?days=7`),
      api(`/api/provider/patient/${pid}/escalation-history?limit=20`),
      api(`/api/provider/patient/${pid}/notes?limit=20`),
    ]);

    $('#detail-name').textContent=today.full_name||'Patient';
    $('#detail-meta').textContent=`Day ${today.care_plan_day} \u2014 Phase ${today.phase} \u2014 ${today.date}`;

    renderDetailToday(today);
    drawLineChart($('#detail-weight-chart'),(vitals.weight||[]).map(l=>({x:l.date,y:typeof l.value==='number'?l.value:0})),'#00d4aa','kg');
    drawLineChart($('#detail-bp-chart'),(vitals.bp||[]).map(l=>({x:l.date,y:typeof l.value==='object'?l.value.systolic||0:0})),'#648cff','mmHg');
    renderDetailCompliance(vitals.compliance||[]);
    renderDetailEscalations(escHist.history||[]);
    renderDetailNotes(notes.notes||[],pid);
  }catch(e){$('#detail-name').textContent='Error: '+e.message;}
}

function renderDetailToday(data){
  const c=$('#detail-today');
  const acts=[];
  const ws=data.vitals.weight_logged?'done':'pending';
  acts.push({time:'07:30',icon:'\u2696\uFE0F',desc:'Weight: '+(data.vitals.weight_logged?data.vitals.weight_value+' kg':'Not logged'),status:ws});
  const bs=data.vitals.bp_logged?'done':'pending';
  acts.push({time:'08:30',icon:'\uD83E\uDE7A',desc:'BP: '+(bs==='done'?'Logged':'Not logged'),status:bs});
  (data.medications||[]).forEach(m=>{
    const s=m.status==='taken'?'done':m.status==='skipped'?'missed':'pending';
    acts.push({time:m.scheduled_time,icon:'\uD83D\uDC8A',desc:`${m.medication_name} (${m.dose||''})`,status:s});
  });
  const ss=data.vitals.symptom_check_done?'done':'pending';
  acts.push({time:'19:00',icon:'\uD83D\uDCCB',desc:'Symptom check',status:ss});
  acts.sort((a,b)=>a.time.localeCompare(b.time));
  c.innerHTML=acts.map(a=>`
    <div class="today-row">
      <span class="t-icon">${a.icon}</span>
      <span class="t-time">${a.time}</span>
      <span class="t-desc">${esc(a.desc)}</span>
      <span class="t-status ${a.status}">${a.status==='done'?'\u2713':a.status==='missed'?'\u2717':'\u2014'}</span>
    </div>`).join('');
}

function renderDetailCompliance(compliance){
  const c=$('#detail-compliance');
  if(!compliance.length){c.innerHTML='<p style="color:var(--text-muted);font-size:13px;">No compliance data yet.</p>';return;}
  c.innerHTML=compliance.map(r=>{
    const s=Math.round((r.compliance_score||0)*100);
    const cls=s>=70?'good':s>=40?'at_risk':'critical';
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">
      <span>${r.date}</span>
      <span>Meds: ${r.medications_taken||0}/${r.medications_expected||0}</span>
      <span>Wt: ${r.weight_logged?'\u2713':'\u2717'}</span>
      <span class="pc-score ${cls}" style="padding:2px 8px;">${s}%</span>
    </div>`;
  }).join('');
}

function renderDetailEscalations(history){
  const c=$('#detail-escalations');
  if(!history.length){c.innerHTML='<p style="color:var(--text-muted);font-size:13px;">No alert history.</p>';return;}
  c.innerHTML=history.map(e=>{
    const open=e.status==='open';
    return `<div class="esc-item ${open?'esc-open':'esc-resolved'}">
      <div class="esc-top"><span class="esc-trigger">${esc(fmtTrigger(e.trigger_type))}</span><span class="esc-date">${timeAgo(e.created_at)}</span></div>
      ${!open&&e.resolution_type?`<div class="esc-resolution">${esc(e.action_taken||e.resolution_type)}${e.resolution_note?' \u2014 '+esc(e.resolution_note):''}</div>`:''}
      ${open?'<div style="font-size:11px;color:var(--danger);margin-top:4px;font-weight:700;">OPEN \u2014 Level '+(e.level||0)+'</div>':''}
    </div>`;
  }).join('');
}

function renderDetailNotes(notes,pid){
  const c=$('#detail-notes');
  if(!notes.length){c.innerHTML='<p style="color:var(--text-muted);font-size:13px;">No clinical notes yet.</p>';return;}
  c.innerHTML=notes.map(n=>`
    <div class="note-item">
      <div class="note-meta">
        <span class="note-type-badge">${esc(n.note_type||'general')}</span>
        <span>${timeAgo(n.created_at)}</span>
      </div>
      <div class="note-text">${esc(n.note)}</div>
    </div>`).join('');
}

function initDetailPanel(){
  $('#detail-close').addEventListener('click',()=>{$('#patient-detail').hidden=true;currentDetailId=null;});
  $('#btn-add-note').addEventListener('click',async()=>{
    if(!currentDetailId)return;
    const text=$('#new-note-text').value.trim();
    if(!text)return;
    const type=$('#new-note-type').value;
    try{
      await api(`/api/provider/patient/${currentDetailId}/notes`,{method:'POST',body:JSON.stringify({note:text,note_type:type})});
      $('#new-note-text').value='';
      const notes=await api(`/api/provider/patient/${currentDetailId}/notes?limit=20`);
      renderDetailNotes(notes.notes||[],currentDetailId);
    }catch(e){alert('Failed to add note: '+e.message);}
  });
}

/* ══════════ Charts ══════════ */
function drawLineChart(canvas,data,color,unit){
  const ctx=canvas.getContext('2d');
  const w=canvas.parentElement.clientWidth-16;
  const h=parseInt(canvas.height);
  canvas.width=w;ctx.clearRect(0,0,w,h);
  if(!data.length){ctx.fillStyle='#5e6d85';ctx.font='13px Sora';ctx.fillText('No data',w/2-25,h/2);return;}
  const vals=data.map(d=>d.y);const labels=data.map(d=>d.x||'');
  const min=Math.min(...vals)-1;const max=Math.max(...vals)+1;const range=max-min||1;
  const pad={top:12,bottom:22,left:38,right:8};
  const pw=w-pad.left-pad.right;const ph=h-pad.top-pad.bottom;
  // Grid
  ctx.strokeStyle='rgba(255,255,255,0.05)';ctx.lineWidth=1;
  for(let i=0;i<=3;i++){const y=pad.top+(ph/3)*i;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(w-pad.right,y);ctx.stroke();
    ctx.fillStyle='#5e6d85';ctx.font='9px Sora';ctx.fillText((max-(range/3)*i).toFixed(1),1,y+3);}
  // Line
  ctx.strokeStyle=color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.beginPath();
  vals.forEach((v,i)=>{const x=pad.left+(pw/Math.max(vals.length-1,1))*i;const y=pad.top+ph-((v-min)/range)*ph;i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);});
  ctx.stroke();
  // Gradient fill
  const last=vals.length-1;
  ctx.lineTo(pad.left+(pw/Math.max(last,1))*last,pad.top+ph);ctx.lineTo(pad.left,pad.top+ph);ctx.closePath();
  const grad=ctx.createLinearGradient(0,pad.top,0,h);
  grad.addColorStop(0,color+'30');grad.addColorStop(1,color+'00');ctx.fillStyle=grad;ctx.fill();
  // Points + labels
  vals.forEach((v,i)=>{
    const x=pad.left+(pw/Math.max(last,1))*i;const y=pad.top+ph-((v-min)/range)*ph;
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#5e6d85';ctx.font='8px Sora';ctx.fillText(labels[i]?.slice(5)||'',x-10,h-3);
  });
}

/* ══════════ Onboarding Tab ══════════ */
let wizStep=1;let currentExtraction=null;let currentExtractionId=null;

function setWizStep(step){
  wizStep=step;
  $$('.wizard-step').forEach((el,i)=>{
    el.classList.toggle('active',i+1===step);
    el.classList.toggle('done',i+1<step);
  });
  for(let i=1;i<=3;i++){const p=$('#wiz-step-'+i);if(p)p.classList.toggle('active',i===step);}
}

function initOnboarding(){
  const dropZone=$('#drop-zone'),pdfInput=$('#pdf-input');
  dropZone.addEventListener('click',()=>pdfInput.click());
  pdfInput.addEventListener('change',()=>{if(pdfInput.files&&pdfInput.files[0])uploadPdf(pdfInput.files[0]);});
  dropZone.addEventListener('dragover',e=>{e.preventDefault();dropZone.classList.add('drag-over');});
  dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop',e=>{
    e.preventDefault();dropZone.classList.remove('drag-over');
    const f=e.dataTransfer.files[0];
    if(f&&f.type==='application/pdf')uploadPdf(f);
    else{$('#progress-text').textContent='Error: Please upload a PDF';$('#progress-text').className='progress-text error';$('#upload-progress').hidden=false;}
  });

  $('#btn-wiz-back-1').addEventListener('click',()=>{setWizStep(1);$('#upload-progress').hidden=true;pdfInput.value='';});
  $('#btn-wiz-next-2').addEventListener('click',()=>setWizStep(3));
  $('#btn-wiz-back-2').addEventListener('click',()=>setWizStep(2));

  initRegisterForm();
  loadExtractions();
  $('#btn-refresh-ext').addEventListener('click',loadExtractions);
}

async function uploadPdf(file){
  const prog=$('#upload-progress'),txt=$('#progress-text'),fill=$('#progress-fill');
  prog.hidden=false;txt.textContent=`Extracting "${file.name}"...`;txt.className='progress-text';fill.style.width='0%';
  let pct=0;
  const timer=setInterval(()=>{pct=Math.min(pct+Math.random()*8,90);fill.style.width=pct+'%';},500);
  const fd=new FormData();fd.append('pdf',file);
  try{
    const res=await fetch('/extract',{method:'POST',body:fd});
    clearInterval(timer);
    if(!res.ok){const e=await res.json().catch(()=>({detail:'Extraction failed'}));throw new Error(e.detail||'Extraction failed');}
    const data=await res.json();fill.style.width='100%';txt.textContent='Extraction complete!';
    currentExtraction=data;currentExtractionId=data.extraction_id;
    setTimeout(()=>{showReview(data);setWizStep(2);},500);
  }catch(e){clearInterval(timer);txt.textContent='Error: '+e.message;txt.className='progress-text error';fill.style.width='0%';}
}

function showReview(data){
  const patient=data.patient||{};const ep=data.clinical_episode||{};
  const enc=data.encounter||{};const appt=(data.follow_up?.appointments||[])[0]||{};
  const meds=data.medications?.discharge_medications||[];const val=data.validation||{};
  const id=data.extraction_id||currentExtractionId||'';

  $('#review-card').innerHTML=`<div class="rc-grid">
    <div class="rc-kv"><span class="rc-k">Patient</span><span class="rc-v">${esc(patient.full_name||'Unknown')}</span></div>
    <div class="rc-kv"><span class="rc-k">DOB</span><span class="rc-v">${esc(patient.dob||'NA')}</span></div>
    <div class="rc-kv"><span class="rc-k">MRN</span><span class="rc-v">${esc(patient.mrn||'NA')}</span></div>
    <div class="rc-kv"><span class="rc-k">Primary Dx</span><span class="rc-v">${esc(ep.primary_diagnosis||'NA')}</span></div>
    <div class="rc-kv"><span class="rc-k">Discharge</span><span class="rc-v">${esc(enc.discharge_datetime||'NA')}</span></div>
    <div class="rc-kv"><span class="rc-k">Follow-up</span><span class="rc-v">${esc(appt.scheduled_datetime||'NA')}</span></div>
    <div class="rc-kv"><span class="rc-k">Medications</span><span class="rc-v">${meds.length}</span></div>
    <div class="rc-kv"><span class="rc-k">App Ready</span><span class="rc-v" style="color:${val.ready_for_patient_app?'var(--accent)':'var(--warn)'}">${val.ready_for_patient_app?'Yes':'No'}</span></div>
  </div>`;

  if(meds.length){
    $('#review-meds').innerHTML=`<div class="mini-table" style="margin-top:8px;"><table class="mini-table"><thead><tr><th>Name</th><th>Dose</th><th>Route</th><th>Freq</th></tr></thead><tbody>${meds.map(m=>`<tr><td>${esc(m.medication_name)}</td><td>${esc(m.dose)}</td><td>${esc(m.route)}</td><td>${esc(m.frequency)}</td></tr>`).join('')}</tbody></table></div>`;
  }else{$('#review-meds').innerHTML='<p style="color:var(--text-muted);font-size:12px;">No medications extracted.</p>';}

  const cp=data.care_plan_90d||{};
  let cpHtml='';
  [['phase_0_7','Days 0\u20137'],['phase_8_30','Days 8\u201330'],['phase_31_90','Days 31\u201390']].forEach(([k,label])=>{
    const items=cp[k]||[];if(items.length)cpHtml+=`<div style="margin-bottom:10px;"><strong style="font-size:12px;color:var(--accent);">${label}</strong><ul style="margin:4px 0 0 16px;font-size:12px;color:var(--text-soft);">${items.map(i=>'<li>'+esc(i)+'</li>').join('')}</ul></div>`;
  });
  $('#review-care-plan').innerHTML=cpHtml||'<p style="color:var(--text-muted);font-size:12px;">No care plan data.</p>';

  if(id){
    $('#link-careplan').href='/care-plan?id='+encodeURIComponent(id);
    $('#link-calendar').href='/calendar-view?id='+encodeURIComponent(id);
    $('#link-summary').href='/summary/'+encodeURIComponent(id);
  }

  $('#reg-extraction-id').value=id;
  const ph=data.extracted_details?.patient?.phone;
  if(ph)$('#reg-phone').value=ph;
}

function initRegisterForm(){
  $('#register-form').addEventListener('submit',async e=>{
    e.preventDefault();
    const eid=$('#reg-extraction-id').value.trim();
    const phone=$('#reg-phone').value.trim();
    const cg=$('#reg-caregiver').value.trim()||null;
    const nr=$('#reg-nurse').value.trim()||null;
    if(!eid||!phone){showFeedback('Extraction ID and phone required.','error');return;}
    try{
      const r=await api('/api/patients/register',{method:'POST',body:JSON.stringify({extraction_id:eid,phone:phone,caregiver_phone:cg,nurse_phone:nr})});
      const total=Object.values(r.reminder_rules_created||{}).reduce((a,b)=>a+b,0);
      showFeedback(`Registered ${r.full_name}. ${total} reminder rules created.`,'success');
      loadPatients();loadStats();loadExtractions();
    }catch(err){showFeedback(err.message,'error');}
  });
}

function showFeedback(msg,type){
  const el=$('#reg-feedback');el.textContent=msg;el.className='rf-feedback '+type;el.hidden=false;
  setTimeout(()=>{el.hidden=true;},10000);
}

/* ══════════ Extraction History ══════════ */
async function loadExtractions(){
  try{
    const d=await api('/api/extractions?limit=30');
    renderExtractions(d.items||[]);
  }catch(e){$('#extraction-list').innerHTML='<div class="sidebar-empty">'+esc(e.message)+'</div>';}
}

function renderExtractions(items){
  const c=$('#extraction-list');
  if(!items.length){c.innerHTML='<div class="sidebar-empty">No extractions yet.</div>';return;}
  c.innerHTML=items.map(it=>{
    const st=it.status||'extracted';
    const created=it.created_at?new Date(it.created_at).toLocaleDateString():'';
    return `<div class="ext-card" data-id="${esc(it.id)}">
      <div class="ext-name">${esc(it.patient_name||'Unknown')}</div>
      <div class="ext-meta">${esc(it.primary_diagnosis||'')} \u2014 ${created}</div>
      <div class="ext-bottom">
        <span class="ext-badge status-${st}">${st==='registered'?'Registered':'Pending'}</span>
        <div class="ext-actions">
          <a href="/care-plan?id=${encodeURIComponent(it.id)}" target="_blank" class="ext-link">Care Plan</a>
          ${st!=='registered'?`<button class="ext-link ext-reg-btn" data-id="${esc(it.id)}">Register</button>`:''}
        </div>
      </div>
    </div>`;
  }).join('');

  c.querySelectorAll('.ext-reg-btn').forEach(btn=>{
    btn.addEventListener('click',async e=>{
      e.stopPropagation();
      try{
        const record=await api(`/api/extractions/${encodeURIComponent(btn.dataset.id)}`);
        const data=record.extraction_json||{};data.extraction_id=record.id;
        currentExtraction=data;currentExtractionId=record.id;
        showReview(data);setWizStep(2);
        $('#wiz-step-2').scrollIntoView({behavior:'smooth'});
      }catch(err){alert('Failed: '+err.message);}
    });
  });
}

/* ══════════ Analytics Tab ══════════ */
let analyticsLoaded=false;

async function loadAnalytics(){
  if(analyticsLoaded)return;
  try{
    const [analytics,stats]=await Promise.all([api('/api/provider/analytics'),api('/api/provider/stats')]);
    analyticsLoaded=true;

    // Compliance trend chart
    const trend=analytics.compliance_trend||[];
    drawLineChart($('#chart-compliance'),trend.map(d=>({x:d.date,y:d.avg_compliance})),'#00d4aa','%');

    // Risk distribution donut
    const risk=stats.risk_distribution||{};
    drawDonut($('#chart-risk'),[
      {label:'Good',value:risk.good||0,color:'#00d4aa'},
      {label:'At Risk',value:risk.at_risk||0,color:'#f0a030'},
      {label:'Critical',value:risk.critical||0,color:'#ff5c6c'},
    ]);
    $('#risk-legend').innerHTML=[
      {label:'Good',color:'#00d4aa',val:risk.good||0},
      {label:'At Risk',color:'#f0a030',val:risk.at_risk||0},
      {label:'Critical',color:'#ff5c6c',val:risk.critical||0},
    ].map(r=>`<div class="rl-item"><span class="rl-dot" style="background:${r.color}"></span>${r.label}: ${r.val}</div>`).join('');

    // Escalation summary
    const es=analytics.escalation_summary||{};
    $('#esc-summary').innerHTML=`
      <div class="es-stat"><div class="es-val" style="color:var(--text)">${es.total_7d||0}</div><div class="es-label">Total (7d)</div></div>
      <div class="es-stat"><div class="es-val" style="color:var(--accent)">${es.resolved_7d||0}</div><div class="es-label">Resolved</div></div>
      <div class="es-stat"><div class="es-val" style="color:var(--danger)">${es.open||0}</div><div class="es-label">Open</div></div>`;

    // Alert breakdown
    const breakdown=analytics.alert_type_breakdown||{};
    const maxVal=Math.max(...Object.values(breakdown),1);
    $('#alert-breakdown').innerHTML=Object.entries(breakdown).map(([k,v])=>`
      <div class="ab-row">
        <span class="ab-label">${esc(fmtTrigger(k))}</span>
        <div class="ab-bar"><div class="ab-fill" style="width:${Math.round(v/maxVal*100)}%"></div></div>
        <span class="ab-count">${v}</span>
      </div>`).join('')||'<p style="color:var(--text-muted);font-size:12px;">No alerts in the last 7 days.</p>';
  }catch(e){console.error('Analytics failed:',e);}
}

function drawDonut(canvas,segments){
  const ctx=canvas.getContext('2d');
  const w=canvas.parentElement.clientWidth-16;
  const h=parseInt(canvas.height);
  canvas.width=w;ctx.clearRect(0,0,w,h);
  const total=segments.reduce((a,s)=>a+s.value,0);
  if(!total){ctx.fillStyle='#5e6d85';ctx.font='13px Sora';ctx.fillText('No data',w/2-25,h/2);return;}
  const cx=w/2,cy=h/2,r=Math.min(cx,cy)-10,inner=r*0.6;
  let start=-Math.PI/2;
  segments.forEach(s=>{
    const angle=(s.value/total)*Math.PI*2;
    ctx.beginPath();ctx.moveTo(cx+inner*Math.cos(start),cy+inner*Math.sin(start));
    ctx.arc(cx,cy,r,start,start+angle);
    const endX=cx+inner*Math.cos(start+angle),endY=cy+inner*Math.sin(start+angle);
    ctx.lineTo(endX,endY);ctx.arc(cx,cy,inner,start+angle,start,true);
    ctx.closePath();ctx.fillStyle=s.color;ctx.fill();
    start+=angle;
  });
  // Center text
  ctx.fillStyle='var(--text)';ctx.font='700 24px Sora';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillText(total,cx,cy-6);
  ctx.font='11px Sora';ctx.fillStyle='#5e6d85';ctx.fillText('patients',cx,cy+14);
}

/* ══════════ Init ══════════ */
function init(){
  initTabs();
  initAlertFilters();
  initAlertDrawer();
  initDetailPanel();
  initPatientSearch();
  initOnboarding();

  loadStats();
  loadAlerts();
  loadPatients();

  $('#btn-refresh-cmd').addEventListener('click',()=>{loadStats();loadAlerts();loadPatients();});

  // Auto-refresh every 60s
  setInterval(()=>{loadStats();loadAlerts();},60000);
}
document.addEventListener('DOMContentLoaded',init);
})();
