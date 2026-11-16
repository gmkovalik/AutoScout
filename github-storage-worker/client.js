/* Client for the AutoScout GitHub team-storage Worker.
   Local scouting remains available without this module being configured. */
(function () {
  "use strict";
  const config = window.AUTOSCOUT_TEAM_STORAGE || {};
  let token = null;
  let team = null;
  const base = String(config.url || "").replace(/\/$/, "");

  async function request(path, options = {}) {
    if (!base) throw new Error("Team cloud storage has not been configured.");
    const headers = { "content-type": "application/json", ...(options.headers || {}) };
    if (token) headers.authorization = "Bearer " + token;
    const response = await fetch(base + path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Team storage request failed.");
    return { data, etag: response.headers.get("etag") };
  }

  window.AutoScoutTeamStorage = {
    configured: () => !!base,
    currentTeam: () => team,
    async register(teamNumber, password, registrationKey) {
      const result = await request("/api/register", {
        method: "POST",
        body: JSON.stringify({ team: String(teamNumber), password, registrationKey }),
      });
      token = result.data.token; team = result.data.team;
      return { team };
    },
    async login(teamNumber, password) {
      const result = await request("/api/login", {
        method: "POST",
        body: JSON.stringify({ team: String(teamNumber), password }),
      });
      token = result.data.token; team = result.data.team;
      return { team };
    },
    signOut() { token = null; team = null; },
    async listFiles() {
      if (!team) throw new Error("Sign in to a team first.");
      return (await request("/api/team/" + encodeURIComponent(team) + "/files")).data.files;
    },
    async getFile(path) {
      if (!team) throw new Error("Sign in to a team first.");
      return request("/api/team/" + encodeURIComponent(team) + "/files/" + path.split("/").map(encodeURIComponent).join("/"));
    },
    async putFile(path, value, etag) {
      if (!team) throw new Error("Sign in to a team first.");
      return request("/api/team/" + encodeURIComponent(team) + "/files/" + path.split("/").map(encodeURIComponent).join("/"), {
        method: "PUT",
        headers: etag ? { "if-match": etag } : {},
        body: JSON.stringify(value),
      });
    },
    async deleteFile(path, etag) {
      if (!team) throw new Error("Sign in to a team first.");
      return request("/api/team/" + encodeURIComponent(team) + "/files/" + path.split("/").map(encodeURIComponent).join("/"), {
        method: "DELETE", headers: etag ? { "if-match": etag } : {},
      });
    },
  };
})();
