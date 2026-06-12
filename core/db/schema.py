"""SQLite schema migrations for Picurate catalog."""
import sqlite3
from pathlib import Path

# Each entry is (version, sql).  Apply in order; never remove/modify existing entries.
MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    );

    CREATE TABLE IF NOT EXISTS volumes (
        id    INTEGER PRIMARY KEY,
        label TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS photos (
        id              INTEGER PRIMARY KEY,
        file_path       TEXT NOT NULL,
        volume_id       INTEGER REFERENCES volumes(id),
        filename        TEXT NOT NULL,
        file_size       INTEGER,
        mtime           REAL,
        quick_sig       TEXT,
        partial_hash    TEXT,
        full_hash       TEXT,
        date_taken      TEXT,
        camera_make     TEXT,
        camera_model    TEXT,
        width           INTEGER,
        height          INTEGER,
        gps_lat         REAL,
        gps_lon         REAL,
        place_id        INTEGER,
        status          TEXT NOT NULL DEFAULT 'ok',
        thumbnail_path  TEXT,
        rating          INTEGER DEFAULT 0,
        flag            INTEGER DEFAULT 0,
        quality_score   REAL,
        phash           TEXT,
        trip_id         INTEGER
    );
    CREATE UNIQUE INDEX IF NOT EXISTS photos_path ON photos(file_path);
    CREATE INDEX IF NOT EXISTS photos_full_hash ON photos(full_hash);
    CREATE INDEX IF NOT EXISTS photos_status ON photos(status);

    CREATE TABLE IF NOT EXISTS people (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS faces (
        id           INTEGER PRIMARY KEY,
        photo_id     INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        bounding_box TEXT,
        embedding    BLOB,
        person_id    INTEGER REFERENCES people(id),
        confidence   REAL,
        source       TEXT
    );

    CREATE TABLE IF NOT EXISTS tags (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS tags_name ON tags(name);

    CREATE TABLE IF NOT EXISTS photo_tags (
        photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        confidence REAL,
        source     TEXT,
        PRIMARY KEY (photo_id, tag_id)
    );

    CREATE TABLE IF NOT EXISTS places (
        id      INTEGER PRIMARY KEY,
        city    TEXT,
        region  TEXT,
        country TEXT,
        lat     REAL,
        lon     REAL
    );

    CREATE TABLE IF NOT EXISTS trips (
        id               INTEGER PRIMARY KEY,
        name             TEXT NOT NULL,
        start_date       TEXT,
        end_date         TEXT,
        primary_place_id INTEGER REFERENCES places(id)
    );

    CREATE TABLE IF NOT EXISTS collections (
        id     INTEGER PRIMARY KEY,
        name   TEXT NOT NULL,
        type   TEXT NOT NULL DEFAULT 'manual',
        rules  TEXT,
        source TEXT
    );

    CREATE TABLE IF NOT EXISTS collection_photos (
        collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        PRIMARY KEY (collection_id, photo_id)
    );

    CREATE TABLE IF NOT EXISTS import_batches (
        id          INTEGER PRIMARY KEY,
        source_type TEXT,
        source_path TEXT,
        run_at      TEXT
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id         INTEGER PRIMARY KEY,
        job_type   TEXT NOT NULL,
        payload    TEXT,
        status     TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT
    );
    CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """),
    (2, """
    ALTER TABLE photos ADD COLUMN caption TEXT;
    ALTER TABLE photos ADD COLUMN keywords TEXT;

    ALTER TABLE import_batches ADD COLUMN record_count INTEGER DEFAULT 0;
    ALTER TABLE import_batches ADD COLUMN undo_data TEXT;
    """),
    (3, """
    ALTER TABLE photos ADD COLUMN clip_embedding TEXT;
    CREATE INDEX IF NOT EXISTS photos_clip ON photos(id) WHERE clip_embedding IS NOT NULL;
    """),
    (4, """
    ALTER TABLE photos ADD COLUMN sharpness_score REAL;
    ALTER TABLE photos ADD COLUMN exposure_score REAL;

    CREATE TABLE IF NOT EXISTS similarity_groups (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        scope      TEXT NOT NULL,
        threshold  REAL NOT NULL DEFAULT 0.65,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS photo_similarity_group (
        photo_id           INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        group_id           INTEGER NOT NULL REFERENCES similarity_groups(id) ON DELETE CASCADE,
        similarity_to_best REAL,
        is_suggested_best  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (photo_id, group_id)
    );
    CREATE INDEX IF NOT EXISTS psg_group ON photo_similarity_group(group_id);
    """),
]


def get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> None:
    current = get_version(conn)
    for version, sql in MIGRATIONS:
        if version > current:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(version) VALUES (?)", (version,)
            )
            conn.commit()
