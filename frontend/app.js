//  CONFIG
const SAMPLE_RATE     = 24000;
const END_OF_RESPONSE = new Uint8Array([0xFF, 0xFE]);

//  DOM 
const phoneInput = document.getElementById('phone-input');
const connectBtn = document.getElementById('connect-btn');
const micBtn     = document.getElementById('mic-btn');
const logEl      = document.getElementById('log');
const logEmpty   = document.getElementById('log-empty');
const vizCanvas  = document.getElementById('viz');
const ctx2d      = vizCanvas.getContext('2d');
const statusPill = document.getElementById('status-pill');
const statusText = document.getElementById('status-text');
const errLine    = document.getElementById('err-line');
const waveBars   = document.getElementById('wave-bars');
const micState   = document.getElementById('mic-state');
const hMsgs      = document.getElementById('h-msgs');
const hSess      = document.getElementById('h-sess');
const fSys       = document.getElementById('f-sys');
const fMsgs      = document.getElementById('f-msgs');

//  STATE 
let ws            = null;
let mediaRecorder = null;
let micStream     = null;
let audioCtx      = null;
let analyser      = null;
let vizRaf        = null;
let pcmQueue      = [];
let isPlaying     = false;
let nextPlayTime  = 0;
let streamDone    = false;
let chunksReceived= 0;
let msgCount      = 0;
let sessTimer     = null;
let sessStart     = null;

//  CANVAS SIZING
function sizeCanvas() {
  const mobile = window.innerWidth <= 680;
  const sz = mobile
    ? Math.min(72, window.innerWidth * 0.18)
    : Math.min(190, window.innerWidth * 0.17, window.innerHeight * 0.3);
  vizCanvas.width = vizCanvas.height = Math.round(sz);
}
sizeCanvas();
window.addEventListener('resize', () => { sizeCanvas(); });

//  STATUS 
function setStatus(msg, cls = '') {
  statusText.textContent = msg;
  statusPill.className   = 'status-pill' + (cls ? ' ' + cls : '');
  fSys.textContent       = msg;
  const labels = { recording:'RECORDING', active:'CONNECTED', playing:'SPEAKING', error:'ERROR', '':'STAND BY' };
  if (micState) micState.textContent = labels[cls] ?? 'STAND BY';
}

function showError(msg) {
  errLine.textContent = msg;
  setTimeout(() => { errLine.textContent = ''; }, 4500);
}

//  TIMER 
function startTimer() {
  sessStart = Date.now();
  sessTimer = setInterval(() => {
    const s = Math.floor((Date.now() - sessStart) / 1000);
    hSess.textContent = `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  }, 1000);
}
function stopTimer() { clearInterval(sessTimer); hSess.textContent = '--:--'; }

//  MESSAGES
function ts() {
  return new Date().toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

let _typingEl = null;

function addMsg(role, text) {
  if (role === 'agent') hideTyping();
  if (logEmpty?.parentNode) logEmpty.remove();

  msgCount++;
  hMsgs.textContent = msgCount;
  fMsgs.textContent = msgCount;

  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.innerHTML = `
    <div class="msg-meta">
      <span class="msg-role">${role === 'user' ? '▶ YOU' : '◀ AGENT'}</span>
      <span class="msg-ts">${ts()}</span>
    </div>
    <div class="msg-bubble">${esc(text)}</div>`;
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;

  // Check if this agent message is a booking confirmation → generate receipt
  if (role === 'agent') {
    const receiptData = parseBookingFromText(text);
    if (receiptData) {
      // Small delay so the message renders first
      setTimeout(() => injectReceiptIntoMessage(el, receiptData), 350);
    }
  }
}

function showTyping() {
  if (_typingEl) return;
  if (logEmpty?.parentNode) logEmpty.remove();
  _typingEl = document.createElement('div');
  _typingEl.className = 'msg agent pending';
  _typingEl.innerHTML = `
    <div class="msg-meta">
      <span class="msg-role">◀ AGENT</span>
      <span class="msg-ts">${ts()}</span>
    </div>
    <div class="msg-bubble"><div class="dots"><span></span><span></span><span></span></div></div>`;
  logEl.appendChild(_typingEl);
  logEl.scrollTop = logEl.scrollHeight;
}
function hideTyping() { if (_typingEl) { _typingEl.remove(); _typingEl = null; } }

//  VISUALIZER
function startViz(stream) {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  audioCtx.createMediaStreamSource(stream).connect(analyser);
  drawViz();
}
function stopViz() {
  cancelAnimationFrame(vizRaf);
  const s = vizCanvas.width;
  ctx2d.clearRect(0, 0, s, s);
}

function drawViz() {
  vizRaf = requestAnimationFrame(drawViz);
  const s   = vizCanvas.width;
  const cx  = s / 2, cy = s / 2;
  const r   = s * 0.225;
  const data= new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);
  ctx2d.clearRect(0, 0, s, s);

  const isRec  = micBtn.classList.contains('recording');
  const isPlay = micBtn.classList.contains('playing');
  const N = 54;

  for (let i = 0; i < N; i++) {
    const val   = data[Math.floor(i * data.length / N)] / 255;
    const angle = (i / N) * Math.PI * 2 - Math.PI / 2;
    const inner = r;
    const outer = r + val * (s * 0.175) + (s * 0.024);
    const alpha = 0.28 + val * 0.72;
    ctx2d.strokeStyle = isRec
      ? `rgba(232,64,64,${alpha})`
      : isPlay
        ? `rgba(48,196,122,${alpha})`
        : `rgba(232,160,48,${alpha})`;
    ctx2d.lineWidth = s * 0.015;
    ctx2d.beginPath();
    ctx2d.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
    ctx2d.lineTo(cx + Math.cos(angle) * outer, cy + Math.sin(angle) * outer);
    ctx2d.stroke();
  }

  // Inner ring
  ctx2d.beginPath();
  ctx2d.arc(cx, cy, r - 1.5, 0, Math.PI * 2);
  ctx2d.strokeStyle = isRec ? 'rgba(232,64,64,.18)' : isPlay ? 'rgba(48,196,122,.15)' : 'rgba(232,160,48,.1)';
  ctx2d.lineWidth = 1;
  ctx2d.stroke();
}

//  PCM PLAYBACK
function ensureAudioCtx() {
  if (!audioCtx || audioCtx.state === 'closed')
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

function enqueuePCM(buf) { pcmQueue.push(buf); if (!isPlaying) drainQueue(); }

function drainQueue() {
  if (!pcmQueue.length) {
    if (streamDone) {
      isPlaying = false;
      micBtn.classList.remove('playing');
      micBtn.disabled = false;
      waveBars.classList.remove('on');
      setStatus('READY', 'active');
    }
    return;
  }
  isPlaying = true;
  micBtn.classList.add('playing');
  micBtn.disabled = true;
  waveBars.classList.add('on');

  const raw     = pcmQueue.shift();
  const samples = new Int16Array(raw);
  const floats  = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) floats[i] = samples[i] / 32768;
  const buf = audioCtx.createBuffer(1, floats.length, SAMPLE_RATE);
  buf.getChannelData(0).set(floats);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  const at = Math.max(audioCtx.currentTime, nextPlayTime);
  src.start(at);
  nextPlayTime  = at + buf.duration;
  src.onended   = drainQueue;
}

function isEOR(d) {
  if (d.byteLength !== 2) return false;
  const v = new Uint8Array(d);
  return v[0] === 0xFF && v[1] === 0xFE;
}

//  WEBSOCKET
async function connect(phone) {
  setStatus('CONNECTING…');
  let token;
  try {
    const r = await fetch(`/auth/token?phone=${encodeURIComponent(phone)}`);
    if (!r.ok) { const e = await r.json().catch(()=>({})); showError(e.detail||'Invalid phone'); setStatus('OFFLINE'); return; }
    token = (await r.json()).token;
  } catch { showError('Cannot reach server'); setStatus('OFFLINE'); return; }

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(phone)}?token=${encodeURIComponent(token)}`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = async () => {
    setStatus('READY', 'active');
    // Acquire mic once on connect with track muted — pre-warms the stream
    // so startRecording() has zero init latency, but no audio is captured
    // until the button is held (track.enabled = false until then).
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      micStream.getAudioTracks().forEach(t => { t.enabled = false; });
      startViz(micStream);
    } catch { showError('Mic access denied'); }
    micBtn.disabled = false;
    connectBtn.textContent = 'END';
    connectBtn.classList.add('disc');
    connectBtn.onclick = disconnect;
    startTimer();
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      if      (ev.data.startsWith('user:'))  addMsg('user',  ev.data.slice(5));
      else if (ev.data.startsWith('agent:')) addMsg('agent', ev.data.slice(6));
      return;
    }
    if (isEOR(ev.data)) { hideTyping(); streamDone = true; drainQueue(); return; }
    ensureAudioCtx();
    chunksReceived++;
    if (chunksReceived === 1) { nextPlayTime = 0; micBtn.classList.add('playing'); }
    enqueuePCM(ev.data);
  };

  ws.onerror = () => showError('Connection error');
  ws.onclose = (ev) => {
    if (ev.code === 4001) showError('Auth failed — reconnect');
    else if (ev.code === 4000) showError('Invalid phone number');
    setStatus('OFFLINE');
    micBtn.disabled = true;
    waveBars.classList.remove('on');
    connectBtn.textContent = 'CONNECT';
    connectBtn.classList.remove('disc');
    connectBtn.onclick = () => handleConnect();
    stopTimer();
    ws = null;
  };
}

function disconnect() {
  if (mediaRecorder) { mediaRecorder.stop(); mediaRecorder = null; }
  if (micStream)     { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  stopViz();
  if (ws) ws.close();
  ws = null;
}

//  MIC
function startRecording() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!micStream) { showError('Mic not ready'); return; }
  // Unmute the track — no audio was flowing into the OS buffer before this point
  micStream.getAudioTracks().forEach(t => { t.enabled = true; });
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  mediaRecorder  = new MediaRecorder(micStream, { mimeType, bitsPerSecond: 64000 });
  mediaRecorder.ondataavailable = (ev) => {
    if (ev.data.size > 0 && ws?.readyState === WebSocket.OPEN) ws.send(ev.data);
  };
  mediaRecorder.start(100);
  micBtn.classList.add('recording');
  setStatus('RECORDING', 'recording');
}

function stopRecording() {
  if (!mediaRecorder) return;
  // Mute the track immediately — stops audio flowing before MediaRecorder.stop() fires
  if (micStream) micStream.getAudioTracks().forEach(t => { t.enabled = false; });
  mediaRecorder.stop(); mediaRecorder = null;
  micBtn.classList.remove('recording');
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(new Uint8Array([0x00]).buffer);
    setStatus('PROCESSING…', 'active');
    micBtn.disabled = true;
    showTyping();
    streamDone = false; chunksReceived = 0; isPlaying = false; pcmQueue = [];
  }
}

//  WIRING 
function handleConnect() {
  const phone = phoneInput.value.trim();
  if (!phone)              { showError('Enter your phone number'); return; }
  if (!/^\+?\d{7,15}$/.test(phone)) { showError('Enter a valid phone number'); return; }
  connect(phone);
}

connectBtn.onclick = handleConnect;
phoneInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleConnect(); });

micBtn.addEventListener('mousedown',  startRecording);
micBtn.addEventListener('mouseup',    stopRecording);
micBtn.addEventListener('mouseleave', stopRecording);
micBtn.addEventListener('touchstart', e => { e.preventDefault(); startRecording(); }, { passive: false });
micBtn.addEventListener('touchend',   e => { e.preventDefault(); stopRecording(); },  { passive: false });

//  RECEIPT LOGIC 
let _lastReceiptData = null;

/**
 * Parse booking details from agent confirmation messages.
 * Looks for keywords like "confirmed", "booked", service types,
 * dates, times, and customer names.
 */
function parseBookingFromText(text) {
  const lower = text.toLowerCase();

  // Must look like a confirmation
  const isConfirmation = /\b(confirmed|booked|appointment|booking|scheduled|reservation)\b/.test(lower)
    && /\b(confirmed|success|done|scheduled|set up|all set|good to go)\b/.test(lower);
  if (!isConfirmation) return null;

  const data = {};

  // Service type — ordered from most-specific to least-specific
  const servicePatterns = [
    // Qualified service: "basic service", "full car service", etc.
    /\b((?:basic|full|general|major|minor|standard|premium|comprehensive|express)\s+(?:car\s+)?servi(?:ce|cing))\b/i,
    // Specific job types
    /\b(oil\s*(?:and\s*filter\s*)?change|tyre\s+(?:rotation|swap|change|replacement)|wheel\s+(?:alignment|balancing)|brake\s+(?:inspection|service|check|replacement)|battery\s+(?:check|replacement|service)|AC\s+(?:service|regas|repair)|air\s+con(?:ditioning)?\s+(?:service|repair)|car\s+wash|detailing|diagnostics?|engine\s+service|transmission\s+service|coolant\s+(?:flush|service)|clutch\s+(?:service|replacement))\b/i,
  ];
  data.service = 'Automotive Service';
  for (const p of servicePatterns) {
    const m = text.match(p);
    if (m) {
      const raw = m[1].trim();
      data.service = raw.replace(/\b\w/g, c => c.toUpperCase());
      break;
    }
  }

  // Date — "May 5", "5th May", "2026-05-05", "05/05/2026", relative words
  const datePatterns = [
    /(\d{4}-\d{2}-\d{2})/,
    /(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})/,
    /\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s*(?:\d{4})?)\b/i,
    /\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?)\b/i,
    /\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/i,
  ];
  for (const p of datePatterns) {
    const m = text.match(p);
    if (m) {
      let raw = m[1];
      // Convert ISO format (2026-05-05) to a human-readable string
      if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
        const d = new Date(raw + 'T00:00:00');
        raw = d.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
      }
      // Resolve relative day words to actual dates
      const relMap = { today: 0, tomorrow: 1, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6, sunday: 0 };
      const relKey = raw.toLowerCase();
      if (relMap[relKey] !== undefined && relKey !== 'today' && relKey !== 'tomorrow') {
        const now = new Date();
        const target = relMap[relKey]; // day-of-week index (Sun=0)
        const cur = now.getDay();
        const diff = (target - cur + 7) % 7 || 7;
        now.setDate(now.getDate() + diff);
        raw = now.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
      } else if (relKey === 'tomorrow') {
        const now = new Date();
        now.setDate(now.getDate() + 1);
        raw = now.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
      } else if (relKey === 'today') {
        raw = new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
      }
      data.date = raw;
      break;
    }
  }
  if (!data.date) data.date = 'As Scheduled';

  // Time — "10:00 AM", "14:30", "2 PM"
  const timeMatch = text.match(/\b(\d{1,2}:\d{2}\s*(?:AM|PM)?|\d{1,2}\s*(?:AM|PM))\b/i);
  data.time = timeMatch ? timeMatch[1].toUpperCase() : 'As Scheduled';

  // Name — "for [Name]", "[Name]'s booking"
  const namePatterns = [
    /\bfor\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b/,
    /\bname[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b/i,
    /\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'s\s+(?:appointment|booking)\b/,
  ];
  for (const p of namePatterns) {
    const m = text.match(p);
    if (m && !['The','Your','This','Our'].includes(m[1])) { data.name = m[1]; break; }
  }

  // Car / vehicle
  const carMatch = text.match(/\b([A-Z][a-zA-Z]+\s+(?:Swift|Baleno|Celerio|Alto|Dzire|Ertiga|Vitara|Brezza|Fronx|Jimny|Nexon|Altroz|Harrier|Safari|Tiago|Tigor|Punch|Creta|Venue|i20|i10|Aura|Verna|Tucson|Innova|Fortuner|Corolla|Camry|City|Amaze|Jazz|WR-V|Civic|CR-V|Seltos|Sonet|Carnival|EV6|[A-Z0-9]{2,5}))\b/);
  data.vehicle = carMatch ? carMatch[0] : null;

  // Phone (last 4 digits shown)
  const phoneMatch = text.match(/(?:\+91|0)?[789]\d{9}|\b\d{10}\b/);
  data.phone = phoneMatch ? phoneMatch[0] : null;

  // Generate reference number
  data.ref = 'AV-' + Date.now().toString(36).toUpperCase().slice(-6);
  data.generatedAt = new Date().toLocaleString('en-IN', {
    day:'2-digit', month:'short', year:'numeric',
    hour:'2-digit', minute:'2-digit', hour12: true
  });

  return data;
}

function buildReceiptRows(data) {
  const rows = [
    { label: 'Booking Ref', val: data.ref },
    { label: 'Service', val: data.service },
    { label: 'Date', val: data.date },
    { label: 'Time', val: data.time },
  ];
  if (data.name)    rows.splice(1, 0, { label: 'Customer', val: data.name });
  if (data.vehicle) rows.push({ label: 'Vehicle', val: data.vehicle });
  if (data.phone)   rows.push({ label: 'Phone', val: data.phone });
  rows.push({ divider: true });
  rows.push({ label: 'Status', val: 'CONFIRMED' });
  return rows;
}

function renderReceiptCard(data) {
  const rowsEl = document.getElementById('receipt-rows');
  const refEl  = document.getElementById('receipt-ref');
  const rows   = buildReceiptRows(data);

  rowsEl.innerHTML = rows.map(r => {
    if (r.divider) return '<div class="receipt-divider"></div>';
    return `<div class="receipt-row">
      <span class="receipt-row-label">${r.label}</span>
      <span class="receipt-row-val">${r.val}</span>
    </div>`;
  }).join('');

  refEl.textContent = `Generated ${data.generatedAt} · Ref ${data.ref}`;
}

function showReceipt(data) {
  _lastReceiptData = data;
  renderReceiptCard(data);
  document.getElementById('receipt-overlay').classList.add('show');
}

function closeReceipt() {
  document.getElementById('receipt-overlay').classList.remove('show');
}

// Close on backdrop click
document.getElementById('receipt-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeReceipt();
});

function downloadReceiptPDF() {
  if (!_lastReceiptData) return;
  const d = _lastReceiptData;
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF({ unit: 'mm', format: 'a5', orientation: 'portrait' });

  const W = 148, H = 210;
  const lx = 14, rx = W - 14;
  const headerH = 48;

  //  White background
  doc.setFillColor(255, 255, 255);
  doc.rect(0, 0, W, H, 'F');

  //  Black header band 
  doc.setFillColor(0, 0, 0);
  doc.rect(0, 0, W, headerH, 'F');

  // Company name
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(18);
  doc.setTextColor(255, 255, 255);
  doc.text('AUTOVOICE AI', W / 2, 16, { align: 'center' });

  // Subtitle
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(6);
  doc.setTextColor(160, 160, 160);
  doc.text('AUTOMOTIVE SERVICE BOOKING', W / 2, 23, { align: 'center' });

  // "BOOKING CONFIRMED" badge — plain rect + centred text only
  const badgeW = 58, badgeH = 7;
  const badgeX = (W - badgeW) / 2, badgeY = 28;
  doc.setDrawColor(255, 255, 255);
  doc.setLineWidth(0.35);
  doc.rect(badgeX, badgeY, badgeW, badgeH);
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(6.5);
  doc.setTextColor(255, 255, 255);
  doc.text('BOOKING CONFIRMED', W / 2, badgeY + 4.8, { align: 'center' });

  //  Rows
  // Each row: label line (small grey) + value line (bold black) + separator below
  const rows = buildReceiptRows(d).filter(r => !r.divider);
  const rowH = 14;       // total height per row
  const lblOff = 5;      // label baseline offset from row top
  const valOff = 10;     // value baseline offset from row top
  let y = headerH + 7;

  rows.forEach((r) => {
    // Label (small, grey)
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(6);
    doc.setTextColor(150, 150, 150);
    doc.text(r.label.toUpperCase(), lx, y + lblOff);

    // Value (bold, black, right-aligned)
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(8.5);
    doc.setTextColor(0, 0, 0);
    doc.text(String(r.val), rx, y + valOff, { align: 'right' });

    // Separator after the value
    doc.setDrawColor(220, 220, 220);
    doc.setLineWidth(0.2);
    doc.line(lx, y + rowH - 1, rx, y + rowH - 1);

    y += rowH;
  });

  //  Footer divider + ref
  y += 4;
  doc.setDrawColor(0, 0, 0);
  doc.setLineWidth(0.35);
  doc.line(lx, y, rx, y);
  y += 6;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(6);
  doc.setTextColor(120, 120, 120);
  doc.text('REF: ' + d.ref, lx, y);
  doc.text(d.generatedAt, rx, y, { align: 'right' });

  //  Footer note
  doc.setFontSize(6);
  doc.setTextColor(180, 180, 180);
  doc.text('Please present this receipt at the service center.', W / 2, H - 10, { align: 'center' });

  //  Outer border
  doc.setDrawColor(0, 0, 0);
  doc.setLineWidth(0.5);
  doc.rect(0, 0, W, H);

  doc.save('AutoVoice_Receipt_' + d.ref + '.pdf');
}

/**
 * Adds a receipt attachment chip below an agent message bubble
 * and opens the modal.
 */
function injectReceiptIntoMessage(msgEl, data) {
  const bubble = msgEl.querySelector('.msg-bubble');
  if (!bubble) return;

  const chip = document.createElement('div');
  chip.className = 'receipt-attachment';
  chip.innerHTML = `
    <span class="receipt-attachment-icon">📄</span>
    <div class="receipt-attachment-info">
      <div class="receipt-attachment-name">Booking Receipt</div>
      <div class="receipt-attachment-meta">Ref: ${data.ref} · Tap to view &amp; download</div>
    </div>
    <span class="receipt-attachment-open">VIEW →</span>
  `;
  chip.addEventListener('click', () => showReceipt(data));
  bubble.appendChild(chip);

  // Auto-open modal
  showReceipt(data);
}