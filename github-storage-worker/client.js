/* Client for the AutoScout GitHub team-storage Worker.
   Local scouting remains available without this module being configured.

   The sign-in is remembered on this device (localStorage) until the Worker's
   token expires (12 hours) or "Sign out" is pressed, so a reload or reopening
   the home-screen app does not ask for the password again. */
(function () {
  "use strict";
  const config = window.AUTOSCOUT_TEAM_STORAGE || {};
  const base = String(config.url || "").replace(/\/$/, "");
  const KEY = "autoscout.teamStorage.v1";
  const listeners = new Set();
  let token = null;
  let team = null;
  let expires = 0;

  function tokenExpiry(value) {
    try {
      const payload = String(value).split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
      const data = JSON.parse(atob(payload + "=".repeat((4 - payload.length % 4) % 4)));
      return Number(data.exp) || 0;
    } catch (_) { return 0; }
  }
  function persist() {
    try {
      if (token) localStorage.setItem(KEY, JSON.stringify({ token, team, expires }));
      else localStorage.removeItem(KEY);
    } catch (_) { /* private browsing: stays signed in for this tab only */ }
  }
  function notify() { for (const fn of listeners) { try { fn(); } catch (e) { console.error(e); } } }
  function setSession(newToken, newTeam) {
    token = newToken || null;
    team = newToken ? String(newTeam) : null;
    expires = newToken ? (tokenExpiry(newToken) || Date.now() + 12 * 3600 * 1000) : 0;
    persist();
    notify();
  }
  function expired() { return !!token && expires && Date.now() > expires - 60 * 1000; }
  (function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "null");
      if (saved && saved.token && saved.team && Number(saved.expires) > Date.now() + 60 * 1000) {
        token = saved.token; team = String(saved.team); expires = Number(saved.expires);
      } else if (saved) {
        localStorage.removeItem(KEY);
      }
    } catch (_) { /* ignore */ }
  })();

  class StorageError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function request(path, options = {}) {
    if (!base) throw new StorageError("Team cloud storage has not been configured.", 0);
    if (expired()) { setSession(null); throw new StorageError("Your team sign-in has expired. Sign in again.", 401); }
    const headers = { "content-type": "application/json", ...(options.headers || {}) };
    if (token) headers.authorization = "Bearer " + token;
    let response;
    try {
      response = await fetch(base + path, { ...options, headers });
    } catch (_) {
      throw new StorageError("Could not reach team storage. Check the connection.", 0);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && token && !/\/api\/(login|register)$/.test(path)) setSession(null);
      throw new StorageError(data.error || "Team storage request failed.", response.status);
    }
    return { data, etag: response.headers.get("etag") };
  }
  function filePath(path) {
    if (!team) throw new StorageError("Sign in to a team first.", 401);
    return "/api/team/" + encodeURIComponent(team) + "/files/" + path.split("/").map(encodeURIComponent).join("/");
  }

  const api = {
    configured: () => !!base,
    signedIn: () => !!(token && team) && !expired(),
    currentTeam: () => team,
    expiresAt: () => expires,
    /** Calls fn() whenever the user signs in or out (including an expired token). Returns an unsubscribe function. */
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    async register(teamNumber, password, registrationKey) {
      const result = await request("/api/register", {
        method: "POST",
        body: JSON.stringify({ team: String(teamNumber).trim(), password, registrationKey }),
      });
      setSession(result.data.token, result.data.team);
      return { team };
    },
    async login(teamNumber, password) {
      const result = await request("/api/login", {
        method: "POST",
        body: JSON.stringify({ team: String(teamNumber).trim(), password }),
      });
      setSession(result.data.token, result.data.team);
      return { team };
    },
    signOut() { setSession(null); },
    /** Files in the team's folder, or in one of its sub-folders ("sessions" or "photos"). */
    async listFiles(folder) {
      if (!team) throw new StorageError("Sign in to a team first.", 401);
      const query = folder ? "?folder=" + encodeURIComponent(folder) : "";
      return (await request("/api/team/" + encodeURIComponent(team) + "/files" + query)).data.files;
    },
    getFile(path) { return request(filePath(path)); },
    /** Like getFile, but a missing file gives {data: fallback, etag: null} instead of an error. */
    async getFileOr(path, fallback) {
      try { return await request(filePath(path)); }
      catch (e) { if (e.status === 404) return { data: fallback, etag: null }; throw e; }
    },
    putFile(path, value, etag) {
      return request(filePath(path), {
        method: "PUT",
        headers: etag ? { "if-match": etag } : {},
        body: JSON.stringify(value),
      });
    },
    deleteFile(path, etag) {
      return request(filePath(path), { method: "DELETE", headers: etag ? { "if-match": etag } : {} });
    },
    /** Read a JSON file, change it with edit(data) and save it. If another device saved
        the file in between (HTTP 409), reread and apply the edit again. Returns the saved data. */
    async updateFile(path, fallback, edit) {
      for (let attempt = 0; ; attempt++) {
        const file = await api.getFileOr(path, JSON.parse(JSON.stringify(fallback)));
        const data = file.data && typeof file.data === "object" ? file.data : JSON.parse(JSON.stringify(fallback));
        if (edit(data) === false) return data;            // nothing to change
        try {
          await api.putFile(path, data, file.etag);
          return data;
        } catch (e) {
          const conflict = e.status === 409 || /changed on another device|removed on another device/i.test(e.message);
          if (!conflict || attempt >= 2) throw e;
        }
      }
    },
  };
  window.AutoScoutTeamStorage = api;
})();
