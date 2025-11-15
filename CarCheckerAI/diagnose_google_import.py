import sys
import os
import traceback
import importlib.util

def show_file_snippet(fname, lineno, ctx=6):
    try:
        # try to read text as utf-8, fallback to latin-1 so we can show bytes
        with open(fname, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        with open(fname, "rb") as f:
            b = f.read()
        try:
            text = b.decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
        except Exception:
            lines = [repr(b)]
    start = max(0, lineno - ctx - 1)
    end = min(len(lines), lineno + ctx)
    print(f"\n--- File snippet: {fname} (lines {start+1}-{end}) ---")
    for i in range(start, end):
        prefix = ">>" if i == lineno - 1 else "  "
        # safe repr for non-printable bytes
        print(f"{prefix} {i+1:4d}: {lines[i].rstrip()!r}")
    print("--- end snippet ---\n")

print("Interpreter:", sys.executable)
print("Python:", sys.version.splitlines()[0])
print("CWD:", os.getcwd())
print("importlib.find_spec('google'):", importlib.util.find_spec("google"))

try:
    import google
    print("Imported google ->", getattr(google, "__file__", None), getattr(google, "__path__", None))
    try:
        import google.genai as gg
        print("Imported google.genai ->", getattr(gg, "__file__", None))
        print("SUCCESS: google.genai imported; you should be able to use genai.Client() now.")
    except Exception:
        print("Exception importing google.genai:")
        tb = traceback.format_exc()
        print(tb)
        # try extract last traceback frame in site-packages
        exc_type, exc_value, exc_tb = sys.exc_info()
        tb_list = traceback.extract_tb(exc_tb)
        if tb_list:
            # look for the last frame that is not this diagnostic file
            frame = tb_list[-1]
            fname, lineno, func, text = frame.filename, frame.lineno, frame.name, frame.line
            if os.path.exists(fname):
                show_file_snippet(fname, lineno)
except Exception:
    print("Exception importing google:")
    tb = traceback.format_exc()
    print(tb)
    exc_type, exc_value, exc_tb = sys.exc_info()
    tb_list = traceback.extract_tb(exc_tb)
    if tb_list:
        frame = tb_list[-1]
        fname, lineno = frame.filename, frame.lineno
        if os.path.exists(fname):
            show_file_snippet(fname, lineno)

print("If you still get 'unhashable type: list', paste the full traceback and the file snippet shown above.")