#!/usr/bin/env python3
"""
Universal launcher for NI DAQ Controller Web Server.
Works from any directory - just run: python start_web_server.py
"""
import sys
import os
from pathlib import Path
import traceback

# Change to the script's directory (handles running from anywhere)
SCRIPT_DIR = Path(__file__).parent.absolute()
os.chdir(SCRIPT_DIR)
sys.path.insert(0, str(SCRIPT_DIR))

try:
    exec(open(SCRIPT_DIR / 'web_app.py').read())
except Exception:
    traceback.print_exc()
    print("\nFailed to start web server. See error above.")
    sys.exit(1)
