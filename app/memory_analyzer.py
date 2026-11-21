"""Automatic, character-specific conversational memory for the roleplay engine."""
import json
import httpx

from app.identity import PLAYER_REF, describe_player, npc_ref, participant_roster
from app.narration import player_observers
from app.continuity import PUBLIC_MEMORY, compact, fit_context, match_query, normalized_key


def ensure_memory_table(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS character_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            memory TEXT NOT NULL,
            knowledge_status TEXT NOT NULL DEFAULT 'knows',
            source_description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
            FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_character_memories_lookup
        ON character_memories(campaign_id, character_id, id)
    """)


def load_memories(connection, campaign_id, limit_per_character=10, *, player_visible=False, terms=()):
    columns = """
        cm.id, cm.character_id, c.name AS character_name, cm.memory, cm.knowledge_status,
        cm.source_description, cm.source_message_id, cm.source_beat, cm.speaker_ref, cm.participant_refs
    """
    characters = connection.execute(
        "SELECT id FROM characters WHERE campaign_id = ? ORDER BY id", (campaign_id,)
    ).fetchall()
    visibility = f"AND ({PUBLIC_MEMORY})" if player_visible else ""
    selected = {}
    for character in characters:
        base = f"""
            JOIN characters c ON c.id = cm.character_id
            WHERE cm.campaign_id = ? AND cm.character_id = ? {visibility}
        """
        if terms:
            matched = connection.execute(f"""
                SELECT {columns} FROM recall_character_memories
                JOIN character_memories cm ON cm.id = recall_character_memories.rowid
                {base} AND recall_character_memories MATCH ?
                ORDER BY rank, recall_character_memories.rowid DESC LIMIT 5
            """, (campaign_id, character["id"], match_query(campaign_id, terms))).fetchall()
            selected.update((row["id"], dict(row)) for row in matched)
    for character in characters:
        recent = connection.execute(f"""
            SELECT {columns} FROM character_memories cm
            JOIN characters c ON c.id = cm.character_id
            WHERE cm.campaign_id = ? AND cm.character_id = ? {visibility}
            ORDER BY cm.id DESC LIMIT ?
        """, (campaign_id, character["id"], limit_per_character)).fetchall()
        selected.update((row["id"], dict(row)) for row in recent)
    return list(selected.values())


async def analyze_conversation_memories(user_message, beats, player, characters,
                                        existing_memories, ollama_url, model):
    if not characters:
        return []
    roster = participant_roster(player, characters)
    npc_ids = {npc_ref(character["id"]): character["id"] for character in characters}
    instructions = """Extract up to four durable, character-specific memories from the latest exchange.
Return JSON only, using this structure:
{"memories": [{"owner": "npc:1", "memory": "...", "knowledge_status": "knows",
  "source": "user", "participants": ["player", "npc:1"], "evidence": "exact words"}]}
Rules:
- Use roster references for the NPC owner and participants and actual names in the memory.
- Source is "user" for the user message or a zero-based integer for a narrator beat.
- Quote a short exact excerpt of that source as evidence. Include the memory owner
  and the source speaker (if there is one) in participants.
- The speaker for the user message is the player. An explicit quotation or report
  of another NPC by the player is only something the player told the recipient.
- For narrator beats the speaker and participants are supplied by the engine.
  The narrator itself is never a speaking character. Use only the beat's speaker,
  participants and witnesses. Private thoughts never create memories for others.
- For the user message use only NPCs in player_observers who could perceive the
  relevant part of the message. Never treat an out-of-character direction,
  mention of a name, or imagined event as something an NPC learned.
- Use knowledge_status "knows" for direct observations and the fact someone spoke,
  "believes" for unverified beliefs, or "suspects" for suspicions. Record who
  promised or disclosed what to whom. Do not treat a promise as a future fact.
- Skip ambiguous attribution, private information, pleasantries and duplicates.
  Existing memories are context for deduplication only.
"""
    payload = {
        "model": model, "think": False, "stream": False,
        "format": {
            "type": "object",
            "required": ["memories"],
            "properties": {"memories": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "required": ["owner", "memory", "knowledge_status", "source", "participants", "evidence"],
                    "properties": {
                        "owner": {"enum": list(npc_ids)},
                        "memory": {"type": "string"},
                        "knowledge_status": {"enum": ["knows", "believes", "suspects"]},
                        "source": {"enum": ["user", *range(len(beats))]},
                        "participants": {
                            "type": "array", "items": {"enum": list(roster)}, "maxItems": len(roster),
                        },
                        "evidence": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }},
            "additionalProperties": False,
        },
        "messages": [
            {"role": "system", "content": f"{instructions}\n{describe_player(player)}"},
            {"role": "user", "content": compact({
                "roster": roster, "user_message": user_message, "narrator_beats": beats,
                "player_observers": sorted(player_observers(beats)),
                "existing_memories": fit_context(existing_memories, 2300),
            })},
        ],
        "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 1000},
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(ollama_url, json=payload)
        response.raise_for_status()
    data = json.loads(response.json()["message"]["content"])
    if not isinstance(data, dict) or not isinstance(data.get("memories"), list):
        return []

    sources = [("user", PLAYER_REF, user_message, {PLAYER_REF, *player_observers(beats)})]
    sources.extend(
        (index, beat["speaker"], beat["text"],
         {beat["speaker"], *beat["participants"], *beat["witnesses"]})
        for index, beat in enumerate(beats)
    )
    result = []
    seen = {(m["character_id"], m["memory"].strip().casefold()) for m in existing_memories}
    for item in data["memories"][:4]:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner")
        memory = item.get("memory")
        status = item.get("knowledge_status")
        source = item.get("source")
        evidence = item.get("evidence")
        participants = item.get("participants")
        if (not isinstance(owner, str) or owner not in npc_ids
                or not isinstance(memory, str) or not 12 <= len(memory.strip()) <= 400
                or status not in ("knows", "believes", "suspects")
                or not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= 200
                or not isinstance(participants, list)
                or any(not isinstance(ref, str) or ref not in roster for ref in participants)):
            continue
        actors = set(participants)
        if owner not in actors or (source != "user" and type(source) is not int):
            continue
        evidence = evidence.strip()
        matches = [
            (source_ref, speaker)
            for source_ref, speaker, text, allowed_actors in sources
            if evidence in text and actors <= allowed_actors
            and (speaker is None or speaker in actors)
        ]
        if len(matches) == 1:
            source, speaker = matches[0]
        else:
            # Repeated wording cannot establish a source without a matching reference.
            nominated = next((match for match in matches if match[0] == source), None)
            if nominated is None:
                continue
            source, speaker = nominated
        key = (npc_ids[owner], memory.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        source_name = roster[speaker] if speaker else "Scene observation"
        result.append({
            "character_id": npc_ids[owner],
            "memory": memory.strip(),
            "knowledge_status": status,
            "source_description": f"{source_name}: {evidence}",
            "source_beat": None if source == "user" else source,
            "speaker_ref": speaker,
            "participant_refs": json.dumps(sorted(actors)),
        })
    return result


def save_memories(connection, campaign_id, user_message_id, assistant_message_id, memories):
    connection.execute("BEGIN IMMEDIATE")
    saved = 0
    for memory in memories:
        key = normalized_key(memory["memory"])
        scope = (campaign_id, memory["character_id"])
        existing = connection.execute("""
            SELECT 1 FROM character_memories WHERE campaign_id = ? AND character_id = ? AND memory_key = ?
        """, (*scope, key)).fetchone()
        uncached = connection.execute("""
            SELECT memory FROM character_memories WHERE campaign_id = ? AND character_id = ? AND memory_key IS NULL
        """, scope) if existing is None else ()
        if existing or any(normalized_key(row[0]) == key for row in uncached):
            continue
        message_id = user_message_id if memory["source_beat"] is None else assistant_message_id
        connection.execute("""
            INSERT INTO character_memories
                (campaign_id, character_id, memory, knowledge_status, source_description,
                 source_message_id, source_beat, speaker_ref, participant_refs, memory_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            campaign_id, memory["character_id"], memory["memory"], memory["knowledge_status"],
            memory["source_description"], message_id, memory["source_beat"],
            memory["speaker_ref"], memory["participant_refs"], key,
        ))
        saved += 1
    return saved
