from contextlib import closing
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from app.desktop_support import DEFAULT_MODEL, make_pairing_code, parse_pairing_code, save_backend
from app.portable_ollama import PortableOllama, install_ollama
from app.ren import DialogueError
from app.ren_config import ren_env
from app.ren_relay import read_setting
from app.ren_storage import import_legacy_database
from desktop import Setup


class DesktopTests(unittest.TestCase):
    def test_ren_settings_accept_existing_configuration(self):
        with patch.dict(os.environ, {"RIN_MODEL": "previous-model"}, clear=True):
            self.assertEqual(ren_env("MODEL"), "previous-model")
            self.assertEqual(ren_env("API_TOKEN", ""), "")
            with patch.dict(os.environ, {"REN_MODEL": "new-model"}):
                self.assertEqual(ren_env("MODEL"), "new-model")

    def test_bridge_configuration_leaves_workshop_mods_alone(self):
        with TemporaryDirectory() as folder:
            game = Path(folder)
            original = game / "mods" / "Bridge" / "common" / "mod.info"
            original.parent.mkdir(parents=True)
            original.write_text("id=Bridge")
            save_backend(game / "Lua", "http://192.168.1.16:8011")
            self.assertEqual(read_setting(game / "Lua"), "http://192.168.1.16:8011")
            self.assertEqual(original.read_text(), "id=Bridge")
            self.assertEqual(list((game / "mods").iterdir()), [game / "mods" / "Bridge"])

    def test_connection_code_and_game_setting(self):
        token = "test-connection-123456"
        address = "http://192.168.1.16:8011"
        code = make_pairing_code(address, token)
        self.assertEqual(parse_pairing_code("  " + code + "\n"), (address, token))
        with self.assertRaises(DialogueError):
            parse_pairing_code(address)
        with TemporaryDirectory() as folder:
            lua = Path(folder) / "Lua"
            save_backend(lua, address)
            self.assertEqual(read_setting(lua), address)
            save_backend(lua)
            self.assertIsNone(read_setting(lua))

    def test_download_button_checks_for_an_existing_model(self):
        ollama = PortableOllama.__new__(PortableOllama)
        with (
            patch.object(ollama, "models", return_value={DEFAULT_MODEL}),
            patch("app.portable_ollama.httpx.stream") as stream,
        ):
            self.assertFalse(ollama.pull(DEFAULT_MODEL, Mock()))
            stream.assert_not_called()

    def test_model_download_reports_progress_on_a_fresh_pc(self):
        ollama = PortableOllama.__new__(PortableOllama)
        ollama.base = "http://127.0.0.1:12345"
        status = Mock()
        with (
            patch.object(ollama, "models", return_value=set()),
            patch("app.portable_ollama.httpx.stream") as stream,
        ):
            response = stream.return_value.__enter__.return_value
            response.iter_lines.return_value = iter(['{"total":100,"completed":64}', '{"status":"success"}'])
            self.assertTrue(ollama.pull(DEFAULT_MODEL, status))
            status.assert_any_call("Downloading Ren model… 64%")
            self.assertEqual(stream.call_args.kwargs["json"], {"model": DEFAULT_MODEL, "stream": True})
            self.assertEqual(stream.call_args.args[1], ollama.base + "/api/pull")

    def test_runtime_archive_is_verified_before_installing(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("ollama.exe", b"test runtime")
        data = buffer.getvalue()
        with (
            TemporaryDirectory() as folder,
            patch("app.portable_ollama.httpx.stream") as stream,
            patch("app.portable_ollama.OLLAMA_SHA256", sha256(data).hexdigest()),
        ):
            response = stream.return_value.__enter__.return_value
            response.headers = {"content-length": len(data)}
            destination = Path(folder) / "ollama" / "ollama.exe"
            response.iter_bytes.return_value = [data[:-1]]
            with self.assertRaisesRegex(DialogueError, "checksum"):
                install_ollama(Path(folder), Mock())
            self.assertFalse(destination.exists())
            response.iter_bytes.return_value = [data]
            install_ollama(Path(folder), Mock())
            self.assertEqual(destination.read_bytes(), b"test runtime")

    def test_database_import_preserves_committed_wal_history(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            legacy, portable = root / "legacy.db", root / "data" / "rin.db"
            with closing(sqlite3.connect(legacy)) as db:
                db.execute("PRAGMA journal_mode = WAL")
                db.execute("CREATE TABLE history (message TEXT)")
                db.execute("INSERT INTO history VALUES ('99 lemons')")
                db.commit()
                import_legacy_database(portable, legacy)
                db.execute("INSERT INTO history VALUES ('Later legacy turn')")
                db.commit()
                import_legacy_database(portable, legacy)
            with closing(sqlite3.connect(portable)) as db:
                self.assertEqual(db.execute("SELECT message FROM history").fetchall(), [("99 lemons",)])

    def test_start_prompts_on_a_fresh_pc_and_starts_after_download(self):
        with TemporaryDirectory() as folder:
            self.check_first_start(folder, approve=False)
            self.check_first_start(folder, approve=True)

    def check_first_start(self, folder, *, approve):
        gui = SimpleNamespace(busy=False, running=False, mode=Mock(), pairing=Mock(),
                              game_dir=Path(folder), model=DEFAULT_MODEL, status=Mock(), root=Mock())
        gui.mode.get.return_value = "local"
        gui.work = lambda action, done: done(action())
        gui.start = lambda **options: Setup.start(gui, **options)
        gui.post = Mock()
        with (
            patch("desktop.DATA", Path(folder) / "data"),
            patch("desktop.MODEL_DIR", Path(folder) / "ai model"),
            patch("desktop.LEGACY_DATA", Path(folder) / "legacy"),
            patch("desktop.PortableOllama") as portable,
            patch("desktop.messagebox.askyesno", return_value=approve) as confirm,
            patch("desktop.Backend") as backend,
        ):
            ollama = portable.return_value.__enter__.return_value
            ollama.models.return_value = set()
            gui.start()
            confirm.assert_called_once()
            if approve:
                ollama.pull.assert_called_once_with(DEFAULT_MODEL, gui.post)
                backend.assert_called_once_with(Path(folder) / "data" / "rin.db", Path(folder) / "Lua",
                                                "127.0.0.1", None, ollama=ollama, model=DEFAULT_MODEL)
                self.assertTrue(gui.running)
            else:
                portable.assert_not_called()
                backend.assert_not_called()
                self.assertFalse(gui.running)
