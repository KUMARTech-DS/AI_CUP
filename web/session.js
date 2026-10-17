/** Shared Command Center session helpers (role-based ACL). */
(function (global) {
  const KEY = 'suffai_session';

  function apiBase() {
    return (global.DASHBOARD_API || '').replace(/\/$/, '');
  }

  function getSession() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function setSession(sess) {
    localStorage.setItem(KEY, JSON.stringify(sess || {}));
    // Back-compat flags used by older checks
    localStorage.setItem('suffai_demo_session', '1');
    localStorage.setItem('suffai_demo_user', (sess && sess.username) || '');
  }

  function clearSession() {
    localStorage.removeItem(KEY);
    localStorage.removeItem('suffai_demo_session');
    localStorage.removeItem('suffai_demo_user');
  }

  function authHeaders() {
    const s = getSession();
    if (!s || !s.token) return {};
    return {
      Authorization: 'Bearer ' + s.token,
      'X-Session-Token': s.token,
    };
  }

  function allowedTabs() {
    const s = getSession();
    return (s && Array.isArray(s.allowed_tabs) && s.allowed_tabs.length)
      ? s.allowed_tabs
      : [];
  }

  function canAccessTab(tab) {
    return allowedTabs().indexOf(tab) >= 0;
  }

  /** Redirect to login if no session. Optionally require a tab id. */
  function requireAuth(tab) {
    const s = getSession();
    if (!s || !s.token) {
      location.replace('./login.html');
      return null;
    }
    if (tab && !canAccessTab(tab)) {
      // Send to shell; shell will open first allowed tab
      location.replace('./index.html');
      return null;
    }
    return s;
  }

  async function fetchMe() {
    const s = getSession();
    if (!s || !s.token) return null;
    const res = await fetch(apiBase() + '/v1/auth/me', {
      headers: { Accept: 'application/json', ...authHeaders() },
    });
    if (!res.ok) {
      clearSession();
      return null;
    }
    const data = await res.json();
    const next = {
      token: data.token || s.token,
      role: data.role,
      username: data.username,
      brand_name: data.brand_name || null,
      customer_id: data.customer_id || null,
      display_name: data.display_name || data.username,
      allowed_tabs: data.allowed_tabs || [],
    };
    setSession(next);
    return next;
  }

  async function logout() {
    try {
      await fetch(apiBase() + '/v1/auth/logout', {
        method: 'POST',
        headers: { ...authHeaders() },
      });
    } catch (_) {}
    clearSession();
    location.href = './login.html';
  }

  global.SuffaiSession = {
    KEY,
    getSession,
    setSession,
    clearSession,
    authHeaders,
    allowedTabs,
    canAccessTab,
    requireAuth,
    fetchMe,
    logout,
    apiBase,
  };
})(window);
