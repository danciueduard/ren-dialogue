# Ren Dialogue for Stay With Me

## Windows release

Extract `release/RenDialogue-Windows-x64.zip` and launch `Ren Dialogue.exe`
inside the extracted `RenDialogue` folder. Follow `START HERE.txt`. Enable
Stay With Me and the Ren Dialogue add-on in Project Zomboid. The executable
bridges the game and Ollama. The gaming PC can connect to a host by pasting a
connection code. The bridge downloads and runs a private copy of Ollama and the
model under `ai model` on the PC doing inference. An existing Ollama installation
can run separately.

This PC and Host for another PC write to `data/rin.db`. An upgrade copies
`%LOCALAPPDATA%\RinDialogue\rin.db` on first start if the portable database is
missing. Connect to a host downloads `data/host-rin.db` and refreshes it during
the session. The host remains the only writer. The separate client filename
preserves your local conversation when you switch modes. Use the updated
release on both PCs for sync. Stop the bridge before moving its folder.

For developers, install PyInstaller and run `python build_windows.py` to rebuild
the Windows x64 archive. Source-level command-line options follow.

## Laptop backend and gaming PC from source

1. On the laptop, start Ollama, then run `start-ren-lan.cmd`. It listens on
   port **8011** and displays the access key for your PC relay.
2. On the gaming PC, run `start-ren-relay.cmd` from a local copy of the source,
   enter the laptop's access key, and leave the relay open.
   The relay only forwards messages. Ollama and SQLite run on the laptop.
3. In a single-player save with both mods enabled, right-click and choose
   **Ren Dialogue backend...**. Enter the laptop's address, for example
   `http://192.168.1.16:8011`. The add-on saves the address on the gaming PC.
4. Select **Ren Dialogue status** to check the connection. Then stand near your
   companion and choose **Talk to [name] (AI)**. An updated address takes effect without
   restarting the game or relay.

Keep the two computers on the same trusted private network. The laptop's
Windows Firewall must allow inbound TCP 8011 on that network. Use the laptop's
current LAN IPv4 address if it changes. Set `REN_BIND_HOST` before starting the
laptop launcher to bind it to a particular interface address.

## Local engine on the game PC

If you run everything on one computer, start Ollama, run `start-ren.cmd` and
leave the in-game backend field blank.
If the model is missing, run `ollama pull qwen3.5:4b`.

A first launch without a virtual environment needs Python 3.11 or newer. When
moving to another PC, copy the source files and recreate the virtual environment
there. The fork keeps its own `database/rin.db` with one companion profile and a
separate conversation for each survivor. One model call produces each reply. The companion sees
up to four preceding exchanges and six remembered details, and replies in
at most 140 characters. Say “Remember that …” to keep a detail available
past the recent dialogue. The companion also retains simple player-stated counts like
“I have 99 lemons” and uses the latest count when you state it again.

Optional environment variables

| Variable | Default |
| --- | --- |
| `REN_MODEL` | `qwen3.5:4b` |
| `REN_OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` |
| `REN_LUA_DIR` | `%USERPROFILE%\Zomboid\Lua` |
| `REN_WARMUP` | `1` (set to `0` to skip preloading the model) |
| `REN_BIND_HOST` | `0.0.0.0` for the laptop LAN launcher |
| `REN_API_TOKEN` | Optional preconfigured access key for both launchers |

Use `REN_LUA_DIR` for a custom game cache. Existing `RIN_*` variables are
accepted when their `REN_*` equivalents are unset. Run one engine per game cache. The
loopback API listens at `http://127.0.0.1:8011`.

## Steam Workshop add-on

The upload-ready Workshop item is `mod/RenDialogue-Workshop`. For a local
installation, run `install-ren-mod.cmd` or copy the `RinDialogue` folder inside
its `Contents/mods` into `%USERPROFILE%\Zomboid\mods`. To update an existing
local install, run `install-ren-mod.cmd /update` so the old Lua entry point
is replaced. The add-on keeps mod ID `RinDialogue` for existing saves.

1. Enable **Stay With Me** (mod ID `Bridge`) and **Ren Dialogue for Stay With Me**
   on your Build 42 save.
2. In single-player, stand within three tiles of your companion on the same
   floor. Press **F9** or choose **Talk to [name] (AI)** from the right-click
   menu. Type a short message and click **Send** or press **Enter**.
3. To rebind F9, go to **Options → Key Bindings → Ren Dialogue → Open companion chat**.

The add-on targets Build 42.21+ and the supplied Stay With Me 0.6.8 integration.
The chat uses the companion's in-game name and falls back to Ren when no name
is supplied. Stay With Me controls relationship
progression. The bridge reads friendship, romance eligibility and score, days
together, kill count, combat fatigue and current activity. The latest snapshot
per survivor is recorded in SQLite's `rin_game_state` table, including updates
between messages. Chat uses the snapshot taken when you send it.

For recent context, the add-on can report completed companion treatment, a
new bullet wound noticed while the companion is nearby, and possible crashes
flagged by Stay With Me's driving dialogue. These observations can be recalled
for up to 15 minutes.
The first reply may take longer while Ollama loads the model.

## Tests

Run `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.
