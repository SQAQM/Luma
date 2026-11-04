"""A bounded SQL write lock in an explicitly named synthetic diagnostic directory."""
import sqlite3,sys,time
from pathlib import Path

path=Path(sys.argv[1]).resolve()
if path.name!='file-reviews.sqlite3' or 'isolated-data' not in path.parts:
    raise SystemExit('synthetic diagnostic control database required')
db=sqlite3.connect(path)
try:
    db.execute('BEGIN IMMEDIATE');Path(sys.argv[2]).write_text('ready')
    for _ in range(400):
        if Path(sys.argv[3]).exists():break
        time.sleep(.01)
finally:
    db.rollback();db.close()
