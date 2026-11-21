"""Read and update directional relationships using the same key throughout."""

from app.relationship_analyzer import ALLOWED_FIELDS, CATEGORICAL_FIELDS, NUMERIC_FIELDS


PAIR = "campaign_id = ? AND character_id = ? AND target_character_id IS ?"


def load_relationships(connection, campaign_id):
    return connection.execute("""
        SELECT r.*, source.name AS character_name,
               coalesce(target.name, player.name) AS target_name
        FROM character_relationships r
        JOIN characters source ON source.id = r.character_id
        LEFT JOIN characters target ON target.id = r.target_character_id
        LEFT JOIN player_profiles player ON player.campaign_id = r.target_player_id
        WHERE r.campaign_id = ?
        ORDER BY r.character_id, r.target_character_id
    """, (campaign_id,)).fetchall()


def record_relationship_change(connection, pair, field, old_value, new_value, reason,
                               source_description):
    if field not in ALLOWED_FIELDS:
        return False
    cursor = connection.execute(
        f"""UPDATE character_relationships SET {field} = ?, updated_at = CURRENT_TIMESTAMP
            WHERE {PAIR} AND CAST({field} AS TEXT) = ? AND {field} <> ?""",
        (new_value, *pair, str(old_value), new_value),
    )
    if not cursor.rowcount:
        return False
    connection.execute("""
        INSERT INTO relationship_history (
            campaign_id, character_id, target_character_id, field_name, old_value,
            new_value, reason, source_description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (*pair, field, str(old_value), str(new_value), reason, source_description))
    return True


def apply_relationship_proposal(connection, campaign_id, proposal):
    field = proposal["field_name"]
    old_value = proposal["old_value"]
    new_value = proposal["proposed_value"]
    if field in NUMERIC_FIELDS:
        try:
            old_number, new_value = int(old_value), int(new_value)
        except (TypeError, ValueError):
            return False
        if not (0 <= new_value <= 100 and 0 < abs(new_value - old_number) <= 10):
            return False
    elif field not in CATEGORICAL_FIELDS or new_value not in CATEGORICAL_FIELDS[field]:
        return False
    pair = (campaign_id, proposal["character_id"], proposal["target_character_id"])
    return record_relationship_change(
        connection, pair, field, old_value, new_value, proposal["reason"],
        "Automatic relationship analyzer",
    )
