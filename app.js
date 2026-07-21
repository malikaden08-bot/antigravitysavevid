/* ===== SaveVid — app.js (Real API Edition) ===== */

const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  ? 'http://localhost:3000'
  : '';

// ── Navbar scroll ─────────────────────────────────────────────────────────────
const navbar = document.getElementById('navbar');
window.addEventListener('scroll', () => {
  navbar.classList.toggle('scrolled', window.scrollY > 30);
});

// ── Mobile menu ───────────────────────────────────────────────────────────────
const hamburger = document.getElementById('hamburger');
const mobileMenu = document.getElementById('mobileMenu');
hamburger.addEventListener('click', () => mobileMenu.classList.toggle('open'));
function closeMobile() { mobileMenu.classList.remove('open'); }
document.getElementById('navCta').addEventListener('click', () => {
  document.getElementById('videoUrl').focus();
  document.getElementById('hero').scrollIntoView({ behavior: 'smooth' });
});

// ── Quality chips (UI only — actual quality chosen via format_spec) ───────────
let selectedQuality = '1080p';
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    selectedQuality = chip.textContent.trim();
  });
});

// ── Paste button ──────────────────────────────────────────────────────────────
document.getElementById('pasteBtn').addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    const input = document.getElementById('videoUrl');
    input.value = text;
    input.dispatchEvent(new Event('input'));
    input.focus();
    showToast('✅ URL pasted from clipboard!');
  } catch {
    showToast('❌ Clipboard permission denied — paste manually.');
  }
});

// ── Platform detection ────────────────────────────────────────────────────────
function detectPlatform(url) {
  const u = url.toLowerCase();
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'YouTube';
  if (u.includes('instagram.com'))                          return 'Instagram';
  if (u.includes('tiktok.com'))                             return 'TikTok';
  if (u.includes('facebook.com') || u.includes('fb.com') || u.includes('fb.watch')) return 'Facebook';
  if (u.includes('pinterest.com') || u.includes('pin.it')) return 'Pinterest';
  return null;
}

function isValidUrl(str) {
  try { return ['http:', 'https:'].includes(new URL(str).protocol); }
  catch { return false; }
}

// ── Live platform badge highlight ─────────────────────────────────────────────
const urlInput = document.getElementById('videoUrl');
const platformIcons = document.querySelectorAll('.pi-icon');
const platformOrder = ['YouTube', 'Instagram', 'TikTok', 'Facebook', 'Pinterest'];

urlInput.addEventListener('input', () => {
  const platform = detectPlatform(urlInput.value.trim());
  platformIcons.forEach((icon, i) => {
    const active = platform === platformOrder[i];
    icon.style.transform  = active ? 'scale(1.2) translateY(-4px)' : '';
    icon.style.boxShadow  = active ? '0 8px 24px rgba(124,58,237,0.2)' : '';
    icon.style.background = active ? 'white' : '';
  });
});

// ── Download trigger ──────────────────────────────────────────────────────────
document.getElementById('downloadBtn').addEventListener('click', handleDownload);
urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleDownload(); });

async function handleDownload() {
  const url = urlInput.value.trim();

  if (!url)                { showToast('⚠️ Please paste a video URL first.'); shakeInput(); return; }
  if (!isValidUrl(url))    { showToast('⚠️ Please enter a valid URL starting with http(s).'); shakeInput(); return; }
  if (!detectPlatform(url)){ showToast('⚠️ Unsupported platform. Try YouTube, Instagram, TikTok, Facebook or Pinterest.'); shakeInput(); return; }

  await fetchVideoInfo(url);
}

function shakeInput() {
  urlInput.style.animation = 'none';
  requestAnimationFrame(() => { urlInput.style.animation = 'shake 0.5s ease'; });
}

const shakeStyle = document.createElement('style');
shakeStyle.textContent = `
@keyframes shake{0%,100%{transform:translateX(0)}15%{transform:translateX(-8px)}30%{transform:translateX(8px)}45%{transform:translateX(-6px)}60%{transform:translateX(6px)}75%{transform:translateX(-3px)}90%{transform:translateX(3px)}}`;
document.head.appendChild(shakeStyle);

// ── Fetch video info from real backend ────────────────────────────────────────
async function fetchVideoInfo(url, attempt = 1) {
  const MAX_ATTEMPTS = 3;
  showProgress('Connecting to source…', 10);

  try {
    updateProgress('Fetching video metadata…', 35);

    const res = await fetch(`${API_BASE}/api/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    updateProgress('Analyzing available formats…', 65);
    const data = await res.json();

    // --- Connection reset: auto-retry up to MAX_ATTEMPTS ---
    if ((res.status === 503 || (data.error && data.error.includes('10054'))) && attempt < MAX_ATTEMPTS) {
      updateProgress(`Connection reset — retrying (${attempt}/${MAX_ATTEMPTS})…`, 20);
      showToast(`⚠️ Connection reset — retrying in 3 s… (attempt ${attempt}/${MAX_ATTEMPTS})`);
      await sleep(3000);
      return fetchVideoInfo(url, attempt + 1);
    }

    if (!res.ok || data.error) {
      hideProgress();
      showToast(`❌ ${data.error || 'Unknown error occurred.'}`);
      return;
    }

    updateProgress('Building download options…', 90);
    await sleep(400);
    updateProgress('Done!', 100);
    await sleep(450);

    hideProgress();
    showResult(data, url);

  } catch (err) {
    if (err.message && err.message.includes('fetch') && attempt < MAX_ATTEMPTS) {
      updateProgress(`Network error — retrying (${attempt}/${MAX_ATTEMPTS})…`, 15);
      await sleep(2000);
      return fetchVideoInfo(url, attempt + 1);
    }
    hideProgress();
    if (err.message && err.message.includes('fetch')) {
      showToast('❌ Cannot reach server.');
    } else {
      showToast(`❌ Error: ${err.message}`);
    }
  }
}

// ── Show real result ──────────────────────────────────────────────────────────
function showResult(data, url) {
  const section = document.getElementById('resultSection');

  document.getElementById('resultPlatform').textContent = data.platform || detectPlatform(url) || 'Video';
  document.getElementById('resultTitle').textContent = data.title || 'Untitled Video';

  // Meta row
  const metaItems = [];
  if (data.duration)   metaItems.push(`Duration: ${data.duration}`);
  if (data.max_height) metaItems.push(`Max: ${data.max_height >= 2160 ? '4K UHD' : data.max_height + 'p'}`);
  if (data.uploader)   metaItems.push(`By: ${data.uploader}`);
  if (!data.ffmpeg)    metaItems.push('No ffmpeg — 4K merge disabled');
  document.getElementById('resultMeta').innerHTML = metaItems
    .map((m, i) => i === 0 ? `<span>${m}</span>` : `<span>•</span><span>${m}</span>`)
    .join('');

  // Thumbnail
  const thumbEl = document.getElementById('resultThumb');
  if (data.thumbnail) {
    thumbEl.innerHTML = `<img src="${data.thumbnail}" alt="thumbnail"
      style="width:100%;height:100%;object-fit:cover;border-radius:inherit;"
      onerror="this.style.display='none'"/>`;
  } else {
    thumbEl.innerHTML = `<div class="thumb-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48"><path d="M15 10l4.553-2.269A1 1 0 0121 8.679V15.32a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"/></svg></div>`;
  }

  // Download options — use session_id + fmt_id (no raw format specs in URL)
  const downloads = document.getElementById('resultDownloads');
  downloads.innerHTML = '';

  (data.formats || []).forEach(fmt => {
    const isAudio = fmt.icon === 'audio';
    const link    = document.createElement('a');
    link.className = 'dl-option';
    link.href      = buildDownloadUrl(url, data.session_id, fmt.id, data.title, isAudio);
    link.target    = '_blank';
    link.rel       = 'noopener';
    link.title     = `Download: ${fmt.label}`;
    link.addEventListener('click', () => {
      showToast(`⏬ Preparing "${fmt.label}" download... Please wait a moment while the server processes your video.`);
    });

    link.innerHTML = `
      ${isAudio
        ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`
        : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v13m0 0l-4-4m4 4l4-4"/><path d="M2 17l.621 2.485A2 2 0 004.561 21h14.878a2 2 0 001.94-1.515L22 17"/></svg>`
      }
      <div>
        <span class="dl-quality">${fmt.label}</span>
        <span class="dl-size">${fmt.size || ''}</span>
      </div>`;

    downloads.appendChild(link);
  });

  section.style.display = 'block';
  setTimeout(() => section.scrollIntoView({ behavior: 'smooth', block: 'center' }), 100);
  showToast(`✅ "${data.title.slice(0, 40)}…" is ready!`);
}

function buildDownloadUrl(videoUrl, sessionId, fmtId, title, isAudio) {
  const params = new URLSearchParams({
    url:        videoUrl,
    session_id: sessionId || '',
    fmt_id:     fmtId     || '0',
    title:      title     || 'video',
    audio:      isAudio   ? '1' : '0',
  });
  return `${API_BASE}/api/download?${params.toString()}`;
}

// ── Progress modal ────────────────────────────────────────────────────────────
let _progressRaf;

function showProgress(msg, pct) {
  const modal = document.getElementById('progressModal');
  const fill  = document.getElementById('progressFill');
  const pctEl = document.getElementById('progressPercent');
  const sub   = document.getElementById('progressSub');
  const title = document.getElementById('progressTitle');

  title.textContent = 'Fetching video info…';
  sub.textContent   = msg;
  fill.style.width  = pct + '%';
  pctEl.textContent = pct + '%';
  modal.style.display = 'flex';
}

function updateProgress(msg, pct) {
  document.getElementById('progressSub').textContent    = msg;
  document.getElementById('progressFill').style.width   = pct + '%';
  document.getElementById('progressPercent').textContent = pct + '%';
}

function hideProgress() {
  document.getElementById('progressModal').style.display = 'none';
}

// ── FAQ accordion ─────────────────────────────────────────────────────────────
function toggleFaq(el) {
  const wasOpen = el.classList.contains('open');
  document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('open'));
  if (!wasOpen) el.classList.add('open');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 4000);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Particle canvas ───────────────────────────────────────────────────────────
(function initParticles() {
  const canvas = document.getElementById('particleCanvas');
  const ctx    = canvas.getContext('2d');
  let particles = [];

  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  window.addEventListener('resize', () => { resize(); buildParticles(); });
  resize();

  const COLORS = [
    'rgba(124,58,237,0.5)', 'rgba(236,72,153,0.45)',
    'rgba(59,130,246,0.4)', 'rgba(20,184,166,0.35)',
    'rgba(245,158,11,0.35)',
  ];

  function buildParticles() {
    particles = [];
    const count = Math.min(60, Math.floor(window.innerWidth / 20));
    for (let i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: Math.random() * 3 + 1,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        opacity: Math.random() * 0.5 + 0.2,
      });
    }
  }

  buildParticles();

  function drawLines() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx   = particles[i].x - particles[j].x;
        const dy   = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 130) {
          ctx.beginPath();
          ctx.strokeStyle = `rgba(124,58,237,${0.08 * (1 - dist / 130)})`;
          ctx.lineWidth   = 0.8;
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    particles.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0)           p.x = canvas.width;
      if (p.x > canvas.width)  p.x = 0;
      if (p.y < 0)           p.y = canvas.height;
      if (p.y > canvas.height) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle   = p.color;
      ctx.globalAlpha = p.opacity;
      ctx.fill();
      ctx.globalAlpha = 1;
    });
    drawLines();
    requestAnimationFrame(animate);
  }

  animate();
})();

// ── Scroll-reveal ─────────────────────────────────────────────────────────────
const reveals = document.querySelectorAll('.feature-card, .platform-card, .step-card, .faq-item');
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.style.opacity   = '1';
      e.target.style.transform = 'translateY(0)';
    }
  });
}, { threshold: 0.1 });

reveals.forEach(el => {
  el.style.opacity    = '0';
  el.style.transform  = 'translateY(30px)';
  el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
  observer.observe(el);
});

// ── 3D tilt on download box ───────────────────────────────────────────────────
const downloadBox = document.getElementById('downloadBox');
downloadBox.addEventListener('mousemove', e => {
  const rect = downloadBox.getBoundingClientRect();
  const dx = (e.clientX - rect.left - rect.width  / 2) / (rect.width  / 2);
  const dy = (e.clientY - rect.top  - rect.height / 2) / (rect.height / 2);
  downloadBox.style.transform = `perspective(900px) rotateX(${-dy * 3}deg) rotateY(${dx * 3}deg) translateY(-4px)`;
});
downloadBox.addEventListener('mouseleave', () => { downloadBox.style.transform = ''; });

// ── Server health check on load ───────────────────────────────────────────────
window.addEventListener('load', async () => {
  try {
    const res  = await fetch(`${API_BASE}/api/status`);
    const data = await res.json();
    if (data.status === 'ok') {
      const ffmpegWarn = !data.ffmpeg
        ? ' ⚠️ ffmpeg not found — 4K merge disabled.'
        : '';
      showToast(`✅ Server connected · yt-dlp v${data.yt_dlp}${ffmpegWarn}`);
    }
  } catch {
    showToast('⚠️ Server not reachable.');
  }
});
