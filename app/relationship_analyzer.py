# relationship_analyzer.py
import json
import httpx

from app.identity import PLAYER_REF, describe_player, npc_ref, participant_roster

NUMERIC_FIELDS = {
    "affection",
    "attraction",
    "trust",
    "respect",
    "resentment",
    "friendship",
}
CATEGORICAL_FIELDS = {
    "romantic_interest": {
        "none",
        "curious",
        "interested",
        "developing_feelings",
        "in_love",
    },
    "sexual_interest": {
        "none",
        "curious",
        "interested",
        "strong_attraction",
        "casual_encounters",
    },
}
ALLOWED_FIELDS = NUMERIC_FIELDS | set(CATEGORICAL_FIELDS)


async def analyze_relationship_changes(
    user_message: str,
    beats: list,
    player,
    characters: list,
    relationships: list,
    ollama_url: str,
    model: str,
    num_ctx: int,
):
    """Return validated relationship suggestions without changing the database."""
    roster = participant_roster(player, characters)
    current = {
        (npc_ref(item["character_id"]),
         PLAYER_REF if item["target_character_id"] is None else npc_ref(item["target_character_id"])): item
        for item in relationships
    }
    if not current:
        return []

    context = [
        {"source": source, "target": target,
         **{field: row[field] for field in sorted(ALLOWED_FIELDS)}}
        for (source, target), row in current.items()
    ]
    profiles = {
        npc_ref(character["id"]): {
            "personality": character["personality"], "background": character["background"],
        } for character in characters
    }
    system_prompt = f"""You analyze how an NPC feels toward a specific player or NPC.
{describe_player(player)}
Only evaluate the LATEST exchange, using the character's actual words, actions, and established context.
Each listed relationship is directional. The source NPC holds the feelings toward the target.
Use roster references. Do not assume reciprocal feelings.

RULES:
1. Most ordinary exchanges cause no measurable relationship change. Return an empty list when appropriate.
2. A polite remark, ordinary greeting, or simple question does not automatically increase affection or trust.
3. Propose only changes supported by specific behavior or events in this exchange. Do not invent events or motives.
4. Interpret reactions according to each character's personality and existing relationship.
5. Affection, attraction, trust, respect, resentment, friendship, romantic_interest, and sexual_interest are independent.
6. Sexual attraction or interest in casual encounters does not imply romance. Romance does not imply sexual interest.
7. Keep numeric changes small (usually 1 to 5 points). Exceptional, strongly supported events may justify up to 10 points.
8. Numeric values must remain between 0 and 100. Never change a numeric value by more than 10 in one exchange.
9. Categorical changes require clear, strong evidence and normally progress gradually. Never infer love or casual sexual intent from generic friendliness.
10. A character can forgive slowly; an apology need not erase resentment or restore trust immediately.
11. Only update characters actually involved or clearly affected by the exchange.
12. Return at most one proposal per source, target, and field.
13. Return JSON only, using the structure below.

ALLOWED NUMERIC FIELDS: {json.dumps(sorted(NUMERIC_FIELDS))}
ALLOWED CATEGORICAL VALUES: {json.dumps({field: sorted(values) for field, values in CATEGORICAL_FIELDS.items()}, sort_keys=True)}

REQUIRED JSON STRUCTURE:
{{"proposals": [{{"source": "npc:1", "target": "player", "field_name": "trust", "proposed_value": 12, "reason": "Specific observed reason"}}]}}
If no meaningful change is justified, return {{"proposals": []}}.
"""
    payload = {
        "model": model,
        "think": False,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps({
                    "roster": roster, "npc_profiles": profiles, "relationships": context,
                    "user_message": user_message, "narrator_beats": beats,
                }, ensure_ascii=False),
            },
        ],
        "options": {"num_ctx": num_ctx, "num_predict": 700, "temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(ollama_url, json=payload)
    response.raise_for_status()
    analysis = json.loads(response.json()["message"]["content"])
    if not isinstance(analysis, dict) or not isinstance(
        analysis.get("proposals", []), list
    ):
        raise ValueError("Relationship analyzer returned invalid proposals JSON")

    validated = []
    seen = set()
    for item in analysis.get("proposals", []):
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        target = item.get("target")
        field = item.get("field_name")
        if not all(isinstance(value, str) for value in (source, target, field)):
            continue
        relationship = current.get((source, target))
        key = (source, target, field)
        if relationship is None or field not in ALLOWED_FIELDS or key in seen:
            continue
        old_value = relationship[field]
        proposed = item.get("proposed_value")
        if field in NUMERIC_FIELDS:
            if isinstance(proposed, bool) or not isinstance(proposed, int):
                continue
            if not 0 <= proposed <= 100 or abs(proposed - old_value) > 10:
                continue
        else:
            if (
                not isinstance(proposed, str)
                or proposed not in CATEGORICAL_FIELDS[field]
            ):
                continue
        if proposed == old_value:
            continue
        reason = item.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            continue
        validated.append(
            {
                "character_id": relationship["character_id"],
                "target_character_id": relationship["target_character_id"],
                "field_name": field,
                "old_value": str(old_value),
                "proposed_value": str(proposed),
                "reason": reason.strip()[:500],
            }
        )
        seen.add(key)
    return validated
