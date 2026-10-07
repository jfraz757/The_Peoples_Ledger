"""
Run a PowerShell script with no window at all. Used by the scheduled "Pending Alert" task.

    pythonw.exe run_hidden.pyw notify_pending.ps1 [script args...]

Why this exists: the task used to launch `conhost.exe --headless powershell.exe ...`, which is
supposed to hide the console, but on Windows 11 a window still flashed up every 10 minutes.
pythonw.exe is a GUI-subsystem program, so Windows never creates a console for it, and
CREATE_NO_WINDOW starts PowerShell without one either. Toast notifications don't need a
console, so they still appear.
"""

import subprocess
import sys
from pathlib import Path

script = Path(sys.argv[1]).resolve()
result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
     "-File", str(script), *sys.argv[2:]],
    cwd=script.parent,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
sys.exit(result.returncode)
