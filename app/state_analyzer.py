# state_analyzer.py
import json
import httpx

from app.identity import describe_player
from app.injuries import validate_physical_change

ALLOWED_FIELDS = {
    "location",
    "physical_condition",
    "emotional_state",
    "appearance",
    "clothing",
    "possessions",
    "current_goals",
}


async def analyze_state_changes(
    user_message: str,
    assistant_reply: str,
    character_states: list,
    ollama_url: str,
    model: str,
    player=None,
    beats=(),
):
    """
    Analyze a roleplay exchange and return proposed character
    state changes.

    This function does not modify the database.
    """

    # Convert database records into ordinary dictionaries
    states = [dict(state) for state in character_states]

    # Build a list of characters the analyzer is allowed to modify
    character_names = [state["name"] for state in states]

    if not character_names:
        return []

    # Prepare current state information
    current_states = json.dumps(
        states,
        ensure_ascii=False,
        indent=2,
    )

    # Build analyzer instructions
    system_prompt = f"""
{describe_player(player) if player else ""}
You are a careful state-change analyzer for a persistent
roleplay simulation engine.

Your job is to identify meaningful changes to established
character states based on the latest roleplay exchange.

CURRENT CHARACTER STATES:
{current_states}

CHARACTERS YOU ARE ALLOWED TO UPDATE:
{json.dumps(character_names, ensure_ascii=False)}

ALLOWED STATE FIELDS:
{json.dumps(sorted(ALLOWED_FIELDS))}

RULES:

1. Only propose changes supported by the user's message
   or the narrator's response.

2. Use the narrator's response as evidence of what actually
   happened in the scene.

3. Do not invent events, actions, possessions, injuries,
   emotions, or changes.

4. Only propose a change when the new information differs
   meaningfully from the currently stored value.

5. Use the exact character names provided above.

6. Only use the allowed state fields.

7. Do not propose changes for characters who were not
   involved in the events.

8. Be conservative. If an event is ambiguous, leave it out.

9. For old_value, reproduce the currently stored value.

10. For proposed_value, provide the complete new value
    that should replace the old value.

11. Keep reasons short and explain what happened.

12. Return valid JSON only. Do not include Markdown,
    explanations, or text outside the JSON object.

13. For physical_condition, include injury_status (none, minor, serious, or critical),
    source ("user" or the zero-based index of a narrator beat), and evidence
    (a short exact quotation from that source). Require a real injury, symptom,
    or observable recovery affecting that character. Exclude idioms, jokes,
    hypotheticals, emotional pain, and dialogue-only claims. For user evidence,
    use a sentence naming the NPC. Use critical only for signs of immediate
    danger to life. Worsening requires a new observed sign or consequence.
    A critical NPC stays critical until the player resolves their outcome.
    A deceased NPC's physical condition is fixed. Never propose death.

REQUIRED JSON STRUCTURE:

{{
    "proposals": [
        {{
            "character_name": "Isabella",
            "field_name": "physical_condition",
            "old_value": "",
            "proposed_value": "A bruised wrist from the fall.",
            "injury_status": "minor",
            "source": 0,
            "evidence": "a bruise spreads across her wrist",
            "reason": "Visible bruising after the fall."
        }}
    ]
}}

If no meaningful changes occurred, return:

{{
    "proposals": []
}}
"""

    # Build the analysis request
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

ATTRIBUTED NARRATOR BEATS (indexed from zero):
{json.dumps(list(beats), ensure_ascii=False)}

Identify any supported character state changes.
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

    # Ask Ollama to analyze the exchange
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            ollama_url,
            json=payload,
        )

    response.raise_for_status()

    result = response.json()
    content = result["message"]["content"]

    # Parse the returned JSON
    analysis = json.loads(content)

    if not isinstance(analysis, dict):
        raise ValueError("Analyzer returned an invalid JSON object.")

    raw_proposals = analysis.get("proposals", [])

    if not isinstance(raw_proposals, list):
        raise ValueError("Analyzer proposals must be a list.")

    # Validate the proposed changes
    valid_names = {name.casefold(): name for name in character_names}

    current_state_lookup = {state["name"].casefold(): state for state in states}

    validated_proposals = []

    for proposal in raw_proposals:

        if not isinstance(proposal, dict):
            continue

        character_name = proposal.get("character_name")
        field_name = proposal.get("field_name")
        proposed_value = proposal.get("proposed_value")

        if not isinstance(character_name, str):
            continue

        if not isinstance(field_name, str):
            continue

        if not isinstance(proposed_value, str):
            continue

        character_key = character_name.casefold()

        if character_key not in valid_names:
            continue

        if field_name not in ALLOWED_FIELDS:
            continue

        proposed_value = proposed_value.strip()

        if not proposed_value:
            continue

        character_name = valid_names[character_key]
        current_state = current_state_lookup[character_key]

        old_value = current_state.get(field_name) or ""

        reason = proposal.get("reason", "")

        if not isinstance(reason, str):
            reason = ""

        validated = {
            "character_name": character_name,
            "field_name": field_name,
            "old_value": old_value,
            "proposed_value": proposed_value,
            "reason": reason.strip(),
        }
        if field_name == "physical_condition":
            physical = validate_physical_change(
                {**proposal, "proposed_value": proposed_value}, current_state,
                user_message, beats, character_names,
            )
            if physical is None:
                continue
            validated.update(physical)
        if (old_value.strip().casefold() == proposed_value.casefold()
                and validated.get("injury_status") == validated.get("old_injury_status")):
            continue
        validated_proposals.append(validated)

    return validated_proposals
