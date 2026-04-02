// ── Configure marked.js ───────────────────────────────────────────────────
  marked.setOptions({
    breaks: true,           // newline → <br>
    gfm: true,              // GitHub Flavored Markdown
    highlight: function(code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try { return hljs.highlight(code, { language: lang }).value; } catch {}
      }
      return hljs.highlightAuto(code).value;
    }
  });

  // Custom renderer: wrap <pre><code> with our copy-button wrapper
  const renderer = new marked.Renderer();
  renderer.code = function(code, lang) {
    const highlighted = (lang && hljs.getLanguage(lang))
      ? hljs.highlight(code, { language: lang }).value
      : hljs.highlightAuto(code).value;
    const label = lang || 'code';
    // Escape for data attribute
    const escaped = code.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
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
        ${!s.is_ready ? '<span style="font-size:.6rem;color:#f59e0b;flex-shrink:0">⏳</span>' : ''}
      </div>
    `).join('');
  }

  async function loadSession(sessionId) {
    if (activeSessionId === sessionId) return;
    activeSessionId = sessionId;

    const session = sessions.find(s => s.id === sessionId);
    activeSession = session || null;
    document.getElementById('topbarTitle').textContent = session?.title || 'Artikel';

    // Update header
    const header = document.getElementById('chatHeader');
    const headerTitle = document.getElementById('chatHeaderTitle');
    const headerSubtitle = document.getElementById('chatHeaderSubtitle');
    if (session) {
      headerTitle.textContent = session.title || session.topic || 'Artikel';
      headerSubtitle.textContent = `Topik: ${session.topic || 'N/A'}`;
      header.classList.remove('hidden');
    }

    const badge = document.getElementById('topbarBadge');
    if (session?.topic) {
      badge.textContent = session.topic;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }

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
        let sourcesHtml = '';
        
        // Fetch sources for this article
        try {
          const sourcesRes = await fetch(`/api/chat/sessions/${activeSessionId}/sources`, {
            credentials: 'include'
          });
          if (sourcesRes.ok) {
            const sourcesData = await sourcesRes.json();
            if (sourcesData.sources && sourcesData.sources.length > 0) {
              sourcesHtml = buildSourcesSection(sourcesData.sources);
            }
          }
        } catch (err) {
          // Silently fail if sources can't be fetched
        }
        
        html += `<div class="article-wrapper">
          ${buildArticleCard(m.content, activeSession?.topic)}
          ${sourcesHtml}
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
      content = `<div class="msg-bubble">${escHtml(text).replace(/\n/g,'<br>')}</div>`;
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

    return `<div class="article-card">
      ${badge}
      ${sectionsHtml}
    </div>`;
  }

  // ── Build Sources Section ────────────────────────────────────────────────
  function buildSourcesSection(sources) {
    if (!sources || sources.length === 0) {
      return '';
    }

    const sourcesList = sources.map((src, idx) => {
      const link = src.url
        ? `<a href="${escAttr(src.url)}" target="_blank" rel="noopener noreferrer">${escHtml(src.judul)}</a>`
        : `<span>${escHtml(src.judul)}</span>`;
      const source = escHtml(src.sumber || 'Unknown');
      return `<li class="source-item">
        <div class="source-title">${link}</div>
        <div class="source-attribution">${source}</div>
      </li>`;
    }).join('');

    return `<div class="sources-section">
      <h3 class="sources-heading">Sumber & Referensi</h3>
      <ol class="sources-list">
        ${sourcesList}
      </ol>
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

  // ── Generate Article ─────────────────────────────────────────────────────
  async function generateArticle() {
    if (isSending || !activeSessionId) return;
    isSending = true;
    setGenerateEnabled(false);

    const container = document.getElementById('chatMessages');
    const emptyEl = container.querySelector('.chat-empty');
    if (emptyEl) emptyEl.remove();

    // Typing indicator
    const typingId = 'typing_' + Date.now();
    container.insertAdjacentHTML('beforeend', `
      <div class="msg-row bot typing" id="${typingId}">
        <div class="msg-avatar">AI</div>
        <div class="msg-bubble">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>`);
    container.scrollTop = container.scrollHeight;

    try {
      const res = await fetch('/api/chat/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ session_id: activeSessionId }),
      });

      document.getElementById(typingId)?.remove();

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      articleRawStore[activeSessionId] = data.article;
      
      // Fetch sources from database
      let sourcesHtml = '';
      try {
        const sourcesRes = await fetch(`/api/chat/sessions/${activeSessionId}/sources`, {
          credentials: 'include'
        });
        if (sourcesRes.ok) {
          const sourcesData = await sourcesRes.json();
          if (sourcesData.sources && sourcesData.sources.length > 0) {
            sourcesHtml = buildSourcesSection(sourcesData.sources);
          }
        }
      } catch (err) {
        logger_chat.warn(`Gagal fetch sources: ${err.message}`);
      }

      const html = `<div class="article-wrapper">
        ${buildArticleCard(data.article, activeSession?.topic)}
        ${sourcesHtml}
      </div>`;
      container.insertAdjacentHTML('beforeend', html);
      container.scrollTop = container.scrollHeight;

    } catch (err) {
      document.getElementById(typingId)?.remove();
      container.insertAdjacentHTML('beforeend', `
        <div class="msg-row bot">
          <div class="msg-avatar" style="background:#fee2e2;color:#dc2626;">!</div>
          <div><div class="msg-bubble" style="color:#dc2626;padding-top:2px;">
            ⚠️ Gagal generate artikel: ${escHtml(err.message)}
          </div></div>
        </div>`);
      container.scrollTop = container.scrollHeight;
      showToast(err.message, 'error');
    } finally {
      isSending = false;
      setGenerateEnabled(true);
    }
  }

  // ── Modal ─────────────────────────────────────────────────────────────────
  function openModal() {
    document.getElementById('newChatModal').classList.add('open');
    document.getElementById('topicInput').value = '';
    document.getElementById('crawlLoading').classList.remove('show');
    document.getElementById('modalActions').style.display = 'flex';
    document.getElementById('btnStart').disabled = false;
    setTimeout(() => document.getElementById('topicInput').focus(), 120);
  }

  function closeModal() {
    document.getElementById('newChatModal').classList.remove('open');
  }

  function handleModalKey(e) {
    if (e.key === 'Enter') { e.preventDefault(); startNewChat(); }
    if (e.key === 'Escape') closeModal();
  }

  async function startNewChat() {
    const topic = document.getElementById('topicInput').value.trim();
    if (!topic) { document.getElementById('topicInput').focus(); return; }

    document.getElementById('btnStart').disabled = true;
    document.getElementById('modalActions').style.display = 'none';
    document.getElementById('crawlLoading').classList.add('show');

    try {
      // 1. POST /sessions — langsung return, tidak tunggu lama
      const res = await fetch('/api/chat/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ topic }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
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
      headerSubtitle.textContent = `Topik: ${newSession.topic || 'N/A'}`;
      header.classList.remove('hidden');
      document.getElementById('topbarTitle').textContent = newSession.title || 'Artikel';
      const badge = document.getElementById('topbarBadge');
      if (newSession.topic) { badge.textContent = newSession.topic; badge.style.display = ''; }

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
            Mohon tunggu 1–3 menit. Halaman akan otomatis update.
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
    const MAX_TRIES   = 75;     // max ~5 menit
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
    const decoded = code.replace(/&amp;/g,'&').replace(/&quot;/g,'"');
    navigator.clipboard.writeText(decoded).then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    });
  }

  // ── UI Helpers ────────────────────────────────────────────────────────────
  function setGenerateEnabled(enabled) {
    const btn  = document.getElementById('btnGenerate');
    const hint = document.getElementById('generateHint');
    btn.disabled = !enabled;
    hint.textContent = enabled
      ? 'Klik untuk generate ulang artikel dari topik ini'
      : 'Sedang memproses...';
  }

  function setInputEnabled(enabled) {
    setGenerateEnabled(enabled);
  }

  function onInputChange(el) {
    // kept for compatibility but textarea is now hidden
    autoResize(el);
  }

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }

  function updateCharCounter(val) {
    // kept for compatibility
  }

  function toggleSidebar() {
    sidebarOpen = !sidebarOpen;
    document.getElementById('sidebar').classList.toggle('collapsed', !sidebarOpen);
  }

  function handleKey(e) {
    // keyboard shortcut maintained for potential future use
  }

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/login';
  }

  function escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
              .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
  }

  function escAttr(str) {
    if (!str) return '';
    return str.replace(/"/g,'&quot;').replace(/'/g,'&#039;');
  }

  // ── Toast ─────────────────────────────────────────────────────────────────
  function showToast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      ${type==='error' ? '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
      : type==='success' ? '<polyline points="20 6 9 17 4 12"/>'
      : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'}
    </svg><span>${escHtml(msg)}</span>`;
    document.getElementById('toastContainer').appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  // ── Close modal on backdrop click ─────────────────────────────────────────
  document.getElementById('newChatModal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
  });

  // ── Boot ──────────────────────────────────────────────────────────────────
  init();
