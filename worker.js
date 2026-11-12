/* AutoScout web: runs the desktop AutoScout.py in Pyodide (Python compiled to
   WebAssembly) inside this Web Worker, so the page stays responsive while an
   event loads. The page sends {id, kind: "view"|"action", name, args} and gets
   back {id, result}; long actions also send {type: "progress"} messages. */

const PYODIDE_VERSION = "0.29.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const DATA_DIR = "/home/pyodide/.cache";      // AutoScout's cache dir lives under here
const SOURCES = ["AutoScout.py", "py/tkstub.py", "py/webbridge.py"];

let py = null;
let bridge = null;

function status(text) { postMessage({ type: "status", text }); }

function syncfs(populate) {
  return new Promise((resolve) => {
    try { py.FS.syncfs(populate, (err) => { if (err) console.warn("storage sync", err); resolve(); }); }
    catch (e) { console.warn("storage sync", e); resolve(); }
  });
}

const ready = (async () => {
  status("Downloading the AutoScout engine (about 7 MB the first time)…");
  importScripts(PYODIDE_URL + "pyodide.js");
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  status("Starting Python…");
  await py.loadPackage(["sqlite3"]);
  py.FS.mkdirTree("/app");
  const texts = await Promise.all(SOURCES.map(async (f) => {
    const r = await fetch(f, { cache: "no-cache" });
    if (!r.ok) throw new Error(`Could not download ${f} (HTTP ${r.status})`);
    return r.text();
  }));
  SOURCES.forEach((f, i) => py.FS.writeFile("/app/" + f.split("/").pop(), texts[i]));
  py.FS.mkdirTree(DATA_DIR);
  try {
    py.FS.mount(py.FS.filesystems.IDBFS, {}, DATA_DIR);
    await syncfs(true);                         // restore saved sessions + FTCScout cache
  } catch (e) {
    console.warn("No persistent storage; sessions last until the tab closes.", e);
  }
  status("Loading AutoScout…");
  py.runPython("import sys; sys.path.insert(0, '/app')");
  bridge = py.pyimport("webbridge");
  return JSON.parse(bridge.boot());
})();

// One request at a time: Python is single-threaded and storage syncs must not overlap.
let queue = Promise.resolve();
onmessage = (e) => { queue = queue.then(() => handle(e.data)); };

async function handle({ id, kind, name, args }) {
  try {
    const boot = await ready;
    let out;
    if (kind === "boot") {
      out = boot;
    } else if (kind === "view") {
      out = JSON.parse(bridge.view(name, JSON.stringify(args || {})));
    } else {
      const progress = (msg, frac) => postMessage({ type: "progress", id, msg, frac });
      out = JSON.parse(bridge.action(name, JSON.stringify(args || {}), progress));
      await syncfs(false);                              // persist sessions and cache
    }
    postMessage({ id, result: out });
  } catch (err) {
    const text = String(err && err.message || err);
    console.error(text);
    const last = text.trim().split("\n").filter(Boolean).pop() || text;
    postMessage({ id, error: last.slice(0, 400), detail: text.slice(-4000) });
  }
}
