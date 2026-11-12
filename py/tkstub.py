"""Stand-ins for tkinter so the desktop file imports in a browser (no GUI there)."""
import sys, types

class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return _Any()

class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        cls = type(name, (_Any,), {})
        setattr(self, name, cls)
        return cls

def install():
    subs = ["ttk", "filedialog", "messagebox", "font", "simpledialog", "scrolledtext", "colorchooser"]
    root = _StubModule("tkinter")
    sys.modules["tkinter"] = root
    for s in subs:
        m = _StubModule("tkinter." + s)
        sys.modules["tkinter." + s] = m
        setattr(root, s, m)
