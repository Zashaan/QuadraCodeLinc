"""Run a finite command and terminate its entire process group on timeout."""

import os
import signal
import subprocess
import sys

seconds = int(sys.argv[1])
process = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    sys.exit(process.wait(timeout=seconds))
except (subprocess.TimeoutExpired, KeyboardInterrupt):
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
    print("Command stopped at its time limit or on interruption.", file=sys.stderr)
    sys.exit(124)
