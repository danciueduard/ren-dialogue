"""Store grounded NPC knowledge and project player-visible context for narration."""

from app.continuity import match_query


def load_narrator_knowledge(connection, campaign_id, terms=()):
    discovery_columns = """
        SELECT event.id AS event_id, event.event_type, event.description AS world_event,
               event.location, event.occurred_at, event.certainty, discovery.discovery_note
    """
    discovery_scope = """
        JOIN world_events event ON event.id = discovery.event_id
                               AND event.campaign_id = discovery.campaign_id
        WHERE discovery.campaign_id = ? AND event.status = 'accepted'
    """
    knowledge_columns = """
        SELECT knowledge.id AS record_id, npc.id AS character_id, npc.name AS character_name,
               event.id AS event_id, knowledge.knowledge_status, knowledge.knowledge_detail,
               knowledge.source_description
    """
    knowledge_scope = """
        JOIN characters npc ON npc.id = knowledge.character_id
        JOIN world_events event ON event.id = knowledge.event_id
                               AND event.campaign_id = npc.campaign_id
        JOIN messages source ON source.id = knowledge.source_message_id
                            AND source.campaign_id = event.campaign_id
        WHERE npc.campaign_id = ? AND event.status = 'accepted' AND knowledge.player_visible = 1
          AND knowledge.source_message_id IS NOT NULL
    """
    discoveries, knowledge = [], []
    if terms:
        query = match_query(campaign_id, terms)
        discoveries.extend(connection.execute(f"""
            {discovery_columns}
            FROM recall_world_events
            JOIN player_discoveries discovery ON discovery.event_id = recall_world_events.rowid
            {discovery_scope} AND recall_world_events MATCH ?
            ORDER BY rank, recall_world_events.rowid DESC LIMIT 10
        """, (campaign_id, query)))
        knowledge.extend(connection.execute(f"""
            {knowledge_columns}
            FROM recall_character_knowledge
            JOIN character_knowledge knowledge ON knowledge.id = recall_character_knowledge.rowid
            {knowledge_scope} AND recall_character_knowledge MATCH ?
            ORDER BY rank, recall_character_knowledge.rowid DESC LIMIT 10
        """, (campaign_id, query)))
    discoveries.extend(connection.execute(f"""
        {discovery_columns} FROM player_discoveries discovery {discovery_scope}
        ORDER BY discovery.id DESC LIMIT 12
    """, (campaign_id,)))
    for npc in connection.execute("SELECT id FROM characters WHERE campaign_id = ?", (campaign_id,)):
        knowledge.extend(connection.execute(f"""
            {knowledge_columns} FROM character_knowledge knowledge {knowledge_scope}
            AND knowledge.character_id = ? ORDER BY knowledge.id DESC LIMIT 6
        """, (campaign_id, npc["id"])))
    return (
        list({row["event_id"]: dict(row) for row in discoveries}.values()),
        list({row["record_id"]: dict(row) for row in knowledge}.values()),
    )


def apply_observation(connection, observation):
    connection.execute("""
        INSERT INTO character_knowledge (
            character_id, event_id, knowledge_status, knowledge_detail, source_description,
            source_message_id, source_beat, player_visible
        ) VALUES (
            :character_id, :event_id, :knowledge_status, :knowledge_detail, :source_description,
            :source_message_id, :source_beat, :player_visible
        )
        ON CONFLICT(character_id, event_id) DO UPDATE SET
            knowledge_status = excluded.knowledge_status,
            knowledge_detail = excluded.knowledge_detail,
            source_description = excluded.source_description,
            source_message_id = excluded.source_message_id,
            source_beat = excluded.source_beat,
            player_visible = excluded.player_visible
        WHERE character_knowledge.knowledge_status = 'suspects'
              AND excluded.knowledge_status IN ('believes', 'knows')
           OR character_knowledge.knowledge_status = 'believes'
              AND excluded.knowledge_status = 'knows'
    """, dict(observation))


def save_observations(connection, campaign_id, user_message_id, assistant_message_id, observations):
    connection.execute("BEGIN IMMEDIATE")
    for observation in observations:
        source_message_id = user_message_id if observation["source_beat"] is None else assistant_message_id
        observation = dict(observation, campaign_id=campaign_id, source_message_id=source_message_id)
        event = connection.execute("""
            SELECT event.status
            FROM world_events event
            JOIN characters npc ON npc.campaign_id = event.campaign_id
            WHERE event.id = :event_id AND event.campaign_id = :campaign_id AND npc.id = :character_id
        """, observation).fetchone()
        if event is None:
            continue
        if event["status"] == "accepted":
            apply_observation(connection, observation)
        elif event["status"] == "pending":
            connection.execute("""
                INSERT INTO character_knowledge_proposals (
                    campaign_id, character_id, event_id, knowledge_status, knowledge_detail,
                    source_description, source_message_id, source_beat, player_visible
                )
                SELECT :campaign_id, :character_id, :event_id, :knowledge_status,
                       :knowledge_detail, :source_description, :source_message_id,
                       :source_beat, :player_visible
                WHERE NOT EXISTS (
                    SELECT 1 FROM character_knowledge_proposals
                    WHERE event_id = :event_id AND status = 'pending'
                      AND source_message_id IS NOT NULL AND character_id = :character_id
                      AND knowledge_status = :knowledge_status AND knowledge_detail = :knowledge_detail
                      AND player_visible = :player_visible
                )
            """, observation)


def resolve_event_observations(connection, campaign_id, event_id, status):
    pending = connection.execute("""
        SELECT * FROM character_knowledge_proposals
        WHERE campaign_id = ? AND event_id = ? AND status = 'pending'
          AND source_message_id IS NOT NULL
        ORDER BY id
    """, (campaign_id, event_id)).fetchall()
    for observation in pending:
        if status == "accepted":
            apply_observation(connection, observation)
        connection.execute(
            "UPDATE character_knowledge_proposals SET status = ? WHERE id = ?",
            (status, observation["id"]),
        )
