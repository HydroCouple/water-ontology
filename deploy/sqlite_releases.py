"""Register immutable SQLite releases and atomically select the active release per system."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from filelock import FileLock


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def register(directory, snapshot, actor):
    directory, snapshot = Path(directory).resolve(), Path(snapshot)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as td:
        copied = Path(td) / "release.sqlite"
        shutil.copyfile(snapshot, copied)
        with closing(
            sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)
        ) as release:
            summary = json.loads(
                release.execute(
                    "SELECT value FROM reference__release WHERE name='input_summary'"
                ).fetchone()[0]
            )
            if (
                release.execute("PRAGMA integrity_check").fetchone() != ("ok",)
                or release.execute(
                    "SELECT 1 FROM reference__violations WHERE severity='ERROR' LIMIT 1"
                ).fetchone()
            ):
                raise ValueError(
                    "Snapshot did not pass integrity and semantic validation"
                )
        identity = digest(copied)
        with FileLock(str(directory / ".publish.lock")):
            target = directory / (identity + ".sqlite")
            if target.exists() and digest(target) != identity:
                raise ValueError(
                    "Stored release bytes differ from their content address"
                )
            if not target.exists():
                os.link(copied, target)
            with closing(sqlite3.connect(directory / "releases.sqlite")) as db, db:
                db.executescript("""
CREATE TABLE IF NOT EXISTS releases(digest TEXT PRIMARY KEY,system TEXT NOT NULL,revision TEXT NOT NULL,actor TEXT NOT NULL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY,system TEXT NOT NULL,digest TEXT NOT NULL REFERENCES releases,
    actor TEXT NOT NULL,recorded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE VIEW IF NOT EXISTS active AS SELECT c.* FROM changes c
    WHERE c.id=(SELECT max(h.id) FROM changes h WHERE h.system=c.system);
""")
                db.execute("PRAGMA foreign_keys=ON")
                seen = db.execute(
                    "SELECT 1 FROM releases WHERE digest=?", (identity,)
                ).fetchone()
                if not seen:
                    db.execute(
                        "INSERT INTO releases(digest,system,revision,actor) VALUES(?,?,?,?)",
                        (identity, summary["system"], summary["revision"], actor),
                    )
                    db.execute(
                        "INSERT INTO changes(system,digest,actor) VALUES(?,?,?)",
                        (summary["system"], identity, actor),
                    )
        return identity


def activate(directory, identity, actor):
    directory = Path(directory)
    with (
        FileLock(str(directory / ".publish.lock")),
        closing(
            sqlite3.connect(
                (directory / "releases.sqlite").resolve().as_uri() + "?mode=rw",
                uri=True,
            )
        ) as db,
        db,
    ):
        db.execute("PRAGMA foreign_keys=ON")
        row = db.execute(
            "SELECT system FROM releases WHERE digest=?", (identity,)
        ).fetchone()
        if row is None or digest(directory / (identity + ".sqlite")) != identity:
            raise ValueError("Unknown or altered release")
        active = db.execute("SELECT digest FROM active WHERE system=?", row).fetchone()
        if active != (identity,):
            db.execute(
                "INSERT INTO changes(system,digest,actor) VALUES(?,?,?)",
                (row[0], identity, actor),
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=["register", "activate"])
    p.add_argument("directory", type=Path)
    p.add_argument("release", help="Snapshot path to register or digest to activate")
    p.add_argument("--actor", required=True)
    a = p.parse_args()
    if a.operation == "register":
        print(register(a.directory.resolve(), a.release, a.actor))
    else:
        activate(a.directory, a.release, a.actor)
