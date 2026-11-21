import sqlite3
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.campaign_schema import migrate_campaign_rules, migrate_campaigns, migrate_continuity, migrate_injuries, migrate_knowledge

DATABASE = Path(__file__).parent / "roleplay.db"


def initialize_base_database(database):
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        # Campaigns
        connection.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                rules TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Characters
        connection.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                age INTEGER,
                description TEXT DEFAULT '',
                personality TEXT DEFAULT '',
                background TEXT DEFAULT '',
                current_state TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,
                UNIQUE (campaign_id, name)
            )
        """)

        # Persistent character state
        connection.execute("""
            CREATE TABLE IF NOT EXISTS character_states (
                character_id INTEGER PRIMARY KEY,
                location TEXT DEFAULT '',
                physical_condition TEXT DEFAULT '',
                emotional_state TEXT DEFAULT '',
                appearance TEXT DEFAULT '',
                clothing TEXT DEFAULT '',
                possessions TEXT DEFAULT '',
                current_goals TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            INSERT OR IGNORE INTO character_states (character_id)
            SELECT id FROM characters
        """)

        # Conversation history
        connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_campaign
            ON messages(campaign_id, id)
        """)

        # Character state change proposals
        connection.execute("""
            CREATE TABLE IF NOT EXISTS state_change_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                field_name TEXT NOT NULL CHECK (
                    field_name IN (
                        'location',
                        'physical_condition',
                        'emotional_state',
                        'appearance',
                        'clothing',
                        'possessions',
                        'current_goals'
                    )
                ),
                old_value TEXT DEFAULT '',
                proposed_value TEXT NOT NULL,
                reason TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending',
                        'accepted',
                        'rejected'
                    )),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_state_proposals_campaign
            ON state_change_proposals(campaign_id, status)
        """)

        # Objective world events
        connection.execute("""
            CREATE TABLE IF NOT EXISTS world_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                event_type TEXT DEFAULT 'general',
                description TEXT NOT NULL,
                location TEXT DEFAULT '',
                participants TEXT DEFAULT '[]',
                occurred_at TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'accepted'
                    CHECK (status IN (
                        'pending',
                        'accepted',
                        'rejected'
                    )),
                certainty TEXT NOT NULL DEFAULT 'confirmed'
                    CHECK (certainty IN (
                        'confirmed',
                        'uncertain'
                    )),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE
            )
        """)

        # Upgrade existing world_events tables safely
        existing_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(world_events)").fetchall()
        }

        if "status" not in existing_columns:
            connection.execute("""
                ALTER TABLE world_events
                ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'
                CHECK (status IN ('pending', 'accepted', 'rejected'))
            """)

        if "certainty" not in existing_columns:
            connection.execute("""
                ALTER TABLE world_events
                ADD COLUMN certainty TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (certainty IN ('confirmed', 'uncertain'))
            """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_world_events_campaign
            ON world_events(campaign_id, id)
        """)

        # Individual character knowledge
        connection.execute("""
            CREATE TABLE IF NOT EXISTS character_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                knowledge_status TEXT NOT NULL
                    CHECK (knowledge_status IN (
                        'knows',
                        'believes',
                        'suspects'
                    )),
                knowledge_detail TEXT DEFAULT '',
                source_description TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (event_id)
                    REFERENCES world_events(id)
                    ON DELETE CASCADE,
                UNIQUE (character_id, event_id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_character_knowledge
            ON character_knowledge(character_id, event_id)
        """)

        # Character knowledge proposals
        connection.execute("""
            CREATE TABLE IF NOT EXISTS character_knowledge_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                knowledge_status TEXT NOT NULL
                    CHECK (knowledge_status IN (
                        'knows',
                        'believes',
                        'suspects'
                    )),
                knowledge_detail TEXT DEFAULT '',
                source_description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending',
                        'accepted',
                        'rejected'
                    )),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (event_id)
                    REFERENCES world_events(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_proposals_campaign
            ON character_knowledge_proposals(campaign_id, status)
        """)

        # Player discoveries
        connection.execute("""
            CREATE TABLE IF NOT EXISTS player_discoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                discovery_note TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (event_id)
                    REFERENCES world_events(id)
                    ON DELETE CASCADE,
                UNIQUE (campaign_id, event_id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_player_discoveries
            ON player_discoveries(campaign_id, event_id)
        """)

        # Current relationships between characters and the user
        connection.execute("""
            CREATE TABLE IF NOT EXISTS character_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL UNIQUE,

                affection INTEGER NOT NULL DEFAULT 0
                    CHECK (affection BETWEEN 0 AND 100),

                attraction INTEGER NOT NULL DEFAULT 0
                    CHECK (attraction BETWEEN 0 AND 100),

                trust INTEGER NOT NULL DEFAULT 0
                    CHECK (trust BETWEEN 0 AND 100),

                respect INTEGER NOT NULL DEFAULT 0
                    CHECK (respect BETWEEN 0 AND 100),

                resentment INTEGER NOT NULL DEFAULT 0
                    CHECK (resentment BETWEEN 0 AND 100),

                friendship INTEGER NOT NULL DEFAULT 0
                    CHECK (friendship BETWEEN 0 AND 100),

                romantic_interest TEXT NOT NULL DEFAULT 'none'
                    CHECK (romantic_interest IN (
                        'none',
                        'curious',
                        'interested',
                        'developing_feelings',
                        'in_love'
                    )),

                sexual_interest TEXT NOT NULL DEFAULT 'none'
                    CHECK (sexual_interest IN (
                        'none',
                        'curious',
                        'interested',
                        'strong_attraction',
                        'casual_encounters'
                    )),

                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationships_campaign
            ON character_relationships(campaign_id)
        """)

        # Permanent history of relationship changes
        connection.execute("""
            CREATE TABLE IF NOT EXISTS relationship_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,

                field_name TEXT NOT NULL
                    CHECK (field_name IN (
                        'affection',
                        'attraction',
                        'trust',
                        'respect',
                        'resentment',
                        'friendship',
                        'romantic_interest',
                        'sexual_interest'
                    )),

                old_value TEXT DEFAULT '',
                new_value TEXT NOT NULL,
                reason TEXT DEFAULT '',
                source_description TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationship_history
            ON relationship_history(character_id, id)
        """)

        # AI-proposed relationship changes
        connection.execute("""
            CREATE TABLE IF NOT EXISTS relationship_change_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                field_name TEXT NOT NULL
                    CHECK (field_name IN (
                        'affection',
                        'attraction',
                        'trust',
                        'respect',
                        'resentment',
                        'friendship',
                        'romantic_interest',
                        'sexual_interest'
                    )),
                old_value TEXT NOT NULL,
                proposed_value TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending',
                        'accepted',
                        'rejected'
                    )),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id)
                    REFERENCES campaigns(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (character_id)
                    REFERENCES characters(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationship_proposals_campaign
            ON relationship_change_proposals(campaign_id, status)
        """)

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def initialize_database(database=DATABASE):
    database = Path(database)
    existing = database.exists()
    if not existing:
        initialize_base_database(database)
    migrate_campaigns(database, backup=existing)
    migrate_knowledge(database, backup=existing)
    migrate_injuries(database, backup=existing)
    migrate_continuity(database, backup=existing)
    migrate_campaign_rules(database, backup=existing)
    print("Database initialized successfully!")


if __name__ == "__main__":
    initialize_database()
