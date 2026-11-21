# event_analyzer.py
import json

import httpx

from app.identity import describe_player
from app.continuity import compact

ALLOWED_CERTAINTIES = {"confirmed", "uncertain"}


async def analyze_world_events(
    user_message: str,
    assistant_reply: str,
    existing_events: list,
    ollama_url: str,
    model: str,
    player=None,
):
    """
    Analyze a roleplay exchange and identify meaningful
    objective world events.

    This function does not modify the database.
    It returns validated event proposals for review.
    """

    events = [dict(event) for event in existing_events]

    current_events = compact(events)

    system_prompt = f"""
{describe_player(player) if player else ""}
You are a careful world-event analyzer for a persistent
roleplay simulation engine.

Your job is to identify meaningful events established
by the latest roleplay exchange.

EXISTING WORLD EVENTS:
{current_events}

CORE PRINCIPLE:

Record what the evidence establishes.
Never turn speculation, interpretation, or suspicion
into objective world facts.

IMPORTANT DISTINCTIONS:

- An observation is something directly perceived or
  explicitly established in the scene.
- An interpretation is what a character thinks an
  observation might mean.
- A suspicion is a possibility that has not been proven.
- An objective event describes what actually happened
  according to the available narrative evidence.

RULES:

1. Only identify meaningful events supported by the
   user's message or the narrator's response.

2. Treat the narrator's response as evidence of what
   happened, while carefully separating established
   events from speculative narration.

3. Never invent people, participants, causes, motives,
   intentions, or actions.

4. A sound does not prove that a person caused it.

5. An empty room or alley does not prove that someone
   is hiding nearby.

6. A character's suspicion that someone is watching,
   following, threatening, or manipulating them does
   not establish that this is happening.

7. If a character sees an unidentified person, you may
   record that an unidentified person was seen.
   Do not invent their identity or intentions.

8. If an event is directly established, use certainty
   "confirmed".

9. Use certainty "uncertain" only when the event itself
   has some narrative evidence but its occurrence remains
   genuinely unclear.

10. If only the cause, identity, motive, or meaning is
    uncertain, describe the established observation
    without inventing an explanation.

11. Do not record ordinary dialogue, routine movements,
    thoughts, feelings, or insignificant actions.

12. Do not duplicate existing world events. Consider
    whether a proposed event describes something that
    has already been recorded, even when the wording
    differs.

13. Keep descriptions concise, factual, and neutral.

14. Use participant names exactly as established in
    the exchange. Only include people whose participation
    in the event is supported by evidence.

15. Do not use "unknown person" as a participant unless
    an unidentified person was actually observed or
    explicitly established as participating.

16. Location should describe where the event happened.
    Leave it empty when unknown.

17. occurred_at should describe the scene's time when
    known. Otherwise leave it empty.

18. Return valid JSON only. Do not include Markdown,
    explanations, or text outside the JSON object.

REQUIRED JSON STRUCTURE:

{{
    "events": [
        {{
            "event_type": "general",
            "description": "Isabella heard a metallic sound in the alley.",
            "location": "Narrow alley behind the café",
            "participants": ["Isabella"],
            "occurred_at": "Late afternoon",
            "certainty": "confirmed"
        }}
    ]
}}

If no meaningful new events occurred, return:

{{
    "events": []
}}
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": f"""
LATEST USER MESSAGE:
{user_message}

NARRATOR'S RESPONSE:
{assistant_reply}

Identify meaningful new objective world events.
""",
        },
    ]

    payload = {
        "model": model,
        "think": False,
        "stream": False,
        "format": "json",
        "messages": messages,
        "options": {
            "num_predict": 700,
            "temperature": 0.1,
        },
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            ollama_url,
            json=payload,
        )

    response.raise_for_status()

    result = response.json()
    content = result["message"]["content"]

    analysis = json.loads(content)

    if not isinstance(analysis, dict):
        raise ValueError("Event analyzer returned an invalid JSON object.")

    raw_events = analysis.get("events", [])

    if not isinstance(raw_events, list):
        raise ValueError("Analyzer events must be a list.")

    validated_events = []

    existing_descriptions = {
        (event.get("description") or "").strip().casefold() for event in events
    }

    for event in raw_events:

        if not isinstance(event, dict):
            continue

        event_type = event.get("event_type", "general")
        description = event.get("description")
        location = event.get("location", "")
        participants = event.get("participants", [])
        occurred_at = event.get("occurred_at", "")
        certainty = event.get("certainty", "confirmed")

        if not isinstance(description, str):
            continue

        description = description.strip()

        if not description:
            continue

        if description.casefold() in existing_descriptions:
            continue

        if not isinstance(event_type, str):
            event_type = "general"

        event_type = event_type.strip() or "general"

        if not isinstance(location, str):
            location = ""

        if not isinstance(occurred_at, str):
            occurred_at = ""

        if not isinstance(participants, list):
            participants = []

        participants = [
            name.strip()
            for name in participants
            if isinstance(name, str) and name.strip()
        ]

        if certainty not in ALLOWED_CERTAINTIES:
            certainty = "uncertain"

        validated_events.append(
            {
                "event_type": event_type,
                "description": description,
                "location": location.strip(),
                "participants": participants,
                "occurred_at": occurred_at.strip(),
                "certainty": certainty,
            }
        )

        existing_descriptions.add(description.casefold())

    return validated_events
