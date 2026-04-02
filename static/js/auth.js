// ─── Helper UI ────────────────────────────────────────────────────────────

function getOrCreateAlert(buttonId) {
  let el = document.getElementById('alert-msg');
  if (!el) {
    el = document.createElement('div');
    el.id = 'alert-msg';
    el.style.cssText = `
      padding: 10px 14px;
      border-radius: 6px;
      margin-bottom: 12px;
      font-size: 14px;
      display: none;
    `;
    const btn = document.getElementById(buttonId);
    if (btn) {
      btn.parentNode.insertBefore(el, btn);
    }
  }
  return el;
}

function showError(msg, buttonId) {
  const el = getOrCreateAlert(buttonId);
  if (!el) return;
  el.style.background = '#fdecea';
  el.style.color      = '#c0392b';
  el.style.border     = '1px solid #e74c3c';
  el.textContent      = msg;
  el.style.display    = 'block';
}

function showSuccess(msg, buttonId) {
  const el = getOrCreateAlert(buttonId);
  if (!el) return;
  el.style.background = '#eafaf1';
  el.style.color      = '#1e8449';
  el.style.border     = '1px solid #27ae60';
  el.textContent      = msg;
  el.style.display    = 'block';
}


// ─── Password Strength ────────────────────────────────────────────────────

window.checkStrength = function(val) {
  const bars  = ['s1','s2','s3','s4'].map(id => document.getElementById(id));
  const label = document.getElementById('strength-label');
  if (!label || bars.some(b => !b)) return;
  
  const colors = ['#e74c3c','#e67e22','#f1c40f','#27ae60'];
  const labels = ['Weak','Fair','Good','Strong'];
  let score = 0;
  
  if (val.length >= 8)           score++;
  if (/[A-Z]/.test(val))         score++;
  if (/[0-9]/.test(val))         score++;
  if (/[^A-Za-z0-9]/.test(val))  score++;
  
  bars.forEach((b, i) => b.style.background = i < score ? colors[score-1] : '#e0e0da');
  label.textContent = val.length ? labels[score-1] || '' : '';
  label.style.color = score > 0  ? colors[score-1] : '#aab5ae';
};


// ─── Login Logic ─────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const btnLogin = document.getElementById('btn-login');
  if (btnLogin) {
    btnLogin.addEventListener('click', async () => {
      const email    = document.getElementById('email').value.trim();
      const password = document.getElementById('password').value;

      // Validasi frontend
      if (!email || !password) {
        return showError('Email dan password wajib diisi', 'btn-login');
      }

      try {
        const res = await fetch('/api/auth/login', {
          method      : 'POST',
          headers     : { 'Content-Type': 'application/json' },
          credentials : 'include',  // ← penting! agar cookie JWT tersimpan
          body        : JSON.stringify({ email, password })
        });

        const data = await res.json();

        if (res.ok) {
          showSuccess(`Selamat datang! Mengarahkan...`, 'btn-login');
          setTimeout(() => window.location.href = '/chat', 1500);
        } else {
          showError(data.detail || 'Login gagal', 'btn-login');
        }

      } catch (err) {
        showError('Tidak dapat terhubung ke server', 'btn-login');
      }
    });
  }

  // ─── Register Logic ──────────────────────────────────────────────────────
  const btnRegister = document.getElementById('btn-register');
  if (btnRegister) {
    btnRegister.addEventListener('click', async () => {
      const firstname = document.getElementById('firstname').value.trim();
      const lastname  = document.getElementById('lastname').value.trim();
      const email     = document.getElementById('email').value.trim();
      const password  = document.getElementById('password').value;
      const confirm   = document.getElementById('confirm').value;

      // Validasi frontend
      if (!firstname || !email || !password) {
        return showError('Nama, email, dan password wajib diisi', 'btn-register');
      }
      if (password !== confirm) {
        return showError('Password tidak cocok', 'btn-register');
      }
      if (password.length < 8) {
        return showError('Password minimal 8 karakter', 'btn-register');
      }

      const username = `${firstname} ${lastname}`.trim();

      try {
        const res = await fetch('/api/auth/register', {
          method  : 'POST',
          headers : { 'Content-Type': 'application/json' },
          body    : JSON.stringify({ username, email, password })
        });

        const data = await res.json();

        if (res.ok) {
          // Berhasil → redirect ke login
          showSuccess('Akun berhasil dibuat! Mengarahkan ke halaman login...', 'btn-register');
          setTimeout(() => window.location.href = '/login', 1500);
        } else {
          // Gagal → tampilkan pesan error dari API
          showError(data.detail || 'Registrasi gagal', 'btn-register');
        }

      } catch (err) {
        showError('Tidak dapat terhubung ke server', 'btn-register');
      }
    });
  }

  // ─── Navbar Auth Check ───────────────────────────────────────────────────
  const navRight = document.getElementById('nav-right');
  if (navRight) {
    (async () => {
      try {
        const res = await fetch('/api/auth/me', { credentials: 'include' });
        if (res.ok) {
          const user = await res.json();
          navRight.innerHTML = `
            <span class="nav-username">Hi, ${user.username}</span>
            <button class="btn-logout" id="btn-logout">Logout</button>
          `;
          document.getElementById('btn-logout').addEventListener('click', async () => {
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
            window.location.href = '/login';
          });
        } else {
          navRight.innerHTML = `
            <a href="/login" class="btn-login">Login</a>
            <a href="/signup" class="btn-signup">Sign Up</a>
          `;
        }
      } catch {
        navRight.innerHTML = `
          <a href="/login" class="btn-login">Login</a>
          <a href="/signup" class="btn-signup">Sign Up</a>
        `;
      }
    })();
  }
});
