"""Named swatches and recent batch colours shared by the desktop app and MCP."""

import os
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path


RGB = tuple[int, int, int]


def hex_colour(colour: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*colour)


def parse_hex(value: str) -> RGB:
    digits = value.strip().removeprefix("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
        raise ValueError("Enter a six-digit hex colour such as #C54860.")
    return tuple(bytes.fromhex(digits))


def valid_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 64 or not name.isprintable():
        raise ValueError("Use a swatch name between 1 and 64 printable characters.")
    return name


@dataclass(frozen=True)
class Swatch:
    name: str
    hex: str


class SwatchStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            path = data / "Colour Shifter" / "swatches.sqlite3"
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS swatches (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    hex TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recent (
                    id INTEGER PRIMARY KEY,
                    hex TEXT UNIQUE NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            yield db

    def snapshot(self) -> tuple[list[Swatch], list[str]]:
        with self.connect() as db:
            db.execute("BEGIN")
            saved = [Swatch(*row) for row in db.execute("SELECT name, hex FROM swatches ORDER BY key")]
            recent = [row[0] for row in db.execute("SELECT hex FROM recent ORDER BY id DESC")]
        return saved, recent

    def get(self, name: str) -> Swatch:
        with self.connect() as db:
            row = db.execute(
                "SELECT name, hex FROM swatches WHERE key = ?", (valid_name(name).casefold(),)
            ).fetchone()
        if row is None:
            raise ValueError(f"No saved swatch named {name!r}.")
        return Swatch(*row)

    def add(self, name: str, colour: str) -> Swatch:
        swatch = Swatch(valid_name(name), hex_colour(parse_hex(colour)))
        try:
            with self.connect() as db:
                db.execute("INSERT INTO swatches VALUES (?, ?, ?)", (swatch.name.casefold(), swatch.name, swatch.hex))
        except sqlite3.IntegrityError as error:
            raise ValueError(f"A swatch named {swatch.name!r} already exists.") from error
        return swatch

    def rename(self, old_name: str, new_name: str) -> Swatch:
        name = valid_name(new_name)
        try:
            with self.connect() as db:
                row = db.execute(
                    "UPDATE swatches SET key = ?, name = ? WHERE key = ? RETURNING hex",
                    (name.casefold(), name, valid_name(old_name).casefold()),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise ValueError(f"A swatch named {name!r} already exists.") from error
        if row is None:
            raise ValueError(f"No saved swatch named {old_name!r}.")
        return Swatch(name, row[0])

    def delete(self, name: str) -> Swatch:
        with self.connect() as db:
            row = db.execute(
                "DELETE FROM swatches WHERE key = ? RETURNING name, hex", (valid_name(name).casefold(),)
            ).fetchone()
        if row is None:
            raise ValueError(f"No saved swatch named {name!r}.")
        return Swatch(*row)

    def record_batch(self, colour: RGB) -> None:
        value = hex_colour(colour)
        with self.connect() as db:
            db.execute("DELETE FROM recent WHERE hex = ?", (value,))
            db.execute("INSERT INTO recent (hex) VALUES (?)", (value,))
            db.execute("DELETE FROM recent WHERE id NOT IN (SELECT id FROM recent ORDER BY id DESC LIMIT 20)")


def remember_batch_colour(colour: RGB) -> str | None:
    try:
        SwatchStore().record_batch(colour)
    except (OSError, sqlite3.Error):
        return "Exports were saved, but the colour could not be added to recent batches."
    return None
