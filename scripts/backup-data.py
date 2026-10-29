#!/usr/bin/env python3
"""Consistent SQLite backup; never copy auth files or print database contents."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise FileNotFoundError('No existing Harvey database to back up')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as original:
            with sqlite3.connect(destination) as copy:
                original.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = root / 'data/harvey.db'
    if source.exists():
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        result = backup(source, root / 'data/backups' / f'harvey-{suffix}.db')
        print(f'Created and checked private backup: {result}')
    else:
        print('No existing data/harvey.db; nothing to back up.')
