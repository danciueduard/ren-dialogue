import json
import asyncio
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from contextlib import closing

from fastapi.testclient import TestClient
import httpx

from app.main import create_app
from app.ren import MAX_REPLY, clean_reply
from app.ren_bridge import HEARTBEAT, REQUEST, RESPONSE, STATE
from app.ren import DialogueError, GameState, Turn
from app.ren_relay import SETTINGS, RemoteDialogue, normalize_address
from app.ren_storage import DatabaseMirror


def wait_for(path):
    for _ in range(60):
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path.name}")


class RenTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.database = self.root / "rin.db"
        self.lua = self.root / "Lua"
        self.payloads = []

    def model(self, request):
        self.payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "Rin: We made it. Stay close.\n*She smiles.*"}})

    def app(self, *, use_mailbox=False):
        return create_app(self.database, self.lua, use_mailbox=use_mailbox,
                          transport=httpx.MockTransport(self.model))

    def turn(self, request_id, **changes):
        return dict({
            "request_id": request_id, "save_id": "world1", "player_name": "Alex",
            "message": "We made it?", "friendship": 55, "romance": 60,
            "romance_enabled": False, "romance_open": False,
        }, **changes)

    def test_single_npc_only_and_current_romance_gate(self):
        with TestClient(self.app()) as api:
            self.assertEqual(api.get("/").json()["character"], "Rin")
            self.assertEqual(api.post("/campaigns/1/characters", json={"name": "Other"}).status_code, 404)
            reply = api.post("/chat", json=self.turn("one"))
            self.assertEqual(reply.json(), {"reply": "We made it. Stay close."})
            prompt = self.payloads[-1]["messages"][0]["content"]
            self.assertIn('You are "Ren"', prompt)
            self.assertIn("Keep it platonic", prompt)
            self.assertEqual(self.payloads[-1]["options"]["num_predict"], 48)
            self.assertEqual(api.post("/chat", json=self.turn("one")).json(), reply.json())
            self.assertEqual(len(self.payloads), 1)
            self.assertEqual(api.post("/chat", json=self.turn("too-long", message="a" * 161)).status_code, 422)
            api.post("/chat", json=self.turn("two", romance_enabled=True, romance_open=True)).raise_for_status()
            self.assertIn("Established romantic affection", self.payloads[-1]["messages"][0]["content"])
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT name FROM characters").fetchall(), [("Ren",)])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO characters(campaign_id,name) VALUES (1,'Other')")

    def test_recent_history_is_bounded_and_isolated_by_survivor(self):
        with TestClient(self.app()) as api:
            for index in range(6):
                api.post("/chat", json=self.turn(f"turn-{index}", message=f"Old message {index}")).raise_for_status()
            first = self.payloads[-1]["messages"]
            self.assertEqual(len(first), 10)
            self.assertNotIn("Old message 0", [m["content"] for m in first])
            self.assertIn("Old message 1", [m["content"] for m in first])
            api.post("/chat", json=self.turn("fresh", save_id="world2")).raise_for_status()
            self.assertEqual(len(self.payloads[-1]["messages"]), 2)

    def test_player_fact_outlives_recent_dialogue_and_backend_restart(self):
        self.model = self.contradicting_model
        with TestClient(self.app()) as api:
            api.post("/chat", json=self.turn("remember", message="I have 99 lemons, remember that!")).raise_for_status()
            for item in ("cans", "pens", "pears", "apples", "keys", "coins", "pencils"):
                api.post("/chat", json=self.turn(f"other-{item}", message=f"I have 2 {item}")).raise_for_status()

        with TestClient(self.app()) as api:
            question = "Noo, I mean, how many lemons do I have?"
            response = api.post("/chat", json=self.turn("recall", message=question)).raise_for_status()
            self.assertEqual(response.json(), {"reply": "99 lemons."})
            self.assertEqual(len(self.payloads), 9)
            messages = self.payloads[-1]["messages"]
            self.assertNotIn("I have 99 lemons, remember that!", [m["content"] for m in messages])
            self.assertIn("99 lemons", messages[0]["content"])
            self.assertNotIn("3 lemons", messages[0]["content"])
            self.assertIn("You have 3 lemons.", [m["content"] for m in messages[1:]])
            api.post("/chat", json=self.turn("different", save_id="world2", message=question)).raise_for_status()
            self.assertNotIn("99 lemons", self.payloads[-1]["messages"][0]["content"])
            api.post("/chat", json=self.turn("correction", message="I have 12 lemons left now.")).raise_for_status()
            response = api.post("/chat", json=self.turn("recall-latest", message=question)).raise_for_status()
            self.assertEqual(response.json(), {"reply": "12 lemons."})
            prompt = self.payloads[-1]["messages"][0]["content"]
            self.assertIn("12 lemons", prompt)
            self.assertNotIn("99 lemons", prompt)

    def contradicting_model(self, request):
        self.payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "You have 3 lemons."}})

    def test_explicit_memory_also_keeps_non_quantity_facts(self):
        with TestClient(self.app()) as api:
            api.post("/chat", json=self.turn("remember", message="Remember that the lake cabin is our fallback.")).raise_for_status()
            for index in range(5):
                api.post("/chat", json=self.turn(f"other-{index}")).raise_for_status()
            api.post("/chat", json=self.turn("recall", message="What's our fallback?")).raise_for_status()
            self.assertIn("the lake cabin is our fallback.", self.payloads[-1]["messages"][0]["content"])

    def test_file_mailbox_replies_once_and_preserves_its_protocol(self):
        payload = "RIN1\nreq-1\nsave-1\n55\n60\n1\n1\nAlex\nÇa va?\n"
        with TestClient(self.app(use_mailbox=True)):
            wait_for(self.lua / HEARTBEAT)
            incoming = self.lua / REQUEST
            response = self.lua / RESPONSE
            incoming.write_text(payload, encoding="utf-8")
            time.sleep(0.25)
            self.assertFalse(response.exists())
            incoming.write_text(payload + "END\n", encoding="utf-8")
            self.assertEqual(wait_for(response).splitlines(),
                             ["RIN1", "req-1", "OK", "We made it. Stay close.", "END"])
        with TestClient(self.app(use_mailbox=True)):
            wait_for(self.lua / HEARTBEAT)
            time.sleep(0.3)
            self.assertEqual(len(self.payloads), 1)
            self.assertEqual(self.payloads[0]["messages"][-1]["content"], "Ça va?")
            old = time.time() - 120
            incoming.write_text(payload.replace("req-1", "stale") + "END\n", encoding="utf-8")
            os.utime(incoming, (old, old))
            time.sleep(0.3)
            self.assertEqual(len(self.payloads), 1)

    def test_reply_display_limits(self):
        self.assertEqual(clean_reply("*frowns* Rin: Ready. Let's go. Leave it.\nExtra"), "Ready. Let's go.")
        self.assertEqual(clean_reply("Ren: Ready."), "Ready.")
        self.assertLessEqual(len(clean_reply("extraordinary " * 40)), MAX_REPLY)
        self.assertLessEqual(len(clean_reply(("ok " * 35)).split()), 24)

    def test_live_status_and_name_arrive_with_and_without_chat(self):
        self.model = lambda request: (
            self.payloads.append(json.loads(request.content))
            or httpx.Response(200, json={"message": {"content": "Liz: We made it."}})
        )
        def saved_status():
            with closing(sqlite3.connect(self.database)) as db:
                row = db.execute("SELECT snapshot FROM rin_game_state WHERE save_id = 'world1'").fetchone()
                return json.loads(row[0]) if row else {}

        with TestClient(self.app(use_mailbox=True)) as api:
            wait_for(self.lua / HEARTBEAT)
            (self.lua / STATE).write_text("RIN1\nworld1\n72\n54\n1\n1\nAlex\nLiz\n2000\nFriend\n5\n6\n0.25\nfollow\n\nEND\n")
            for _ in range(60):
                if saved_status().get("companion_name") == "Liz":
                    break
                time.sleep(0.05)
            self.assertEqual(saved_status()["friendship"], 72)
            self.assertEqual(self.payloads, [])
            (self.lua / REQUEST).write_text(
                "RIN1\nchat-live\nworld1\n16.5\n54\n0\n0\nAlex\nAre you okay?\nLiz\n1000000\nKnown\n5\n6"
                "\n0.75\nwait\ntreatment:999900,possible_crash:1,bullet_wound:1000001\nEND\n"
            )
            self.assertEqual(wait_for(self.lua / RESPONSE).splitlines()[3], "We made it.")
            prompt = self.payloads[-1]["messages"][0]["content"]
            self.assertIn('You are "Liz"', prompt)
            self.assertIn("Keep it platonic", prompt)
            self.assertIn('"friendship": 16.5', prompt)
            self.assertIn('"combat_fatigue": 0.75', prompt)
            self.assertIn("treated the player's injuries", prompt)
            self.assertNotIn("possible car crash", prompt)
            self.assertNotIn("bullet wound", prompt)
            stale = self.turn("older", observed_at=999999, companion_name="Rin", friendship=99)
            self.assertEqual(api.put("/state", json=stale).status_code, 204)
            self.assertEqual(saved_status()["companion_name"], "Liz")
            self.assertEqual(saved_status()["friendship"], 16.5)
            self.assertNotIn("message", saved_status())

    def test_schema_upgrade_backs_up_existing_conversation(self):
        with TestClient(self.app()) as api:
            api.post("/chat", json=self.turn("prior-turn")).raise_for_status()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DROP TABLE rin_game_state")
        with TestClient(self.app()):
            backup = self.database.with_name("rin.before-game-state.db")
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(db.execute("SELECT request_id FROM rin_turns").fetchall(), [("prior-turn",)])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name = 'rin_game_state'").fetchone())
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT request_id FROM rin_turns").fetchall(), [("prior-turn",)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM rin_game_state").fetchone(), (0,))

    def test_legacy_profile_rename_preserves_chat_and_game_name(self):
        snapshot = self.turn("saved", companion_name="Liz", observed_at=1000)
        with TestClient(self.app()) as api:
            api.post("/chat", json=snapshot).raise_for_status()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DROP TRIGGER rin_identity")
            db.execute("UPDATE characters SET name = 'Rin' WHERE id = 1")
            db.execute("""
                CREATE TRIGGER rin_identity BEFORE UPDATE OF name ON characters
                WHEN NEW.name <> 'Rin' BEGIN SELECT RAISE(ABORT, 'The character is Rin'); END
            """)
        with TestClient(self.app()) as api:
            self.assertEqual(api.post("/chat", json=snapshot).json(), {"reply": "We made it. Stay close."})
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT id, name FROM characters").fetchone(), (1, "Ren"))
                self.assertEqual(db.execute("SELECT count(*) FROM rin_turns").fetchone(), (1,))
                self.assertEqual(db.execute("SELECT json_extract(snapshot, '$.companion_name') FROM rin_game_state").fetchone(), ("Liz",))
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE characters SET name = 'Other' WHERE id = 1")
        with closing(sqlite3.connect(self.database.with_name("rin.before-ren.db"))) as db:
            self.assertEqual(db.execute("SELECT id, name FROM characters").fetchone(), (1, "Rin"))

    def test_lan_api_requires_key_for_connection_check_and_dialogue(self):
        app = create_app(self.database, use_mailbox=False, transport=httpx.MockTransport(self.model),
                         api_token="test-key")
        with TestClient(app) as api:
            headers = {"Authorization": "Bearer test-key"}
            self.assertEqual(api.get("/connection").status_code, 401)
            self.assertEqual(api.get("/database").status_code, 401)
            self.assertEqual(api.post("/chat", json=self.turn("unauthorized")).status_code, 401)
            self.assertEqual(api.put("/state", json=self.turn("unauthorized")).status_code, 401)
            self.assertEqual(api.get("/connection", headers=headers).json(), {"character": "Rin", "protocol": 1})
            self.assertEqual(api.post("/chat", headers=headers, json=self.turn("authorized")).status_code, 200)
            snapshot = api.get("/database", headers=headers).raise_for_status()
            self.assertEqual(api.get("/database", headers=headers | {"If-None-Match": snapshot.headers["etag"]}).status_code, 304)

        with TestClient(self.app(use_mailbox=True)) as api:
            self.assertEqual(api.get("/database").status_code, 404)

    def test_connected_relay_copies_committed_turns_without_touching_local_history(self):
        host = create_app(self.database, use_mailbox=False, transport=httpx.MockTransport(self.model),
                          api_token="test-key")
        self.lua.mkdir()
        portable_data = self.root / "client" / "data"
        portable_data.mkdir(parents=True)
        independent_history = portable_data / "rin.db"
        independent_history.write_bytes(b"local history")
        mirror = DatabaseMirror("http://testserver", portable_data / "host-rin.db")

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), headers={
                "Authorization": "Bearer test-key",
            }) as client:
                await mirror.refresh(client)
                first_etag = mirror.etag
                relay = RemoteDialogue(self.lua, client, mirror)
                (self.lua / SETTINGS).write_text("RIN1\nhttp://other-host\nEND\n", encoding="utf-8")
                monitor = asyncio.create_task(relay.monitor())
                try:
                    await relay.sync_state(GameState(**self.turn("unused", companion_name="Liz", observed_at=1000)))
                    self.assertEqual(await relay.respond(Turn(**self.turn("remote", message="Remember the cabin."))),
                                     "We made it. Stay close.")
                    async with asyncio.timeout(4):
                        while mirror.etag == first_etag:
                            await asyncio.sleep(0.1)
                    with closing(sqlite3.connect(mirror.database)) as db:
                        self.assertEqual(db.execute("SELECT request_id FROM rin_turns").fetchall(), [("remote",)])
                        self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
                        saved = db.execute("SELECT snapshot FROM rin_game_state WHERE save_id = 'world1'").fetchone()
                        self.assertEqual(json.loads(saved[0])["companion_name"], "Liz")
                finally:
                    monitor.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await monitor

        with TestClient(host):
            asyncio.run(exercise())
        self.assertEqual(independent_history.read_bytes(), b"local history")

    def test_relay_uses_address_saved_in_game_and_updates_without_restart(self):
        self.lua.mkdir()
        addresses = []
        def backend(request):
            addresses.append(str(request.url))
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            self.assertEqual(json.loads(request.content)["message"], "We made it?")
            return httpx.Response(200, json={"reply": "We did."})

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.MockTransport(backend),
                                         headers={"Authorization": "Bearer test-key"}) as client:
                relay = RemoteDialogue(self.lua, client)
                for address in ("192.168.1.16:8011", "https://ren.example/api/"):
                    (self.lua / SETTINGS).write_text(f"RIN1\n{address}\nEND\n", encoding="utf-8")
                    self.assertEqual(await relay.respond(Turn(**self.turn("relay"))), "We did.")
                self.assertEqual(addresses, ["http://192.168.1.16:8011/chat", "https://ren.example/api/chat"])
        asyncio.run(exercise())
        for invalid in ("file:///test", "http://user:password@server:8011", "http://server:0", "http://server?token=1"):
            with self.assertRaises(DialogueError):
                normalize_address(invalid)
