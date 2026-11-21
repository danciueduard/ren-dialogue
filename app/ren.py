"""Single-character, one-request dialogue for Stay With Me."""
import asyncio
import json
import re
import sqlite3
from contextlib import closing
from itertools import chain
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from database.setup import initialize_base_database


MAX_MESSAGE = 160
MAX_REPLY = 140
HISTORY_TURNS = 4
MEMORY_LIMIT = 6
MODEL_CONTEXT = 3072
DEFAULT_COMPANION_NAME = "Ren"

REMEMBER_PREFIX = re.compile(r"^(?:please\s+)?remember(?:\s+that\b)?[\s,:]+", re.I)
REMEMBER_SUFFIX = re.compile(r"[,;.!?]\s*(?:please\s+)?remember\s+(?:that|this)[.!?]*$", re.I)
ITEM_NAME = r"[^\W\d_]+(?:[ -][^\W\d_]+)*"
QUANTITY_STATEMENT = re.compile(
    rf"^(?:I\s+(?:(?:now|only)\s+)?have|I['’]ve\s+got)\s+"
    rf"(?P<count>[0-9]+)\s+(?P<item>{ITEM_NAME}?)(?:\s+left)?(?:\s+now)?[.!]*$",
    re.I,
)
QUANTITY_QUESTION = re.compile(rf"\bhow many (?P<item>{ITEM_NAME}) do I (?:still )?have[?.!]*$", re.I)

REN_PROFILE = (
    "A practical survivor in Knox County. Observant, resourceful and quietly funny. "
    "She takes danger seriously, shows care through practical help, and dislikes grand speeches."
)


EVENT_TEXT = {
    "treatment": "You recently treated the player's injuries.",
    "bullet_wound": "You noticed a new bullet wound on the player.",
    "possible_crash": "You recently reacted to a possible car crash with the player.",
}
EVENT_WINDOW_MS = 15 * 60 * 1000


class GameEvent(BaseModel):
    kind: Literal["treatment", "bullet_wound", "possible_crash"]
    at: int = Field(gt=0, le=10**14)


class GameState(BaseModel):
    save_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,72}$")
    player_name: str = Field(min_length=1, max_length=60)
    friendship: float = Field(ge=-100, le=100)
    romance: float = Field(ge=0, le=100)
    romance_enabled: bool
    romance_open: bool
    companion_name: str | None = Field(default=None, min_length=1, max_length=60)
    observed_at: int | None = Field(default=None, gt=0, le=10**14)
    relationship_tier: Literal["Cold", "Known", "Pal", "Friend", "Close"] | None = None
    days_together: int | None = Field(default=None, ge=0, le=1000000)
    companion_kills: int | None = Field(default=None, ge=0, le=1000000000)
    combat_fatigue: float | None = Field(default=None, ge=0, le=1)
    activity: str | None = Field(default=None, min_length=1, max_length=40)
    events: list[GameEvent] = Field(default_factory=list, max_length=6)

    @field_validator("player_name", "companion_name", "activity")
    @classmethod
    def require_single_line(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("Provide a nonempty line of text")
        return value


class Turn(GameState):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,72}$")
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)

    @field_validator("message")
    @classmethod
    def require_message(cls, value):
        return cls.require_single_line(value)


class DialogueError(RuntimeError):
    pass


def initialize_ren_database(path):
    if not path.exists():
        initialize_base_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'rin_turns'").fetchone()
        if exists:
            has_state = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'rin_game_state'").fetchone()
            legacy_name = db.execute("SELECT name FROM characters WHERE id = 1").fetchone() == ("Rin",)
            if not has_state or legacy_name:
                backup_before_upgrade(path, "game-state" if not has_state else "ren")
            if not has_state:
                create_game_state_table(db)
            if legacy_name:
                db.execute("DROP TRIGGER IF EXISTS rin_identity")
                db.execute("UPDATE characters SET name = ? WHERE id = 1", (DEFAULT_COMPANION_NAME,))
                create_identity_trigger(db)
            return
        if db.execute("SELECT 1 FROM campaigns LIMIT 1").fetchone():
            raise RuntimeError("Ren Dialogue requires a separate database. This database already has campaigns.")
        db.execute("INSERT INTO campaigns(id, name) VALUES (1, 'Stay With Me')")
        db.execute("INSERT INTO characters(id, campaign_id, name, personality) VALUES (1, 1, ?, ?)",
                   (DEFAULT_COMPANION_NAME, REN_PROFILE))
        db.execute("""
            CREATE TABLE rin_turns (
                request_id TEXT PRIMARY KEY, save_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                user_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                reply_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX idx_rin_turns_save ON rin_turns(save_id, user_message_id DESC)")
        create_game_state_table(db)
        db.execute("""
            CREATE TRIGGER rin_single_character BEFORE INSERT ON characters
            BEGIN SELECT RAISE(ABORT, 'Only one companion profile is supported'); END
        """)
        create_identity_trigger(db)
        db.execute("""
            CREATE TRIGGER rin_required BEFORE DELETE ON characters
            BEGIN SELECT RAISE(ABORT, 'The companion profile is required'); END
        """)


def backup_before_upgrade(path, change):
    from app.ren_bridge import atomic_bytes
    from app.ren_storage import database_snapshot

    backup = path.with_name(f"{path.stem}.before-{change}.db")
    if not backup.exists():
        atomic_bytes(backup, database_snapshot(path))


def create_identity_trigger(db):
    db.execute("""
        CREATE TRIGGER rin_identity BEFORE UPDATE OF name, campaign_id ON characters
        WHEN NEW.name <> OLD.name OR NEW.campaign_id <> OLD.campaign_id
        BEGIN SELECT RAISE(ABORT, 'The companion profile identity is fixed'); END
    """)


def create_game_state_table(db):
    db.execute("""
        CREATE TABLE rin_game_state (
            save_id TEXT PRIMARY KEY,
            observed_at INTEGER NOT NULL,
            snapshot TEXT NOT NULL
        )
    """)


def relationship_voice(turn):
    if turn.friendship < 0:
        friendship = "Guarded and blunt. Keep emotional distance."
    elif turn.friendship < 20:
        friendship = "Polite, practical and a little reserved."
    elif turn.friendship < 45:
        friendship = "At ease, with occasional dry humor."
    elif turn.friendship < 75:
        friendship = "Warm and familiar, as with a trusted friend."
    else:
        friendship = "Close and candid. Show trust without making every reply sentimental."
    if not turn.romance_enabled or not turn.romance_open or turn.romance == 0:
        romance = "Keep it platonic, even if an earlier turn was romantic."
    elif turn.romance < 25:
        romance = "Affection may be tentative; follow the situation and do not assume a relationship."
    elif turn.romance < 50:
        romance = "Allow occasional understated flirtation if the moment calls for it."
    else:
        romance = "Established romantic affection can show through small, natural remarks."
    return friendship + " " + romance


def clean_reply(text, companion_name=DEFAULT_COMPANION_NAME):
    if not isinstance(text, str):
        return ""
    # Remove stage directions and keep a single short spoken bubble.
    text = re.sub(r"\*[^*\n]*\*|\[[^\]\n]*\]|<[^>\n]*>", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = re.sub(rf"^(?:{re.escape(companion_name)}|Ren|Rin)\s*:\s*", "", lines[0], flags=re.I).strip("\"“” ")
    sentences = re.split(r"(?<=[.!?])\s+", line)[:2]
    words = " ".join(sentences).split()
    result = " ".join(words[:24])
    if len(result) > MAX_REPLY:
        result = result[:MAX_REPLY - 1]
        if " " in result:
            result = result.rsplit(" ", 1)[0]
        return result.rstrip(",;:-") + "…"
    if len(words) > 24:
        return result.rstrip(".,;:-") + "…"
    return result


def player_memories(current_message, prior_messages):
    question = QUANTITY_QUESTION.search(current_message)
    wanted_item = question["item"].casefold() if question else None
    memories = []
    seen = set()
    recalled_quantity = None
    for message in chain((current_message,), prior_messages):
        without_prefix = REMEMBER_PREFIX.sub("", message)
        fact = REMEMBER_SUFFIX.sub("", without_prefix).strip()
        quantity = QUANTITY_STATEMENT.fullmatch(fact)
        if quantity:
            item = quantity["item"].casefold()
            key = ("quantity", item)
            fact = f"The player says they have {quantity['count']} {quantity['item']}."
        elif fact != message and fact:
            item = None
            key = ("statement", fact.rstrip(".!?").casefold())
        else:
            continue
        if key in seen:
            continue
        if wanted_item and item == wanted_item:
            recalled_quantity = f"{quantity['count']} {quantity['item']}."
            memories.insert(0, fact)
            wanted_item = None
        elif len(memories) < MEMORY_LIMIT:
            memories.append(fact)
        else:
            continue
        seen.add(key)
        if len(memories) >= MEMORY_LIMIT and wanted_item is None:
            break
    return memories[:MEMORY_LIMIT], recalled_quantity


class RenDialogue:
    def __init__(self, database: Path, ollama: httpx.AsyncClient, url: str, model: str):
        self.database = database
        self.ollama = ollama
        self.url = url
        self.model = model
        self.lock = asyncio.Lock()

    async def sync_state(self, state: GameState):
        if state.observed_at is None or state.companion_name is None:
            raise DialogueError("The game status is missing its name or timestamp.")
        payload = state.model_dump_json(exclude={"request_id", "message"}, exclude_none=True)
        try:
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("""
                    INSERT INTO rin_game_state (save_id, observed_at, snapshot) VALUES (?, ?, ?)
                    ON CONFLICT(save_id) DO UPDATE SET
                        observed_at = excluded.observed_at, snapshot = excluded.snapshot
                    WHERE excluded.observed_at > rin_game_state.observed_at
                """, (state.save_id, state.observed_at, payload))
        except sqlite3.Error as error:
            raise DialogueError("The bridge couldn't update the saved game status.") from error

    async def respond(self, turn: Turn):
        async with self.lock:
            with closing(sqlite3.connect(self.database)) as db:
                old = db.execute("""
                    SELECT t.save_id, t.player_name, u.content, a.content
                    FROM rin_turns t JOIN messages u ON u.id = t.user_message_id
                    JOIN messages a ON a.id = t.reply_message_id WHERE t.request_id = ?
                """, (turn.request_id,)).fetchone()
                if old:
                    if old[:3] != (turn.save_id, turn.player_name, turn.message):
                        raise DialogueError("That dialogue request ID is already in use.")
                    return old[3]
                personality = db.execute("SELECT personality FROM characters WHERE id = 1").fetchone()[0]
                recent = db.execute("""
                    SELECT u.content, a.content FROM rin_turns t
                    JOIN messages u ON u.id = t.user_message_id
                    JOIN messages a ON a.id = t.reply_message_id WHERE t.save_id = ?
                    ORDER BY t.user_message_id DESC LIMIT ?
                """, (turn.save_id, HISTORY_TURNS)).fetchall()
                with closing(db.execute("""
                    SELECT u.content FROM rin_turns t
                    JOIN messages u ON u.id = t.user_message_id
                    WHERE t.save_id = ? AND (
                        lower(u.content) LIKE '%remember%' OR lower(u.content) LIKE 'i %have %'
                        OR lower(u.content) LIKE 'i''ve got %' OR lower(u.content) LIKE 'i’ve got %'
                    )
                    ORDER BY t.user_message_id DESC
                """, (turn.save_id,))) as candidates:
                    memories, recalled_quantity = player_memories(turn.message, (row[0] for row in candidates))

            if turn.observed_at is not None and turn.companion_name is not None:
                await self.sync_state(turn)
            name = json.dumps(turn.player_name, ensure_ascii=False)
            companion_name = turn.companion_name or DEFAULT_COMPANION_NAME
            speaker = json.dumps(companion_name, ensure_ascii=False)
            prompt = (
                f"You are {speaker}, speaking to player {name} in Project Zomboid. {personality} "
                f"{relationship_voice(turn)} Reply in the player's language. "
                "Only give your spoken words. Use one short line, normally one sentence and at most 24 words. "
                "No names as labels, narration, actions, or stage directions. Stay in character. "
                "Answer what was said. Never invent actions or speak for the player. "
                "The game determines real actions, relationship progression, and outcomes. "
                "For recall, use the player's latest statements and remembered facts. "
                "Keep exact quantities and other details as stated. Earlier replies may be mistaken. "
                "If the player asks for a detail you do not know, say you don't remember instead of guessing."
            )
            if turn.companion_name:
                live_status = {
                    "friendship": turn.friendship, "romance": turn.romance,
                    "romance_enabled": turn.romance_enabled, "romance_open": turn.romance_open,
                    "relationship_tier": turn.relationship_tier, "days_together": turn.days_together,
                    "kills": turn.companion_kills, "combat_fatigue": turn.combat_fatigue,
                    "current_activity": turn.activity,
                }
                prompt += "\nCurrent Stay With Me status (use for tone and context, keep scores out of dialogue unless asked): " + json.dumps(
                    {key: value for key, value in live_status.items() if value is not None}
                )
            if turn.observed_at:
                events = dict.fromkeys(
                    EVENT_TEXT[event.kind] for event in turn.events
                    if 0 <= turn.observed_at - event.at <= EVENT_WINDOW_MS
                )
                if events:
                    prompt += (
                        "\nRecent events observed in-game: " + json.dumps(list(events))
                        + "\nRecall these events using only details the game or player provided. "
                        "If the circumstances are unknown, answer briefly and leave them unspecified."
                    )
            if memories:
                prompt += "\nRemembered facts from this player: " + json.dumps(memories, ensure_ascii=False)
            if recalled_quantity:
                prompt += "\nAnswer this count question with just the number and item: " + json.dumps(recalled_quantity)
            messages = [{"role": "system", "content": prompt}]
            for user, reply in reversed(recent):
                messages.extend(({"role": "user", "content": user}, {"role": "assistant", "content": reply}))
            messages.append({"role": "user", "content": turn.message})

            try:
                response = await self.ollama.post(self.url, json={
                    "model": self.model, "messages": messages, "stream": False, "think": False,
                    "keep_alive": "10m",
                    "options": {"num_ctx": MODEL_CONTEXT, "num_predict": 48, "temperature": 0.55, "stop": ["\n"]},
                })
                response.raise_for_status()
                reply = clean_reply(response.json()["message"]["content"], companion_name)
            except httpx.TimeoutException as error:
                raise DialogueError(f"{companion_name}'s reply timed out. Try again.") from error
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                raise DialogueError("The companion model is unavailable. Check Ollama and the configured model.") from error
            if recalled_quantity:
                # Keep a direct count answer grounded even when the model contradicts the saved statement.
                expected = clean_reply(recalled_quantity)
                if reply.rstrip(".").casefold() != expected.rstrip(".").casefold():
                    reply = expected
            if not reply:
                raise DialogueError(f"{companion_name} didn't produce a spoken reply. Try again.")

            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("PRAGMA foreign_keys = ON")
                user_id = db.execute(
                    "INSERT INTO messages(campaign_id, role, content) VALUES (1, 'user', ?)", (turn.message,)
                ).lastrowid
                reply_id = db.execute(
                    "INSERT INTO messages(campaign_id, role, content) VALUES (1, 'assistant', ?)", (reply,)
                ).lastrowid
                db.execute("INSERT INTO rin_turns VALUES (?, ?, ?, ?, ?)",
                           (turn.request_id, turn.save_id, turn.player_name, user_id, reply_id))
            return reply
