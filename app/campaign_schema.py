"""Apply campaign schema migrations with backups for existing databases."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.memory_analyzer import ensure_memory_table
from app.continuity import create_recall_indexes, normalized_key
from app.relationship_analyzer import ALLOWED_FIELDS, CATEGORICAL_FIELDS, NUMERIC_FIELDS


def backup_database(database, migration="player-identity"):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = database.parent.parent / "backups"
    directory.mkdir(exist_ok=True)
    path = directory / f"{database.stem}-before-{migration}-{stamp}.db"
    path.touch(exist_ok=False)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(path)) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Database backup failed integrity check: {path}")
    return path


def migrate_campaign_rules(database, *, backup=True):
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(campaigns)")}
        if "rules" in columns:
            return None
        snapshot = backup_database(database, "campaign-rules") if backup else None
        connection.execute("ALTER TABLE campaigns ADD COLUMN rules TEXT NOT NULL DEFAULT ''")
        return snapshot


def sql_values(values):
    return ", ".join(f"'{value}'" for value in sorted(values))


def migrate_relationships(connection):
    fields = sql_values(ALLOWED_FIELDS)
    metric_columns = [
        f"{field} INTEGER NOT NULL DEFAULT 0 CHECK ({field} BETWEEN 0 AND 100)"
        for field in sorted(NUMERIC_FIELDS)
    ] + [
        f"{field} TEXT NOT NULL DEFAULT 'none' CHECK ({field} IN ({sql_values(values)}))"
        for field, values in CATEGORICAL_FIELDS.items()
    ]
    table_columns = {
        "character_relationships": ", ".join(
            [*metric_columns, "updated_at TEXT DEFAULT CURRENT_TIMESTAMP"]
        ),
        "relationship_history": f"""
            field_name TEXT NOT NULL CHECK (field_name IN ({fields})),
            old_value TEXT DEFAULT '',
            new_value TEXT NOT NULL,
            reason TEXT DEFAULT '',
            source_description TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        """,
        "relationship_change_proposals": f"""
            field_name TEXT NOT NULL CHECK (field_name IN ({fields})),
            old_value TEXT NOT NULL,
            proposed_value TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        """,
    }
    connection.execute("CREATE UNIQUE INDEX idx_characters_campaign_id ON characters(campaign_id, id)")
    for table, columns in table_columns.items():
        old_columns = ", ".join(
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        )
        sequence = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
        ).fetchone()
        connection.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        connection.execute(f"""
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                target_character_id INTEGER,
                target_player_id INTEGER GENERATED ALWAYS AS (
                    CASE WHEN target_character_id IS NULL THEN campaign_id END
                ) VIRTUAL,
                {columns},
                CHECK (target_character_id IS NULL OR target_character_id <> character_id),
                FOREIGN KEY (campaign_id, character_id)
                    REFERENCES characters(campaign_id, id) ON DELETE CASCADE,
                FOREIGN KEY (campaign_id, target_character_id)
                    REFERENCES characters(campaign_id, id) ON DELETE CASCADE,
                FOREIGN KEY (target_player_id) REFERENCES player_profiles(campaign_id)
                    ON DELETE CASCADE
            )
        """)
        connection.execute(f"""
            INSERT INTO {table} ({old_columns})
            SELECT {old_columns} FROM {table}_legacy
        """)
        connection.execute(f"DROP TABLE {table}_legacy")
        if sequence:
            connection.execute(
                "UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?",
                (sequence[0], table),
            )
    connection.execute("""
        CREATE UNIQUE INDEX idx_relationships_target_npc
        ON character_relationships(campaign_id, character_id, target_character_id)
    """)
    connection.execute("""
        CREATE UNIQUE INDEX idx_relationships_campaign
        ON character_relationships(campaign_id, character_id)
        WHERE target_character_id IS NULL
    """)
    connection.execute("""
        CREATE INDEX idx_relationship_history
        ON relationship_history(campaign_id, character_id, target_character_id, id)
    """)
    connection.execute("""
        CREATE INDEX idx_relationship_proposals_campaign
        ON relationship_change_proposals(campaign_id, status)
    """)
    connection.execute("""
        INSERT OR IGNORE INTO character_relationships (campaign_id, character_id)
        SELECT campaign_id, id FROM characters
    """)
    connection.execute("""
        INSERT INTO character_relationships (campaign_id, character_id, target_character_id)
        SELECT source.campaign_id, source.id, target.id
        FROM characters source
        JOIN characters target ON target.campaign_id = source.campaign_id AND target.id <> source.id
    """)
    connection.execute("""
        CREATE TRIGGER seed_character_relationships AFTER INSERT ON characters
        BEGIN
            INSERT INTO character_relationships (campaign_id, character_id)
            VALUES (NEW.campaign_id, NEW.id);
            INSERT INTO character_relationships (campaign_id, character_id, target_character_id)
            SELECT NEW.campaign_id, NEW.id, id FROM characters
            WHERE campaign_id = NEW.campaign_id AND id <> NEW.id;
            INSERT INTO character_relationships (campaign_id, character_id, target_character_id)
            SELECT NEW.campaign_id, id, NEW.id FROM characters
            WHERE campaign_id = NEW.campaign_id AND id <> NEW.id;
        END
    """)


def migrate_campaigns(database, *, backup=True):
    database = Path(database)
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'player_profiles'"
        ).fetchone():
            return
        backup_path = backup_database(database) if backup else None
        connection.execute("""
            CREATE TABLE player_profiles (
                campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
                name TEXT NOT NULL DEFAULT 'Player' CHECK (length(trim(name)) > 0),
                description TEXT NOT NULL DEFAULT '',
                background TEXT NOT NULL DEFAULT ''
            )
        """)
        for campaign_id, in connection.execute("SELECT id FROM campaigns"):
            names = {
                name.strip().casefold()
                for name, in connection.execute(
                    "SELECT name FROM characters WHERE campaign_id = ?", (campaign_id,)
                )
            }
            name = "Player"
            suffix = 2
            while name.casefold() in names:
                name = f"Player {suffix}"
                suffix += 1
            connection.execute(
                "INSERT INTO player_profiles (campaign_id, name) VALUES (?, ?)",
                (campaign_id, name),
            )
        connection.execute("""
            CREATE TRIGGER seed_campaign_player AFTER INSERT ON campaigns
            BEGIN
                INSERT INTO player_profiles (campaign_id) VALUES (NEW.id);
            END
        """)
        for table, other in (("player_profiles", "characters"), ("characters", "player_profiles")):
            for event in ("INSERT", "UPDATE OF name, campaign_id"):
                trigger = f"{table}_check_player_name_{event.split()[0].lower()}"
                connection.execute(f"""
                    CREATE TRIGGER {trigger} BEFORE {event} ON {table}
                    WHEN EXISTS (
                        SELECT 1 FROM {other} WHERE campaign_id = NEW.campaign_id
                        AND trim(name) = trim(NEW.name) COLLATE NOCASE
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'Player and NPC names must be distinct');
                    END
                """)
        migrate_relationships(connection)
        connection.execute("ALTER TABLE messages ADD COLUMN narrator_beats TEXT")
        ensure_memory_table(connection)
        connection.execute("""
            ALTER TABLE character_memories ADD COLUMN source_message_id INTEGER
            REFERENCES messages(id) ON DELETE SET NULL
        """)
        connection.execute("ALTER TABLE character_memories ADD COLUMN source_beat INTEGER")
        connection.execute("ALTER TABLE character_memories ADD COLUMN speaker_ref TEXT")
        connection.execute("ALTER TABLE character_memories ADD COLUMN participant_refs TEXT")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Campaign migration failed foreign key validation")
    return backup_path


def migrate_knowledge(database, *, backup=True):
    database = Path(database)
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(character_knowledge)")}
        if "source_message_id" in columns:
            return
        backup_path = backup_database(database, "automatic-knowledge") if backup else None
        for table in ("character_knowledge", "character_knowledge_proposals"):
            deletion = "CASCADE" if table == "character_knowledge_proposals" else "SET NULL"
            connection.execute(f"""
                ALTER TABLE {table} ADD COLUMN source_message_id INTEGER
                REFERENCES messages(id) ON DELETE {deletion}
            """)
            connection.execute(f"ALTER TABLE {table} ADD COLUMN source_beat INTEGER")
            connection.execute(f"""
                ALTER TABLE {table} ADD COLUMN player_visible INTEGER NOT NULL DEFAULT 0
                CHECK (player_visible IN (0, 1))
            """)
        connection.execute("""
            CREATE INDEX idx_automatic_knowledge_event
            ON character_knowledge_proposals(event_id, status)
            WHERE source_message_id IS NOT NULL
        """)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Knowledge migration failed foreign key validation")
    return backup_path


def migrate_injuries(database, *, backup=True):
    database = Path(database)
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(character_states)")}
        if "injury_status" in columns:
            return
        backup_path = backup_database(database, "critical-injuries") if backup else None
        connection.execute("""
            ALTER TABLE character_states ADD COLUMN injury_status TEXT NOT NULL DEFAULT 'unassessed'
            CHECK (injury_status IN ('unassessed', 'none', 'minor', 'serious', 'critical', 'dead'))
        """)
        connection.execute("""
            CREATE TABLE critical_injury_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                condition TEXT NOT NULL,
                evidence TEXT NOT NULL,
                source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                source_beat INTEGER,
                outcome TEXT NOT NULL DEFAULT 'pending'
                    CHECK (outcome IN ('pending', 'survived', 'died')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                FOREIGN KEY (campaign_id, character_id)
                    REFERENCES characters(campaign_id, id) ON DELETE CASCADE
            )
        """)
        connection.execute("""
            CREATE UNIQUE INDEX idx_pending_critical_injury
            ON critical_injury_decisions(campaign_id, character_id) WHERE outcome = 'pending'
        """)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Injury migration failed foreign key validation")
    return backup_path


def migrate_continuity(database, *, backup=True):
    database = Path(database)
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recall_messages'"
        ).fetchone():
            return
        backup_path = backup_database(database, "long-campaign-recall") if backup else None
        for table, body, key, scope in (
            ("character_memories", "memory", "memory_key", "campaign_id, character_id"),
            ("world_events", "description", "event_key", "campaign_id"),
        ):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {key} TEXT")
            rows = connection.execute(f"SELECT id, {body} FROM {table}")
            while batch := rows.fetchmany(500):
                connection.executemany(
                    f"UPDATE {table} SET {key} = ? WHERE id = ?",
                    ((normalized_key(text), row_id) for row_id, text in batch),
                )
            connection.execute(f"CREATE INDEX idx_{table}_{key} ON {table}({scope}, {key})")
            connection.execute(f"""
                CREATE TRIGGER reset_{key} AFTER UPDATE OF {body} ON {table}
                WHEN NEW.{body} <> OLD.{body} AND NEW.{key} IS OLD.{key}
                BEGIN
                    UPDATE {table} SET {key} = NULL WHERE id = NEW.id;
                END
            """)
        create_recall_indexes(connection)
        connection.execute("""
            CREATE INDEX idx_player_discoveries_recent ON player_discoveries(campaign_id, id DESC)
        """)
        connection.execute("""
            CREATE INDEX idx_public_npc_knowledge_recent ON character_knowledge(character_id, id DESC)
            WHERE player_visible = 1 AND source_message_id IS NOT NULL
        """)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Continuity migration failed foreign key validation")
    return backup_path
