// ── Configure marked.js ───────────────────────────────────────────────────
marked.setOptions({
  breaks: true,           // newline → <br>
  gfm: true,              // GitHub Flavored Markdown
  highlight: function (code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value; } catch { }
    }
    return hljs.highlightAuto(code).value;
  }
});

// Custom renderer: wrap <pre><code> with our copy-button wrapper
const renderer = new marked.Renderer();
renderer.code = function (code, lang) {
  const highlighted = (lang && hljs.getLanguage(lang))
    ? hljs.highlight(code, { language: lang }).value
    : hljs.highlightAuto(code).value;
  const label = lang || 'code';
  // Escape for data attribute
  const escaped = code.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  return `<div class="code-block-wrap">
      <pre><code class="hljs language-${label}">${highlighted}</code></pre>
      <button class="copy-btn" onclick="copyCode(this, &quot;${escaped}&quot;)">Copy</button>
      <span class="code-lang-label">${label}</span>
    </div>`;
};
marked.use({ renderer });

// ── State ─────────────────────────────────────────────────────────────────
let sessions = [];
let activeSessionId = null;
let activeSession = null;
let sidebarOpen = true;
let isSending = false;
let articleRawStore = {};  // simpan raw markdown per sessionId untuk copy

// ── Init ──────────────────────────────────────────────────────────────────
async function init() {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'include' });
    if (!res.ok) { window.location.href = '/login'; return; }
  } catch {
    window.location.href = '/login';
    return;
  }
  await loadSessions();
}

// ── Sessions ──────────────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const res = await fetch('/api/chat/sessions', { credentials: 'include' });
    if (!res.ok) throw new Error('Server error');
    sessions = await res.json();
    renderSidebar();
  } catch (err) {
    showToast('Gagal memuat sesi: ' + err.message, 'error');
  }
}

function renderSidebar() {
  const list = document.getElementById('chatList');
  if (sessions.length === 0) {
    list.innerHTML = `<div class="sidebar-empty">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <br>Belum ada sesi.<br>Klik <strong style="color:rgba(255,255,255,.65)">New Chat</strong> untuk mulai.
      </div>`;
    return;
  }
  list.innerHTML = sessions.map(s => `
      <div class="chat-item ${s.id === activeSessionId ? 'active' : ''}"
           onclick="loadSession('${escAttr(s.id)}')"
           title="${escAttr(s.topic)}">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <span class="chat-item-text">${escHtml(s.title)}</span>
        ${!s.is_ready ? '<span style="font-size:.6rem;color:#f59e0b;flex-shrink:0;margin-left:4px;">⏳</span>' : ''}
        <button class="btn-delete-session" onclick="openDeleteModal(event, '${escAttr(s.id)}')" title="Hapus Sesi">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
        </button>
      </div>
    `).join('');
}

async function loadSession(sessionId) {
  if (activeSessionId === sessionId) return;
  activeSessionId = sessionId;

  const session = sessions.find(s => s.id === sessionId);
  activeSession = session || null;
  document.getElementById('topbarTitle').textContent = 'YDSF AI Chat';

  // Update header
  const header = document.getElementById('chatHeader');
  const headerTitle = document.getElementById('chatHeaderTitle');
  const headerSubtitle = document.getElementById('chatHeaderSubtitle');
  if (session) {
    headerTitle.textContent = session.title || session.topic || 'Artikel';
    headerSubtitle.textContent = '';
    header.classList.remove('hidden');
  }

  const badge = document.getElementById('topbarBadge');
  if (badge) badge.style.display = 'none';

  renderSidebar();

  // Loading state
  const container = document.getElementById('chatMessages');
  container.innerHTML = `<div class="chat-empty">
      <div class="crawl-ring" style="margin:auto;width:32px;height:32px;border-width:3px;"></div>
    </div>`;

  try {
    const res = await fetch(`/api/chat/sessions/${sessionId}/messages`, { credentials: 'include' });
    if (!res.ok) throw new Error('Gagal memuat pesan');
    const messages = await res.json();
    renderMessages(messages);
  } catch (err) {
    showToast('Gagal load pesan: ' + err.message, 'error');
    renderMessages([]);
  }

  setInputEnabled(true);
}

// ── Render Messages ───────────────────────────────────────────────────────
async function renderMessages(messages) {
  const container = document.getElementById('chatMessages');
  if (!messages || messages.length === 0) {
    container.innerHTML = `<div class="chat-empty">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#2d6a4f" stroke-width="1.4">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <p style="font-size:15px;color:#374151;">Sesi siap!</p>
        <span>Klik <strong>Generate Artikel</strong> di bawah untuk menghasilkan artikel otomatis.</span>
      </div>`;
    return;
  }

  // Render messages - for articles, display standalone; for others, use bubble
  let html = '';
  for (const m of messages) {
    const isArticle = m.role === 'bot' && /^##\s/m.test(m.content);
    if (isArticle) {
      articleRawStore[m.session_id || activeSessionId] = m.content;
      html += `<div class="article-wrapper">
          ${buildArticleCard(m.content, activeSession?.topic)}
        </div>`;
    } else {
      html += buildBubble(m.role, m.content, m.created_at, m.session_id);
    }
  }

  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

// Bot messages rendered as article card if multi-section, else prose bubble
function buildBubble(role, text, timestamp, sessionId) {
  const time = timestamp
    ? new Date(timestamp).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
    : new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });

  let content;
  if (role === 'user') {
    content = `<div class="msg-bubble">${escHtml(text).replace(/\n/g, '<br>')}</div>`;
  } else {
    // Bot: plain answer only (articles handled separately in renderMessages)
    content = `<div class="msg-bubble">${marked.parse(text)}</div>`;
  }

  return `<div class="msg-row ${role}">
      <div class="msg-avatar">${role === 'user' ? 'U' : 'AI'}</div>
      <div style="flex:1;min-width:0;">
        ${content}
        <div class="msg-meta">
          <span class="msg-time">${time}</span>
        </div>
      </div>
    </div>`;
}

// ── Article Card Builder ─────────────────────────────────────────────────
function buildArticleCard(markdown, topic) {
  // Split markdown by ## headings → each gets .article-section wrapper
  const sections = markdown.split(/(?=^## )/m);
  const sectionsHtml = sections.map(block => {
    if (!block.trim()) return '';
    const parsed = marked.parse(block);
    return `<div class="article-section">${parsed}</div>`;
  }).join('');

  const badge = topic
    ? `<span class="article-topic-badge">
           <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
           ${escHtml(topic)}
         </span>`
    : '';

  const actions = `
    <div class="article-actions">
      <button class="btn-copy-article" onclick="copyArticleCard(this)" data-html2canvas-ignore="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Salin Artikel
      </button>
      <button class="btn-download-pdf" onclick="downloadPDF(this)" data-html2canvas-ignore="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Download PDF
      </button>
    </div>
  `;

  return `<div class="article-card">
      <div class="article-card-header">
        <div>${badge}</div>
        ${actions}
      </div>
      <div class="article-card-body">
        ${sectionsHtml}
      </div>
    </div>`;
}

// Copy article to clipboard
function copyArticleCard(btn) {
  const raw = activeSessionId && articleRawStore[activeSessionId]
    ? articleRawStore[activeSessionId]
    : btn.previousElementSibling?.innerText || '';
  navigator.clipboard.writeText(raw).then(() => {
    btn.classList.add('copied');
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Tersalin!`;
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Salin Artikel`;
    }, 2500);
  });
}

// ── Download PDF ─────────────────────────────────────────────────────────
function downloadPDF(btn) {
  const articleCard = btn.closest('.article-card');
  if (!articleCard) return;

  const opt = {
    margin: [15, 12, 15, 12],
    filename: 'Artikel_YDSF.pdf',
    image: { type: 'jpeg', quality: 0.98 },
    html2canvas: { scale: 2, useCORS: true, letterRendering: true },
    jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
  };

  // Use title from topbar or active session
  const topicText = activeSession?.title || document.getElementById('topbarTitle').innerText;
  if (topicText && topicText !== 'Artikel') {
    opt.filename = `Artikel_YDSF_${topicText.replace(/[^a-zA-Z0-9]/g, '_')}.pdf`;
  }

  const originalText = btn.innerHTML;
  btn.innerHTML = `<span class="crawl-ring" style="width:14px;height:14px;border-width:2px;display:inline-block;margin:0 6px 0 0;vertical-align:middle;"></span> Memproses PDF...`;
  btn.disabled = true;

  // Remove box-shadow/max-width temporarily for cleaner PDF
  const origMaxWidth = articleCard.style.maxWidth;
  const origPadding = articleCard.style.padding;
  const origMargin = articleCard.style.margin;

  articleCard.style.maxWidth = 'none';
  articleCard.style.padding = '10px 20px';
  articleCard.style.margin = '0';

  html2pdf().set(opt).from(articleCard).save().then(() => {
    btn.innerHTML = originalText;
    btn.disabled = false;

    // Restore styles
    articleCard.style.maxWidth = origMaxWidth;
    articleCard.style.padding = origPadding;
    articleCard.style.margin = origMargin;
  }).catch(err => {
    console.error('PDF error:', err);
    showToast('Gagal mengunduh PDF', 'error');
    btn.innerHTML = originalText;
    btn.disabled = false;

    // Restore styles even on error
    articleCard.style.maxWidth = origMaxWidth;
    articleCard.style.padding = origPadding;
    articleCard.style.margin = origMargin;
  });
}

// ── Generate Article ─────────────────────────────────────────────────────
async function generateArticle() {
  if (isSending || !activeSessionId) return;
  isSending = true;
  setInputEnabled(false);

  const container = document.getElementById('chatMessages');
  const emptyEl = container.querySelector('.chat-empty');
  if (emptyEl) emptyEl.remove();

  // Show processing state in chat area
  container.innerHTML = `
      <div class="chat-empty" id="processingState" style="gap:20px;padding:80px 40px;">
        <div class="crawl-ring" style="width:44px;height:44px;border-width:4px;"></div>
        <p style="font-size:15px;color:#1e3326;margin:0;">Menghasilkan Artikel Panjang…</p>
        <span style="font-size:13px;color:#9ca3af;max-width:320px;text-align:center;">
          AI sedang menulis artikel mendalam berdasarkan data riset.<br/>
          Mohon tunggu 1–3 menit. Artikel akan muncul secara otomatis.
        </span>
        <span id="pollTimer" style="font-size:12px;color:#b0b8b4;margin-top:4px;">Menghubungkan ke server...</span>
      </div>`;
  container.scrollTop = container.scrollHeight;

  try {
    const res = await fetch('/api/chat/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ session_id: activeSessionId }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.message || err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    
    if (data.status === 'processing') {
      // Start polling
      await pollSessionStatus(activeSessionId, activeSession?.topic);
    } else {
      // Fallback if it returned immediately (shouldn't happen with new API)
      await loadSession(activeSessionId);
    }

  } catch (err) {
    container.innerHTML = `
        <div class="chat-empty" style="gap:14px;padding:80px 40px;">
          <p style="color:#dc2626;font-size:15px;font-weight:600;">⚠️ Gagal generate artikel</p>
          <span style="font-size:13px;color:#6b7280;max-width:360px;text-align:center;">
            ${escHtml(err.message)}
          </span>
        </div>`;
    showToast(err.message, 'error');
  } finally {
    isSending = false;
    setInputEnabled(true);
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────
let sessionToDelete = null;

function openDeleteModal(e, sessionId) {
  if (e) {
    e.stopPropagation(); // Mencegah loadSession terpanggil
  }
  sessionToDelete = sessionId;
  document.getElementById('deleteConfirmModal').classList.add('open');
}

function closeDeleteModal() {
  document.getElementById('deleteConfirmModal').classList.remove('open');
  sessionToDelete = null;
}

async function confirmDelete() {
  if (!sessionToDelete) return;
  
  const btn = document.getElementById('btnConfirmDelete');
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = 'Menghapus...';

  try {
    const res = await fetch(`/api/chat/sessions/${sessionToDelete}`, {
      method: 'DELETE',
      credentials: 'include'
    });

    if (!res.ok) throw new Error('Gagal menghapus di server');

    // Update local state
    sessions = sessions.filter(s => s.id !== sessionToDelete);
    
    // Jika yang dihapus adalah session aktif, reset UI
    if (activeSessionId === sessionToDelete) {
      activeSessionId = null;
      activeSession = null;
      document.getElementById('chatMessages').innerHTML = `<div class="chat-empty" id="emptyState">
          <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#1e3326" stroke-width="1.3">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <p>Selamat datang di YDSF Article Generator</p>
          <span>Sesi telah dihapus. Silakan buat sesi baru atau pilih sesi lain.</span>
        </div>`;
      document.getElementById('chatHeader').classList.add('hidden');
      document.getElementById('topbarTitle').textContent = 'YDSF AI Chat';
    }

    renderSidebar();
    showToast('Sesi berhasil dihapus', 'success');
    closeDeleteModal();

  } catch (err) {
    showToast('Gagal menghapus: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

function openModal() {
  document.getElementById('newChatModal').classList.add('open');
  document.getElementById('topicInput').value = '';
  
  // Reset advanced fields
  document.getElementById('lengthInput').value = 'medium';
  document.getElementById('modelInput').value = 'llama-3.3-70b-versatile';
  document.getElementById('infoInput').value = '';
  document.getElementById('pointsInput').value = '';
  document.getElementById('seoInput').value = '';
  document.getElementById('crawlToggle').checked = true;
  
  // Hide advanced options by default
  const adv = document.getElementById('advancedOptions');
  adv.classList.remove('show');
  document.getElementById('advancedToggleIcon').style.transform = 'rotate(0deg)';

  document.getElementById('crawlLoading').classList.remove('show');
  document.getElementById('modalActions').style.display = 'flex';
  document.getElementById('btnStart').disabled = false;
  setTimeout(() => document.getElementById('topicInput').focus(), 120);
}

function closeModal() {
  document.getElementById('newChatModal').classList.remove('open');
}

function toggleAdvancedOptions() {
  const adv = document.getElementById('advancedOptions');
  const icon = document.getElementById('advancedToggleIcon');
  const isShow = adv.classList.toggle('show');
  icon.style.transform = isShow ? 'rotate(180deg)' : 'rotate(0deg)';
}

function handleModalKey(e) {
  if (e.key === 'Enter') { e.preventDefault(); startNewChat(); }
  if (e.key === 'Escape') closeModal();
}

async function startNewChat() {
  const topic = document.getElementById('topicInput').value.trim();
  if (!topic) { document.getElementById('topicInput').focus(); return; }

  // Gather advanced config
  const length = document.getElementById('lengthInput').value;
  const model_choice = document.getElementById('modelInput').value;
  const additional_info = document.getElementById('infoInput').value.trim();
  const key_points_raw = document.getElementById('pointsInput').value.trim();
  const seo_keywords_raw = document.getElementById('seoInput').value.trim();
  const use_crawling = document.getElementById('crawlToggle').checked;

  const key_points = key_points_raw ? key_points_raw.split(',').map(s => s.trim()) : null;
  const seo_keywords = seo_keywords_raw ? seo_keywords_raw.split(',').map(s => s.trim()) : null;

  const config = {
    topic,
    length,
    model_choice,
    additional_info: additional_info || null,
    key_points,
    seo_keywords,
    use_crawling
  };

  document.getElementById('btnStart').disabled = true;
  document.getElementById('modalActions').style.display = 'none';
  document.getElementById('crawlLoading').classList.add('show');

  try {
    // 1. POST /sessions — langsung return, tidak tunggu lama
    const res = await fetch('/api/chat/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ topic, config }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.message || err.detail || `HTTP ${res.status}`);
    }
    const response = await res.json();

    // response = { session: {...}, status: "processing" }
    const newSession = response.session || response;

    sessions.unshift(newSession);
    activeSession = newSession;
    activeSessionId = newSession.id;

    // Update header & topbar
    const header = document.getElementById('chatHeader');
    const headerTitle = document.getElementById('chatHeaderTitle');
    const headerSubtitle = document.getElementById('chatHeaderSubtitle');
    headerTitle.textContent = newSession.title || newSession.topic || 'Artikel';
    if (headerSubtitle) headerSubtitle.textContent = '';
    header.classList.remove('hidden');
    document.getElementById('topbarTitle').textContent = 'YDSF AI Chat';

    renderSidebar();

    // 2. Tutup modal, tampilkan loading di area chat
    closeModal();
    const container = document.getElementById('chatMessages');
    container.innerHTML = `
        <div class="chat-empty" id="processingState" style="gap:20px;padding:80px 40px;">
          <div class="crawl-ring" style="width:44px;height:44px;border-width:4px;"></div>
          <p style="font-size:15px;color:#1e3326;margin:0;">Sedang Mengumpulkan & Membuat Artikel…</p>
          <span style="font-size:13px;color:#9ca3af;max-width:320px;text-align:center;">
            AI sedang crawl dari berbagai sumber dan generate artikel.<br/>
            Mohon tunggu 3–5 menit. Halaman akan otomatis update.
          </span>
          <span id="pollTimer" style="font-size:12px;color:#b0b8b4;margin-top:4px;">Mengecek status...</span>
        </div>`;
    container.scrollTop = container.scrollHeight;

    // 3. Polling status
    await pollSessionStatus(newSession.id, newSession.topic);

  } catch (err) {
    closeModal();
    showToast('Gagal membuat sesi: ' + err.message, 'error');
  }
}

// Poll GET /sessions/{id}/status sampai ready atau error
async function pollSessionStatus(sessionId, topic) {
  const INTERVAL_MS = 4000;   // cek setiap 4 detik
  const MAX_TRIES = 75;     // max ~5 menit
  let tries = 0;

  const timerEl = document.getElementById('pollTimer');

  return new Promise((resolve) => {
    const interval = setInterval(async () => {
      tries++;
      if (timerEl) timerEl.textContent = `Sudah menunggu ${tries * 4} detik…`;

      try {
        const res = await fetch(`/api/chat/sessions/${sessionId}/status`, {
          credentials: 'include',
        });
        if (!res.ok) return; // skip, coba lagi nanti

        const status = await res.json();

        if (status.processing_status === 'ready' && status.has_article) {
          clearInterval(interval);
          // Update session di list agar ⏳ hilang
          const idx = sessions.findIndex(s => s.id === sessionId);
          if (idx !== -1) { sessions[idx].is_ready = true; renderSidebar(); }
          activeSession = sessions.find(s => s.id === sessionId) || activeSession;
          // Load artikel dari DB
          await loadSession(sessionId);
          showToast('Artikel berhasil dibuat! 🎉', 'success');
          resolve('done');

        } else if (status.processing_status === 'error') {
          clearInterval(interval);
          const container = document.getElementById('chatMessages');
          container.innerHTML = `
              <div class="chat-empty" style="gap:14px;padding:80px 40px;">
                <p style="color:#dc2626;font-size:15px;font-weight:600;">⚠️ Gagal membuat artikel</p>
                <span style="font-size:13px;color:#6b7280;max-width:360px;text-align:center;">
                  ${escHtml(status.error_message || 'Terjadi error saat proses.')}
                </span>
              </div>`;
          showToast('Gagal: ' + (status.error_message || 'Error'), 'error');
          resolve('error');

        } else if (tries >= MAX_TRIES) {
          clearInterval(interval);
          const container = document.getElementById('chatMessages');
          container.innerHTML = `
              <div class="chat-empty" style="gap:14px;padding:80px 40px;">
                <p style="color:#f59e0b;font-size:15px;font-weight:600;">⏱ Proses terlalu lama</p>
                <span style="font-size:13px;color:#6b7280;">Coba refresh halaman dan buka sesi dari sidebar.</span>
              </div>`;
          showToast('Timeout menunggu artikel', 'error');
          resolve('timeout');
        }
      } catch (e) {
        // Network error saat polling — lanjutkan coba
      }
    }, INTERVAL_MS);
  });
}

// ── Copy code ──────────────────────────────────────────────────────────────
function copyCode(btn, code) {
  const decoded = code.replace(/&amp;/g, '&').replace(/&quot;/g, '"');
  navigator.clipboard.writeText(decoded).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
  });
}

// ── UI Helpers ────────────────────────────────────────────────────────────
function setGenerateEnabled(enabled) {
  // kept for background logic but button is gone
}

function setInputEnabled(enabled) {
  // kept for background logic
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  
  if (window.innerWidth <= 768) {
    // Mobile: Toggle drawer
    const isOpen = sidebar.classList.toggle('open');
    if (overlay) overlay.classList.toggle('show', isOpen);
  } else {
    // Desktop: Toggle collapsed state
    sidebarOpen = !sidebarOpen;
    sidebar.classList.toggle('collapsed', !sidebarOpen);
  }
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
  window.location.href = '/login';
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function escAttr(str) {
  if (!str) return '';
  return str.replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// ── Toast ─────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  
  let icon = '';
  if (type === 'success') {
    icon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
  } else if (type === 'error') {
    icon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
  } else {
    icon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
  }

  el.innerHTML = `${icon}<span>${escHtml(msg)}</span>`;
  container.appendChild(el);

  // Auto remove after 4s
  setTimeout(() => {
    el.classList.add('fade-out');
    setTimeout(() => el.remove(), 500);
  }, 4000);
}

// ── Close modal on backdrop click ─────────────────────────────────────────
document.getElementById('newChatModal').addEventListener('click', function (e) {
  if (e.target === this) closeModal();
});

// ── Boot ──────────────────────────────────────────────────────────────────
init();

