"""Retrieve recent and relevant campaign context without expanding the chat prompt indefinitely."""

import json
import re

from app.narration import visible_to_player

STOP_WORDS = set("""
    about after again also and are at before being but can could did does for from had has have
    her here him his how into its just like may more much must not now only our out said she
    should some than that the their them then there these they this through too use very want
    was were what when where which while who why will with would you your tell ask says remember
""".split())

PUBLIC_MEMORY = """
    cm.source_message_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM json_each(
            CASE WHEN json_valid(cm.participant_refs) THEN cm.participant_refs END
        ) WHERE value = 'player'
    )
"""

SEARCH_SOURCES = {
    "messages": ("campaign_id", "COALESCE(content, '')"),
    "character_memories": ("campaign_id", "COALESCE(memory, '')"),
    "world_events": (
        "campaign_id",
        "COALESCE(description, '') || ' ' || COALESCE(location, '') || ' ' || COALESCE(participants, '')",
    ),
    "character_knowledge": (
        "(SELECT campaign_id FROM characters WHERE id = character_knowledge.character_id)",
        "COALESCE(knowledge_detail, '')",
    ),
}


def normalized_key(text):
    return text.strip().casefold()


def create_recall_indexes(connection):
    for source, (campaign, body) in SEARCH_SOURCES.items():
        index = f"recall_{source}"
        connection.execute(f"""
            CREATE VIRTUAL TABLE {index} USING fts5(campaign, body, tokenize='unicode61 remove_diacritics 2')
        """)
        insert = f"""
            INSERT INTO {index}(rowid, campaign, body)
            SELECT id, 'c' || ({campaign}), {body} FROM {source}
        """
        connection.execute(insert)
        operations = {
            "insert": f"{insert} WHERE id = NEW.id;",
            "update": f"DELETE FROM {index} WHERE rowid = OLD.id; {insert} WHERE id = NEW.id;",
            "delete": f"DELETE FROM {index} WHERE rowid = OLD.id;",
        }
        for operation, statements in operations.items():
            connection.execute(f"""
                CREATE TRIGGER {index}_{operation} AFTER {operation.upper()} ON {source}
                BEGIN
                    {statements}
                END
            """)


def search_terms(*texts):
    words = (word.lower() for text in texts for word in re.findall(r"[^\W_]+", text, flags=re.UNICODE))
    return list(dict.fromkeys(
        word for word in words if 3 <= len(word) <= 48 and word not in STOP_WORDS and not word.isdecimal()
    ))[:12]


def match_query(campaign_id, terms):
    return f'campaign : c{campaign_id} AND body : (' + " OR ".join(f'"{term}"' for term in terms) + ")"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fit_context(rows, budget):
    """Return up to budget UTF-8 bytes of JSON, preferring the supplied order."""
    selected = []
    remaining = budget - 2
    for row in rows:
        size = len(compact(row).encode("utf-8")) + bool(selected)
        if size <= remaining:
            selected.append(row)
            remaining -= size
    return selected


def recalled_messages(connection, campaign_id, terms, before_id, limit=6):
    if not terms or before_id is None:
        return []
    rows = connection.execute("""
        SELECT message.id, message.role, snippet(recall_messages, 1, '', '', ' … ', 32) AS excerpt,
               message.narrator_beats
        FROM recall_messages
        JOIN messages message ON message.id = recall_messages.rowid
        WHERE recall_messages MATCH ? AND recall_messages.rowid < ? AND message.campaign_id = ?
        ORDER BY rank, recall_messages.rowid DESC LIMIT ?
    """, (match_query(campaign_id, terms), before_id, campaign_id, limit)).fetchall()
    return [dict(row) for row in rows]


def transcript_entry(message, *, archive=False):
    text = message["excerpt"] if archive else message["content"]
    turn = {"message_id": message["id"], "role": message["role"],
            "content": text[:1300] + (" …" if len(text) > 1300 else "")}
    if message["narrator_beats"]:
        try:
            beats = json.loads(message["narrator_beats"])
        except (TypeError, ValueError):
            beats = []
        if isinstance(beats, list):
            turn["attribution"] = [
                {key: beat[key] for key in ("speaker", "participants", "witnesses", "player_observers")
                 if key in beat}
                for beat in beats if isinstance(beat, dict)
                and all(isinstance(beat.get(key), list) for key in ("participants", "witnesses"))
                and visible_to_player(beat)
            ]
    return turn


def event_candidates(connection, campaign_id, terms, *, include_rejected, recent=16, relevant=20):
    columns = "event.id, event.event_type, event.description, event.location, event.participants, event.occurred_at, event.status, event.certainty"
    statuses = "" if include_rejected else "AND event.status <> 'rejected'"
    matches = []
    if terms:
        matches = connection.execute(f"""
            SELECT {columns}
            FROM recall_world_events
            JOIN world_events event ON event.id = recall_world_events.rowid
            WHERE recall_world_events MATCH ? AND event.campaign_id = ? {statuses}
            ORDER BY rank, recall_world_events.rowid DESC LIMIT ?
        """, (match_query(campaign_id, terms), campaign_id, relevant)).fetchall()
    latest = connection.execute(f"""
        SELECT {columns} FROM world_events event
        WHERE event.campaign_id = ? {statuses} ORDER BY event.id DESC LIMIT ?
    """, (campaign_id, recent)).fetchall()
    by_id = {row["id"]: dict(row) for row in [*matches, *latest]}
    return fit_context(by_id.values(), 7400)


def save_proposed_events(connection, campaign_id, events):
    connection.execute("BEGIN IMMEDIATE")
    saved = []
    for event in events:
        key = normalized_key(event["description"])
        duplicate = connection.execute(
            "SELECT 1 FROM world_events WHERE campaign_id = ? AND event_key = ?", (campaign_id, key)
        ).fetchone()
        uncached = connection.execute(
            "SELECT description FROM world_events WHERE campaign_id = ? AND event_key IS NULL",
            (campaign_id,),
        ) if duplicate is None else ()
        if duplicate or any(normalized_key(row[0]) == key for row in uncached):
            continue
        event_id = connection.execute("""
            INSERT INTO world_events (
                campaign_id, event_type, description, location, participants,
                occurred_at, status, certainty, event_key
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (
            campaign_id, event["event_type"], event["description"], event["location"],
            compact(event["participants"]), event["occurred_at"], event["certainty"], key,
        )).lastrowid
        saved.append(dict(event, id=event_id, status="pending"))
    return saved
