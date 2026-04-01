import gzip, mimetypes, sqlite3
from pathlib import Path, PurePath
from typing import Any
from PIL import Image
from fastapi import UploadFile

from uploads import UPLOAD_DIR

DB_PATH = UPLOAD_DIR / "media.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # access columns by name
    return conn


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS files (
            name TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            media_type TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT ''
        )""")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
              name TEXT PRIMARY KEY
        )"""
    )
    db.execute("""
        CREATE TABLE IF NOT EXISTS folder_items (
              folder_name TEXT NOT NULL,
              item_name TEXT NOT NULL,
              item_type TEXT NOT NULL CHECK(item_type IN ('file', 'folder')),
              PRIMARY KEY (folder_name, item_name),
              FOREIGN KEY (folder_name) REFERENCES folders(name)
        )""")
    db.commit()
    db.close()


def would_create_cycle(
    folder_name: str, item_name: str, db: sqlite3.Connection
) -> bool:
    """Check if adding item_name (a folder) to folder_name would create a cycle."""
    visited = set()
    stack = [item_name]
    while stack:
        current = stack.pop()
        if current == folder_name:
            return True
        if current in visited:
            continue
        visited.add(current)
        children = db.execute(
            "SELECT item_name FROM folder_items WHERE folder_name = ? AND item_type = 'folder'",
            (current,),
        ).fetchall()
        for child in children:
            stack.append(child["item_name"])
    return False


async def save_file(file: UploadFile, name: str, caption: str = ""):
    db = get_db()
    old = db.execute("SELECT filename FROM files WHERE name = ?", (name,)).fetchone()
    if old:
        old_path = UPLOAD_DIR / old["filename"]
        if old_path.exists():
            old_path.unlink()
    db.close()

    if isinstance(file.content_type, str) and file.content_type.startswith("image/"):
        img = Image.open(file.file)
        filename = f"{name}.webp"
        extension = "webp"
        media_type = "image/webp"
        img.save(UPLOAD_DIR / filename, "WEBP", lossless=True)
    else:
        filename = f"{name}.gz"
        extension = PurePath(file.filename or name).suffix
        media_type = (
            mimetypes.guess_type(file.filename or name)[0] or "application/octet-stream"
        )
        (UPLOAD_DIR / filename).write_bytes(gzip.compress(await file.read()))

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO files (name, filename, extension, media_type, caption) VALUES (?, ?, ?, ?, ?)",
        (name, filename, extension, media_type, caption),
    )
    db.commit()
    db.close()


def file_exists(name: str, provided_db: sqlite3.Connection | None = None) -> bool:
    if provided_db is None:
        db = get_db()
    else:
        db = provided_db
    file = db.execute("SELECT name FROM files WHERE name = ?", (name,)).fetchone()
    if provided_db is None:
        db.close()
    return bool(file)


def fetch_file(name: str) -> Any:
    db = get_db()
    row = db.execute(
        "SELECT filename, extension, media_type, caption FROM files WHERE name = ?",
        (name,),
    ).fetchone()
    db.close()
    return row


def fetch_all() -> tuple[list[dict], list[dict]]:
    db = get_db()
    files = db.execute("SELECT name, filename, media_type FROM files").fetchall()
    folders = []
    for row in db.execute("SELECT name FROM folders").fetchall():
        items = [
            dict(r)
            for r in db.execute(
                "SELECT item_name, item_type FROM folder_items WHERE folder_name = ?",
                (row["name"],),
            ).fetchall()
        ]
        folders.append({"name": row["name"], "items": items})

    db.close()
    return (files, folders)


def delete_file(name: str):
    db = get_db()
    row = db.execute("SELECT filename FROM files WHERE name = ?", (name,)).fetchone()
    if row:
        path = UPLOAD_DIR / row["filename"]
        if path.exists():
            path.unlink()
        db.execute("DELETE FROM files WHERE name = ?", (name,))
        db.commit()
    db.close()


def create_folder(name: str):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO folders (name) VALUES (?)", (name,))
    db.commit()
    db.close()


def folder_exists(name: str, provided_db: sqlite3.Connection | None = None) -> bool:
    if provided_db is None:
        db = get_db()
    else:
        db = provided_db
    folder = db.execute("SELECT name FROM folders WHERE name = ?", (name,)).fetchone()
    if provided_db is None:
        db.close()
    return bool(folder)


def add_to_folder(folder_name: str, item_name: str, item_type: str):
    assert item_type in ["file", "folder"]

    db = get_db()
    if not folder_exists(folder_name, db):
        db.execute("INSERT INTO folders (name) VALUES (?)", (folder_name,))

    if item_type == "file" and not file_exists(item_name, db):
        db.close()
        return False
    if item_type == "folder":
        if not folder_exists(item_name, db):
            db.close()
            return False
        if item_name == folder_name or would_create_cycle(folder_name, item_name, db):
            db.close()
            return False

    db.execute(
        "INSERT OR REPLACE INTO folder_items (folder_name, item_name, item_type) VALUES (?, ?, ?)",
        (folder_name, item_name, item_type),
    )
    db.commit()
    db.close()
    return True


def remove_from_folder(folder_name: str, item_name: str):
    db = get_db()
    db.execute(
        "DELETE FROM folder_items WHERE folder_name = ? AND item_name = ?",
        (folder_name, item_name),
    )
    db.commit()
    db.close()


def delete_folder(name: str):
    db = get_db()
    db.execute("DELETE FROM folder_items WHERE folder_name = ?", (name,))
    db.execute("DELETE FROM folders WHERE name = ?", (name,))
    db.commit()
    db.close()


def fetch_folder(name: str) -> list[sqlite3.Row] | None:
    db = get_db()
    folder = db.execute("SELECT name FROM folders WHERE name = ?", (name,)).fetchone()
    if not folder:
        return None
    items = db.execute(
        "SELECT item_name, item_type FROM folder_items WHERE folder_name = ?", (name,)
    ).fetchall()
    db.close()
    return items


init_db()
