# AutoScout GitHub team storage

This folder contains the first server-side component for optional shared team storage.

## Important public-data behavior

The data repository is intentionally public. Anyone can read or copy its notes, sessions, photos, and account metadata. Team passwords are **not** stored in readable or encoded form. The account file contains a salted PBKDF2 verifier derived with a server-only pepper. The pepper and GitHub write token must exist only as Cloudflare Worker secrets.

This is shared-workspace access control, not confidentiality. Do not put private, personal, or sensitive information in team notes.

## Architecture

- Browser local mode continues to use the existing IndexedDB-backed Python filesystem.
- The Worker reads and writes JSON files in a public GitHub data repository.
- Team account credentials are stored at `teams/<team>/account.json`.
- Match notes: `teams/<team>/match-notes.json`.
- Team notes: `teams/<team>/team-notes.json`.
- Sessions: `teams/<team>/sessions/<session-id>.json`.
- Photo metadata and base64 image content: `teams/<team>/photos/<photo-id>.json`.
- Each file update uses the GitHub Contents API SHA and returns a conflict instead of silently overwriting a concurrent edit.

## Worker setup

1. Create a separate **public** repository for team data, `gmkovalik/AutoScout-Data` (already created). Do not use the application repository for scouting data.
2. Create a Cloudflare Worker from `index.js`.
3. Set Worker variables:
   - `DATA_REPO`: the data repository in `owner/repo` format.
   - `ALLOWED_ORIGIN`: `https://gmkovalik.github.io` (add a local development origin only when needed).
4. Add these as Worker secrets, never as source files:
   - `GITHUB_TOKEN`: fine-grained GitHub token with **Contents: Read and write** on the data repository only.
   - `AUTH_PEPPER`: a randomly generated secret of at least 32 bytes. Keep a secure offline backup. Changing it invalidates existing password verification and active sessions.
   - `REGISTRATION_KEY`: a separate one-time/team-account setup code. Share it only with trusted team administrators. It prevents strangers from claiming a public team number.
5. Keep the data repository public. Do not commit the Worker secrets or a token to GitHub.
6. Configure Worker rate limiting for `/api/login` and `/api/register`. The in-memory limiter in this Worker is only a best-effort fallback because serverless instances do not share memory.
7. Set the website configuration before loading the client:
   ```html
   <script>
     window.AUTOSCOUT_TEAM_STORAGE = { url: "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev" };
   </script>
   <script src="github-storage-client.js"></script>
   ```

## API

- `POST /api/register`: `{ team, password, registrationKey }`; team passwords must be at least 12 characters.
- `POST /api/login`: `{ team, password }`.
- `GET /api/team/:team/files`: list that team's data files. Add `?folder=sessions` or `?folder=photos` to list a sub-folder.
- `GET /api/team/:team/files/:path`: read an allowed JSON file.
- `PUT /api/team/:team/files/:path`: create/update an allowed JSON file. Send the last read ETag as `If-Match` to prevent stale writes.
- `DELETE /api/team/:team/files/:path`: delete an allowed JSON file, optionally with `If-Match`.

Allowed paths are `match-notes.json`, `team-notes.json`, `sessions/<id>.json`, and `photos/<id>.json`. Each file is limited to 700 KB to stay within the GitHub Contents API's practical limits. Store resized/compressed photo data in the photo JSON file.

## How the website uses it

- **Signing in:** the account button in the top bar (or **Settings > Team account**) opens the sign-in dialog. "New team? Create a team account" asks for the setup code. The sign-in is remembered on that device until the 12-hour token expires or **Sign out** is pressed. An expired or rejected token signs the device out automatically.
- **Notes:** notes are always saved in the browser's session first, so scouting works offline and without an account. When signed in, each new note is also added to `team-notes.json` or `match-notes.json`, and the Notes pages show the device's notes and the team's shared notes together, each marked **Shared**, **Team** (shared from another device) or **Not shared**. "Share N notes from this device" uploads only the notes the team does not have yet; photos are uploaded once per note. Deleting a shared note removes it for the whole team (after a confirmation).
- **Match notes** carry an `event` field (`season/code`) so notes from different events with the same match label are kept apart. Notes shared before this field existed show at every event.
- **Sessions:** "Save session to team" writes `sessions/<season>_<code>.json` and records it in `sessions/index.json`; "Open a team session" lists them from that index.
- Saves read the file, apply the change and write it back with the file's ETag; if another device saved in between, the change is reapplied to the newer file (up to three tries).

## Deploying this version of the Worker

The website works with the Worker that is already deployed. Redeploying `index.js` adds three improvements:

- the `ETag` header is exposed to the browser, so saves are checked against the version that was read (no silent overwrite when two devices save at the same moment);
- a save that loses that race returns HTTP 409, which the website retries automatically;
- `GET /api/team/:team/files?folder=sessions` lists the sessions folder, so sessions saved before `sessions/index.json` existed also appear in "Open a team session".
