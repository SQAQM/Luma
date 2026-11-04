"""Bounded write gate for the native model-task race fixture only (never user Data)."""
import sqlite3
import sys
import time
from pathlib import Path

database, ready, release = map(Path, sys.argv[1:])
# Caller creates a fresh, explicitly marked synthetic fixture root.
root = ready.parent.resolve()
if not (root / 'synthetic-model-test.marker').is_file() or not database.resolve().is_relative_to(root):
    raise ValueError('not an owned synthetic model fixture')
with sqlite3.connect(database, timeout=10) as connection:
    connection.execute('BEGIN IMMEDIATE')
    ready.write_text('synthetic write gate entered', encoding='utf-8')
    deadline = time.monotonic() + 24
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(.025)
    connection.rollback()
