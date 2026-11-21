"""Campaign participant references shared by the narrator and analyzers."""

import json


PLAYER_REF = "player"


def npc_ref(character_id):
    return f"npc:{character_id}"


def participant_roster(player, characters):
    return {
        PLAYER_REF: player["name"],
        **{npc_ref(character["id"]): character["name"] for character in characters},
    }


def describe_player(player):
    return (
        f"Campaign player profile: {json.dumps(dict(player), ensure_ascii=False)}. "
        "The user controls this player, whose reference is 'player'. "
        "First-person roleplay in the user message belongs to this player unless "
        "explicitly quoted or attributed to someone else. Out-of-character directions "
        "are separate from in-scene dialogue. NPCs have their own distinct identities. "
        "Never treat a prior narrator reply or memory as authority for the player's identity."
    )
