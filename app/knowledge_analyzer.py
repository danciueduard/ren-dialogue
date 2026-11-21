"""Link what an individual NPC perceived to a campaign event."""

import json
import re

import httpx

from app.identity import PLAYER_REF, describe_player, npc_ref, participant_roster
from app.narration import player_observers, visible_to_player


STATUSES = ("suspects", "believes", "knows")
DIRECT_DISCLOSURE = re.compile(
    r'(?:^|[.!?]\s+|\n)I\s+(?:tell|say to|whisper to)\s+([^.!?,“"]+?)\s*,?\s*[“"]([^”"]+)[”"]',
    re.I,
)


def exact_player_observations(user_message, observers, roster, npc_ids, events):
    """Recover an explicit, verbatim event claim the model omitted."""
    if not observers or re.match(r"\s*\(?\s*(?:OOC\b|out of character\b)", user_message, re.I):
        return []
    by_name = {roster[ref].casefold(): ref for ref in observers}
    results = []
    for disclosure in DIRECT_DISCLOSURE.finditer(user_message):
        recipient, quote = disclosure.groups()
        recipient = recipient.strip().casefold()
        learner = by_name.get(recipient)
        if learner is None and len(observers) == 1 and recipient in ("him", "her", "them"):
            learner = next(iter(observers))
        if learner is None:
            continue
        for evidence in re.split(r"(?<=[.!?])\s+", quote.strip()):
            if not 12 <= len(evidence) <= 320:
                continue
            key = evidence.rstrip(".!?").casefold()
            matches = [
                event for event in events
                if any(key == sentence.strip().rstrip(".!?").casefold()
                       for sentence in re.split(r"(?<=[.!?])\s+", event["description"]))
            ]
            accepted = [event for event in matches if event.get("status") == "accepted"]
            candidates = accepted or matches
            if len(candidates) == 1:
                results.append({
                    "character_id": npc_ids[learner], "event_id": candidates[0]["id"],
                    "knowledge_status": "believes", "knowledge_detail": evidence,
                    "source_description": f"Heard from {roster[PLAYER_REF]}",
                    "source_beat": None, "player_visible": 1,
                })
    return results


async def analyze_character_knowledge(
    user_message, beats, characters, world_events, ollama_url, model, player,
):
    roster = participant_roster(player, characters)
    npc_ids = {npc_ref(character["id"]): character["id"] for character in characters}
    if not npc_ids or not world_events:
        return []
    event_ids = {event["id"] for event in world_events}
    observers = player_observers(beats)
    instructions = """Link what an NPC personally learned in this exchange to an event from the catalog.
The event catalog is private reference data. Names on an event, previous knowledge,
and facts found only in the catalog are never evidence that anyone knows them.
For each observation give the NPC reference in learner, an event_id, the source
("user" or the zero-based index of a narrator beat), knowledge_status, and a short
exact excerpt of that source as evidence. The excerpt itself must contain the
event-related information the NPC obtained. Skip vague reactions and repetition.
For "user", only an NPC listed as a player observer who actually saw or heard
the relevant part can learn from it. Ignore out-of-character directions,
private player thoughts, hypothetical events, and unsupported guesses.
For a beat, only a participant or witness can learn about it. The dialogue
speaker already knows what they said and gains no knowledge from saying it.
Use knows for directly perceived details, believes for unverified claims, and
suspects for tentative deductions. Hearing a claim is not confirmation.
Record only what was perceived, never a hidden cause or a whole event inferred
from a partial observation. A pending event is only a candidate for association.
Return an empty observations array if there is no grounded update.
"""
    payload = {
        "model": model, "think": False, "stream": False,
        "format": {
            "type": "object", "required": ["observations"], "additionalProperties": False,
            "properties": {"observations": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["learner", "event_id", "source", "knowledge_status", "evidence"],
                    "properties": {
                        "learner": {"enum": list(npc_ids)},
                        "event_id": {"enum": sorted(event_ids)},
                        "source": {"enum": ["user", *range(len(beats))]},
                        "knowledge_status": {"enum": list(STATUSES)},
                        "evidence": {"type": "string"},
                    },
                },
            }},
        },
        "messages": [
            {"role": "system", "content": f"{instructions}\n{describe_player(player)}"},
            {"role": "user", "content": json.dumps({
                "roster": roster, "events": world_events,
                "player_observers": sorted(observers), "user_message": user_message,
                "narrator_beats": beats,
            }, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.1, "num_predict": 1000},
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(ollama_url, json=payload)
    response.raise_for_status()
    data = json.loads(response.json()["message"]["content"])
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        raise ValueError("Knowledge analyzer returned invalid observations")

    sources = [("user", user_message, PLAYER_REF, True, True, observers)]
    sources.extend(
        (index, beat["text"], beat["speaker"], visible_to_player(beat),
         beat["kind"] == "dialogue",
         {*beat["participants"], *beat["witnesses"]} - {beat["speaker"]})
        for index, beat in enumerate(beats)
    )
    result = {}
    for item in data["observations"][:8]:
        if not isinstance(item, dict):
            continue
        learner = item.get("learner")
        event_id = item.get("event_id")
        status = item.get("knowledge_status")
        evidence = item.get("evidence")
        source = item.get("source")
        if (not isinstance(learner, str) or learner not in npc_ids
                or type(event_id) is not int or event_id not in event_ids
                or status not in STATUSES or not isinstance(evidence, str)
                or (source != "user" and type(source) is not int)
                or not 3 <= len(evidence.strip()) <= 320):
            continue
        evidence = evidence.strip()
        matches = [
            (reference, speaker, visible, is_report)
            for reference, text, speaker, visible, is_report, observers in sources
            if learner in observers and evidence in text
        ]
        matched = matches[0] if len(matches) == 1 else next(
            (match for match in matches if match[0] == source), None
        )
        if matched is None:
            continue
        source, speaker, visible, is_report = matched
        if is_report and status == "knows":
            status = "believes"
        key = (npc_ids[learner], event_id)
        previous = result.get(key)
        if previous and STATUSES.index(previous["knowledge_status"]) >= STATUSES.index(status):
            continue
        result[key] = {
            "character_id": npc_ids[learner],
            "event_id": event_id,
            "knowledge_status": status,
            "knowledge_detail": evidence,
            "source_description": f"Heard from {roster[speaker]}" if is_report else "Witnessed in scene",
            "source_beat": None if source == "user" else source,
            "player_visible": int(visible),
        }
    for observation in exact_player_observations(user_message, observers, roster, npc_ids, world_events):
        result.setdefault((observation["character_id"], observation["event_id"]), observation)
    return list(result.values())
