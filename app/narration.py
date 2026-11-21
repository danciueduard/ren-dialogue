"""Validate narrator attribution before rendering a reply or passing it to analyzers."""

import json
import re

from app.identity import PLAYER_REF


NARRATOR_FORMAT = """
Return JSON only, with a "beats" array of 1 to 8 entries in reading order.
Use one or two beats for an ordinary short exchange.
Each entry has "kind", "speaker", "participants", "player_observers", and "text".
- For dialogue, use kind "dialogue", an NPC reference as speaker, and list the
  addressees in participants. Use optional "witnesses" for other characters
  clearly present and able to hear. Write only that NPC's spoken words, without
  a speaker label or surrounding quotes.
- Keep stage directions and physical reactions in separate action or scene
  beats, including first-person descriptions such as "My eyes narrow".
- For an NPC action, use kind "action", that NPC's reference as speaker, and
  list the affected characters and clearly present witnesses. Start text with
  the verb phrase that follows the NPC's name, e.g. "lowers the lantern."
- For environmental narration, use kind "scene", null as speaker, and list
  the characters who actually observe it. Write a short standalone sentence.
- Include "player" in participants or witnesses only when the player can
  perceive that beat. The player sees only those beats. Keep private thoughts
  out of narration. Prefer at least one beat the player can perceive.
- In "player_observers", list NPCs involved in this beat who actually heard the
  user's latest in-character speech or saw the user's action. Include an NPC
  speaker who is answering what the player just told them. Use an empty list
  when nobody perceived the player. Ignore private thoughts, out-of-character
  directions, and NPCs who were merely mentioned.
- Use only references from the roster. Use "player" when addressing the player
  and the player's configured name if using a name in dialogue. An NPC name in
  direct address must refer to that NPC. Mentioning an NPC is allowed.
- The player cannot be a speaker or actor in any reply. Leave player actions,
  dialogue, decisions, feelings, and thoughts to the user. Do not describe them
  through other beats either. Keep attribution independent of earlier prose.
- Use an empty participants list when nobody else is clearly involved.
Example for a brief NPC reply:
{"beats":[{"kind":"dialogue","speaker":"npc:1","participants":["player"],"player_observers":["npc:1"],"text":"I'll be there."}]}
"""


def narration_schema(roster):
    refs = list(roster)
    actors = [ref for ref in refs if ref != PLAYER_REF]
    return {
        "type": "object",
        "required": ["beats"],
        "properties": {"beats": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["kind", "speaker", "participants", "player_observers", "text"],
                "properties": {
                    "kind": {"enum": ["dialogue", "action", "scene"] if actors else ["scene"]},
                    "speaker": {"enum": [*actors, None]},
                    "participants": {"type": "array", "items": {"enum": refs}},
                    "witnesses": {"type": "array", "items": {"enum": refs}},
                    "player_observers": {"type": "array", "items": {"enum": actors}} if actors else
                    {"type": "array", "maxItems": 0, "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }},
        "additionalProperties": False,
    }


def visible_to_player(beat):
    return PLAYER_REF in beat["participants"] or PLAYER_REF in beat["witnesses"]


def player_observers(beats):
    return {ref for beat in beats for ref in beat.get("player_observers", [])}


def parse_narration(content, roster, *, player_view=False):
    data = json.loads(content)
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or not 1 <= len(beats) <= 8:
        raise ValueError("Expected one to eight narration beats")
    lines = []
    validated = []
    for beat in beats:
        if not isinstance(beat, dict):
            raise ValueError("Invalid narration beat")
        kind = beat.get("kind")
        speaker = beat.get("speaker")
        participants = beat.get("participants")
        witnesses = beat.get("witnesses", [])
        player_observers = beat.get("player_observers", [])
        text = beat.get("text")
        if kind == "scene":
            if speaker is not None:
                raise ValueError("Scene beat cannot have a speaker")
        elif (kind not in ("dialogue", "action") or not isinstance(speaker, str)
              or speaker == PLAYER_REF or speaker not in roster):
            raise ValueError("Dialogue and actions require an NPC speaker")
        if any(not isinstance(group, list)
               or any(not isinstance(ref, str) or ref not in roster for ref in group)
               for group in (participants, witnesses, player_observers)):
            raise ValueError("Unknown participant")
        # A bystander listed only as a player observer has no corroborated perception.
        # Drop that claim so it cannot enter memory without discarding the visible beat.
        involved_npcs = {speaker, *participants, *witnesses} - {PLAYER_REF}
        player_observers = [ref for ref in player_observers if ref in involved_npcs]
        if not isinstance(text, str) or not text.strip() or len(text) > 1200:
            raise ValueError("Invalid narration text")
        text = text.strip()
        if "\n" in text or "\r" in text:
            raise ValueError("Use a separate beat for each speaker or action")
        if kind == "dialogue":
            for left, right in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
                if text.startswith(left) and text.endswith(right):
                    text = text[1:-1].strip()
                    break
            if not text:
                raise ValueError("Dialogue must contain spoken words")
            actor = re.escape(roster[speaker])
            stage_direction = (
                rf"^{actor}\s+(?:nods|shrugs|sighs|wheezes|coughs|smiles|frowns|"
                rf"glances|turns|steps|pauses|whispers|mutters)\b"
                rf"|\bmy (?:eyes|gaze|expression|face|voice) "
                rf"(?:narrow|narrows|flicks|softens|hardens|drops|tightens|doesn['’]t change)\b"
            )
            if re.search(stage_direction, text, re.I):
                raise ValueError("Dialogue must contain speech only. Put stage directions in action beats")
            for ref, name in roster.items():
                named = re.escape(name)
                if re.match(rf"{named}\s*:", text, re.I):
                    raise ValueError("Dialogue must omit speaker labels")
                if ref not in participants or ref == speaker:
                    direct_address = (
                        rf"(?:^|[.!?]\s+)(?:(?:hey|oh|listen),?\s+)?{named}\s*[,!?:.]"
                        rf"|,\s*{named}\s*[.!?]?$|\byou(?:\s+are|['’]re)\s+{named}\b"
                    )
                    if re.search(direct_address, text, re.I):
                        raise ValueError("Direct address must match a listed participant")
            line = f"{roster[speaker]}: {text}"
        else:
            player_name = re.escape(roster[PLAYER_REF])
            player_subject = rf"(?:^|[.!?]\s+)(?:you|your|the player|{player_name})\s+"
            if re.search(player_subject + r"|[\"“”]", text, re.I):
                raise ValueError("Narration must describe the scene or NPC actions")
            if speaker:
                for ref, name in roster.items():
                    prefix = re.match(rf"{re.escape(name)}\s+", text, re.I)
                    if prefix:
                        if ref != speaker:
                            raise ValueError("Action text names a different performer")
                        text = text[prefix.end():].lstrip()
                        break
                line = f"{roster[speaker]} {text}"
            else:
                line = text
        entry = {
            "kind": kind, "speaker": speaker,
            "participants": list(dict.fromkeys(participants)),
            "witnesses": list(dict.fromkeys(witnesses)), "text": text,
            "player_observers": list(dict.fromkeys(player_observers)),
        }
        validated.append(entry)
        if not player_view or visible_to_player(entry):
            lines.append(line)
    return "\n\n".join(lines) or "There’s nothing new to observe.", validated
