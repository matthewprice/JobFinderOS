"""Test package. Makes ``scripts/`` importable so tests can ``import jfgraph``.

Run with:  python3 -m unittest discover -s tests -t .
Every test is token-free: set CLAUDE_BIN=tests/fake_claude.py (the fixtures do this).
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
