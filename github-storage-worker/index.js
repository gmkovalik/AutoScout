/**
 * AutoScout GitHub team-storage gateway.
 *
 * Deploy as a Cloudflare Worker. The data repository stays public; this worker
 * is the only component that holds the GitHub write token and password pepper.
 */
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const MAX_FILE_BYTES = 700 * 1024;
const LOGIN_WINDOW_MS = 60_000;
const LOGIN_MAX_ATTEMPTS = 8;
const attempts = new Map();

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}
function cors(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = (env.ALLOWED_ORIGIN || "https://gmkovalik.github.io").split(",").map(x => x.trim());
  if (!origin || allowed.includes(origin)) return {
    "access-control-allow-origin": origin || allowed[0],
    "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
    "access-control-allow-headers": "Content-Type,Authorization",
    "access-control-max-age": "86400",
    "vary": "Origin",
  };
  return null;
}
function b64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function fromB64url(value) {
  const base = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base + "=".repeat((4 - base.length % 4) % 4));
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}
function equal(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}
async function passwordVerifier(password, salt, pepper) {
  const material = await crypto.subtle.importKey(
    "raw", encoder.encode(password + ":" + pepper), "PBKDF2", false, ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: fromB64url(salt), iterations: 310000 },
    material, 256
  );
  return b64url(new Uint8Array(bits));
}
async function hmac(message, secret) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}
async function issueToken(team, secret) {
  const payload = b64url(encoder.encode(JSON.stringify({ team, exp: Date.now() + 12 * 60 * 60 * 1000 })));
  const signature = b64url(await hmac("session:" + payload, secret));
  return payload + "." + signature;
}
async function verifyToken(request, secret, team) {
  const match = /^Bearer (.+)$/i.exec(request.headers.get("Authorization") || "");
  if (!match) return false;
  const [payload, signature] = match[1].split(".");
  if (!payload || !signature) return false;
  const expected = await hmac("session:" + payload, secret);
  if (!equal(expected, fromB64url(signature))) return false;
  let data;
  try { data = JSON.parse(decoder.decode(fromB64url(payload))); } catch { return false; }
  return data.team === team && Number(data.exp) > Date.now();
}
function validTeam(team) { return /^\d{1,8}$/.test(team); }
function validDataPath(path) {
  return path === "match-notes.json" || path === "team-notes.json" ||
    /^sessions\/[A-Za-z0-9_-]{1,80}\.json$/.test(path) ||
    /^photos\/[A-Za-z0-9_-]{1,80}\.json$/.test(path);
}
function githubHeaders(env) {
  return {
    "authorization": "Bearer " + env.GITHUB_TOKEN,
    "accept": "application/vnd.github+json",
    "content-type": "application/json",
    "x-github-api-version": "2022-11-28",
  };
}
function repoPath(env, path) {
  return "https://api.github.com/repos/" + env.DATA_REPO + "/contents/" + path.split("/").map(encodeURIComponent).join("/");
}
async function getGithubFile(env, path) {
  const response = await fetch(repoPath(env, path), { headers: githubHeaders(env) });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("GitHub read failed: " + response.status);
  const file = await response.json();
  const binary = atob((file.content || "").replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return { sha: file.sha, text: decoder.decode(bytes) };
}
async function putGithubFile(env, path, text, sha, message) {
  const bytes = encoder.encode(text);
  if (bytes.byteLength > MAX_FILE_BYTES) throw new Error("File is too large. The limit is 700 KB per file.");
  const content = btoa(Array.from(bytes, b => String.fromCharCode(b)).join(""));
  const response = await fetch(repoPath(env, path), {
    method: "PUT",
    headers: githubHeaders(env),
    body: JSON.stringify({ message, content, ...(sha ? { sha } : {}) }),
  });
  if (response.status === 409 || response.status === 422) {
    throw new Error("This file changed on another device. Reload it and try saving again.");
  }
  if (!response.ok) throw new Error("GitHub write failed: " + response.status);
  return response.json();
}
async function deleteGithubFile(env, path, sha) {
  const response = await fetch(repoPath(env, path), {
    method: "DELETE",
    headers: githubHeaders(env),
    body: JSON.stringify({ message: "Update AutoScout team data", sha }),
  });
  if (!response.ok) throw new Error("GitHub delete failed: " + response.status);
}
function rateLimitKey(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}
function limited(request) {
  const key = rateLimitKey(request);
  const now = Date.now();
  const old = attempts.get(key) || [];
  const recent = old.filter(t => now - t < LOGIN_WINDOW_MS);
  if (recent.length >= LOGIN_MAX_ATTEMPTS) return true;
  recent.push(now);
  attempts.set(key, recent);
  return false;
}
async function readJson(request) {
  const text = await request.text();
  if (encoder.encode(text).byteLength > MAX_FILE_BYTES) throw new Error("Request is too large.");
  return JSON.parse(text);
}

export default {
  async fetch(request, env) {
    const headers = cors(request, env);
    if (!headers) return json({ error: "This website is not allowed to access team storage." }, 403);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    try {
      if (!env.DATA_REPO || !env.GITHUB_TOKEN || !env.AUTH_PEPPER || !env.REGISTRATION_KEY) {
        return json({ error: "Team storage is not configured yet." }, 503, headers);
      }
      const url = new URL(request.url);
      const parts = url.pathname.split("/").filter(Boolean);
      if (url.pathname === "/health" && request.method === "GET") {
        return json({ ok: true, service: "AutoScout GitHub team storage" }, 200, headers);
      }
      if (url.pathname === "/api/register" && request.method === "POST") {
        if (limited(request)) return json({ error: "Too many attempts. Wait a minute and try again." }, 429, headers);
        const body = await readJson(request);
        const team = String(body.team || "").trim();
        const password = String(body.password || "");
        if (!validTeam(team)) return json({ error: "Enter a valid team number." }, 400, headers);
        if (password.length < 12) return json({ error: "Use a team password with at least 12 characters." }, 400, headers);
        if (body.registrationKey !== env.REGISTRATION_KEY) return json({ error: "The team setup code is incorrect." }, 403, headers);
        const path = "teams/" + team + "/account.json";
        if (await getGithubFile(env, path)) return json({ error: "That team account already exists. Sign in instead." }, 409, headers);
        const salt = b64url(crypto.getRandomValues(new Uint8Array(16)));
        const verifier = await passwordVerifier(password, salt, env.AUTH_PEPPER);
        await putGithubFile(env, path, JSON.stringify({
          schema: 1, team, password: { algorithm: "PBKDF2-SHA256", iterations: 310000, salt, verifier },
          createdAt: new Date().toISOString(),
        }, null, 2), null, "Create AutoScout team account " + team);
        return json({ ok: true, token: await issueToken(team, env.AUTH_PEPPER), team }, 201, headers);
      }
      if (url.pathname === "/api/login" && request.method === "POST") {
        if (limited(request)) return json({ error: "Too many attempts. Wait a minute and try again." }, 429, headers);
        const body = await readJson(request);
        const team = String(body.team || "").trim();
        if (!validTeam(team)) return json({ error: "Enter a valid team number." }, 400, headers);
        const account = await getGithubFile(env, "teams/" + team + "/account.json");
        if (!account) return json({ error: "Team account not found." }, 404, headers);
        const record = JSON.parse(account.text);
        const actual = await passwordVerifier(String(body.password || ""), record.password.salt, env.AUTH_PEPPER);
        if (!equal(encoder.encode(actual), encoder.encode(record.password.verifier))) {
          return json({ error: "Incorrect team number or password." }, 401, headers);
        }
        return json({ ok: true, token: await issueToken(team, env.AUTH_PEPPER), team }, 200, headers);
      }
      if (parts[0] === "api" && parts[1] === "team" && parts.length >= 3) {
        const team = parts[2];
        if (!validTeam(team)) return json({ error: "Invalid team number." }, 400, headers);
        if (!await verifyToken(request, env.AUTH_PEPPER, team)) {
          return json({ error: "Sign in to this team account first." }, 401, headers);
        }
        if (parts[3] === "files" && parts.length === 4 && request.method === "GET") {
          const response = await fetch(repoPath(env, "teams/" + team), { headers: githubHeaders(env) });
          if (response.status === 404) return json({ files: [] }, 200, headers);
          if (!response.ok) throw new Error("GitHub list failed: " + response.status);
          const entries = await response.json();
          return json({ files: entries.map(x => ({ name: x.name, path: x.path, type: x.type, size: x.size })) }, 200, headers);
        }
        if (parts[3] === "files" && parts.length >= 5) {
          const relative = parts.slice(4).join("/");
          if (!validDataPath(relative)) return json({ error: "That data file path is not allowed." }, 400, headers);
          const path = "teams/" + team + "/" + relative;
          if (request.method === "GET") {
            const file = await getGithubFile(env, path);
            return file ? new Response(file.text, { status: 200, headers: { ...headers, "content-type": "application/json; charset=utf-8", "etag": file.sha } }) :
              json({ error: "File not found." }, 404, headers);
          }
          if (request.method === "PUT") {
            const body = await readJson(request);
            const text = JSON.stringify(body);
            const current = await getGithubFile(env, path);
            const expected = request.headers.get("If-Match");
            if (expected && current && expected !== current.sha) return json({ error: "This file changed on another device. Reload it before saving." }, 409, headers);
            if (expected && !current) return json({ error: "This file was removed on another device. Reload before saving." }, 409, headers);
            const saved = await putGithubFile(env, path, text, current && current.sha, "Update AutoScout team " + team + " data");
            return json({ ok: true, sha: saved.content && saved.content.sha }, 200, headers);
          }
          if (request.method === "DELETE") {
            const current = await getGithubFile(env, path);
            if (!current) return json({ error: "File not found." }, 404, headers);
            const expected = request.headers.get("If-Match");
            if (expected && expected !== current.sha) return json({ error: "This file changed on another device. Reload before deleting." }, 409, headers);
            await deleteGithubFile(env, path, current.sha);
            return json({ ok: true }, 200, headers);
          }
        }
      }
      return json({ error: "Not found." }, 404, headers);
    } catch (error) {
      return json({ error: String(error && error.message || error).slice(0, 300) }, 400, headers);
    }
  },
};
