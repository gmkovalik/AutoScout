/* AutoScout web: runs the desktop AutoScout.py in Pyodide (Python compiled to
   WebAssembly) inside this Web Worker, so the page stays responsive while an
   event loads. The page sends {id, kind: "view"|"action", name, args} and gets
   back {id, result}; long actions also send {type: "progress"} messages. */

const PYODIDE_VERSION = "0.29.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const DATA_DIR = "/home/pyodide/.cache";      // AutoScout's cache dir lives under here
const PYC_DIR = DATA_DIR + "/autoscout-pyc";  // compiled AutoScout.py, reused between visits
const SOURCES = ["AutoScout.py", "py/tkstub.py", "py/xpr.py", "py/webbridge.py"];

let py = null;
let bridge = null;

function status(text) { postMessage({ type: "status", text }); }

function syncfs(populate) {
  return new Promise((resolve) => {
    try { py.FS.syncfs(populate, (err) => { if (err) console.warn("storage sync", err); resolve(); }); }
    catch (e) { console.warn("storage sync", e); resolve(); }
  });
}

// Compiling the 600 KB AutoScout.py takes most of the start-up time after the
// engine download. Keep the compiled bytecode in the browser's storage and
// reuse it while the source is unchanged. The .pyc files are "checked hash"
// files: Python re-hashes the source on import and recompiles if it differs,
// so a new AutoScout.py can never run with stale bytecode.
const PYC_PREPARE = `
import sys, importlib.util, py_compile
def _autoscout_prepare_pyc(prefix, files):
    sys.pycache_prefix = prefix
    changed = False
    for src in files:
        try:
            cfile = importlib.util.cache_from_source(src)
            with open(src, "rb") as f:
                want = importlib.util.MAGIC_NUMBER + (3).to_bytes(4, "little") + importlib.util.source_hash(f.read())
            try:
                with open(cfile, "rb") as f:
                    have = f.read(16)
            except OSError:
                have = b""
            if have != want:
                py_compile.compile(src, cfile=cfile, doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
                changed = True
        except Exception as e:          # never block start-up on the cache
            print("bytecode cache skipped for", src, e)
    return changed
`;

const ready = (async () => {
  status("Downloading the AutoScout engine (about 7 MB the first time)…");
  // Start downloading AutoScout's own files while the Python runtime downloads.
  const sources = Promise.all(SOURCES.map(async (f) => {
    const r = await fetch(f, { cache: "no-cache" });
    if (!r.ok) throw new Error(`Could not download ${f} (HTTP ${r.status})`);
    return r.text();
  }));
  sources.catch(() => {});                      // reported below, after the runtime loads
  importScripts(PYODIDE_URL + "pyodide.js");
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  status("Starting Python…");
  const sqlite = py.loadPackage(["sqlite3"]);
  py.FS.mkdirTree(DATA_DIR);
  let persistent = false;
  try {
    py.FS.mount(py.FS.filesystems.IDBFS, {}, DATA_DIR);
    persistent = true;
  } catch (e) {
    console.warn("No persistent storage; sessions last until the tab closes.", e);
  }
  // Restore saved sessions + FTCScout cache while sqlite3 and the sources finish.
  const [texts] = await Promise.all([sources, sqlite, persistent ? syncfs(true) : null]);
  py.FS.mkdirTree("/app");
  SOURCES.forEach((f, i) => py.FS.writeFile("/app/" + f.split("/").pop(), texts[i]));
  status("Loading AutoScout…");
  py.runPython("import sys; sys.path.insert(0, '/app')");
  let compiled = false;
  if (persistent) {
    try {
      py.runPython(PYC_PREPARE);
      compiled = py.globals.get("_autoscout_prepare_pyc")(PYC_DIR, SOURCES.map((f) => "/app/" + f.split("/").pop()));
    } catch (e) {
      console.warn("bytecode cache", e);
    }
  }
  bridge = py.pyimport("webbridge");
  const boot = JSON.parse(bridge.boot());
  if (compiled) await syncfs(false);             // keep the new bytecode for next time
  return boot;
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
