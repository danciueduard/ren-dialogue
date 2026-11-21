# Ren Dialogue for Stay With Me

**Alpha · Early testing.** Ren Dialogue lets you type messages to your Stay With Me companion in Project Zomboid. You may run into bugs, slow replies, or incorrect AI replies. The source is available here, and anyone can help by testing, reporting bugs, improving the instructions, or contributing code.

## Quick start

You need **Windows 64-bit**, **Project Zomboid Build 42.21 or newer**, and a **single-player** game. The PC running the AI needs internet access for the first download, which is about **5 GB**. The app downloads everything it needs.

## Minimum requirements

- at least 6GB of VRAM for the server (tested on RTX 4050 laptop)
- over 12 GB of VRAM if you intend to run it on the same computer you are playing PZ 

Normal mode: Can be played with the server running. At least 8GB of VRAM (minimum RTX 3070Ti recommended)
!!!! Viewpoint: Run the server on a different computer connected to the same network as the gaming PC. The AI model will eat at least 3GB of VRAM and both apps will race for the VRAM generating huge fps drops. 

### 1. Download and start the bridge

1. On this GitHub page, click **Code** above the file list, then **Download ZIP**.
2. Open your Windows **Downloads** folder. Right-click the ZIP you downloaded, choose **Extract All**, then click **Extract**.
3. Open the extracted project folder. If you see another folder called `ren-dialogue-main` inside it, open that too. You should now see a folder called `release`.
4. Open `release`. Right-click `RenDialogue-Windows-x64.zip`, choose **Extract All**, and click **Extract** again.
5. In the extracted files, open the `RenDialogue` folder and you should find **Ren Dialogue.exe**, **data** and **ai model** . Keep the `data` and `ai model` folders beside it. You are free to move that folder wherever you want. That folder will store your interactions with Ren and the events happened.

### 2. Install and enable the two mods on your gaming PC

1. Close Project Zomboid if open.
2. Open the Stay With Me page in Steam, click **Subscribe**, and wait for Steam to finish downloading it. [Stay With Me on Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3800989007) and [Ren Dialogue for Stay With Me (Alpha Testing)](https://steamcommunity.com/sharedfiles/filedetails/?id=3843689875)
3. Start Project Zomboid. From the main menu, open **Mods** and enable **Stay With Me** and **Ren Dialogue for Stay With Me**. If you're using an existing save, choose **Load**, select your save, then go to **More… → Choose Mods…** and enable both mods for that save. Don't load the save yet. RECOMMENDED: backup the save.

### 3. Start Ren Dialogue Bridge.

Pick the setup that matches your computers.

**One PC for the game and the AI**

1. Open **Ren Dialogue.exe**.
2. Select **This PC**, then click **Start Ren**.
3. If asked to download the model, click **Yes**. Wait for the app to say **The bridge is running**. Keep the app open while you play.

**Two PCs on the same home network**

1. Pick the PC that will run the AI. This is the **host**. Copy the extracted `RenDialogue` folder to it so both PCs use the same bridge version.
2. On the host, open **Ren Dialogue.exe**, select **Host for another PC**, then click **Start hosting**. Approve the model download and the private-network firewall prompt if shown.
3. When the app says **Hosting on this PC**, click **Copy connection code**. Send that code to your gaming PC.
4. On the gaming PC, open **Ren Dialogue.exe**, select **Connect to a host**, paste the code, and click **Connect**. Wait for the app to say **Connected**.
5. Leave the app open on both PCs. Share the code only with people you trust. A connected PC receives a copy of the host's conversation database.

### 4. Talk to your companion

1. Start or load a **single-player** game with both mods enabled.
2. Stand within **three tiles** of your companion, on the **same floor**.
3. Press **F9**, or right-click the ground and choose **Talk to [name] (AI)**.
4. Type a short message and press **Enter** or click **Send**. The reply appears in your companion's speech bubble. The first reply can take longer while the AI loads.

To change **F9**, go to **Options → Key Bindings → Ren Dialogue → Open companion chat**.

## If something goes wrong

- **The chat option is missing.** Check that both mods are enabled for the save you loaded. Stand beside your companion and try the right-click menu.
- **The game cannot reach the bridge.** Right-click in the game and select **Ren Dialogue status**. Make sure the app is open. With two PCs, check that both are on the same private network and the host is still hosting.
- **You use a custom Zomboid folder.** Stop the bridge, click **Game folder…**, and select your Zomboid folder, the one that contains your `Lua` and `mods` folders. Then start the bridge again. The default is `%USERPROFILE%\Zomboid`.
- **Something else broke.** Open the repository's **Issues** tab. Include your Zomboid build, whether you used one or two PCs, the steps you took, and the exact error you saw. Leave out your connection code and conversation database.

### Updating an existing installation

1. Close Project Zomboid and stop the bridge.
2. Open the extracted project folder in File Explorer. Click the address bar at the top, type `cmd`, and press **Enter**.
3. In the window that opens, type the command below and press **Enter**.

```bat
install-ren-mod.cmd /update
```

When you replace the bridge, keep your `data` and `ai model` folders beside the new **Ren Dialogue.exe**. If you use two PCs, update both bridge copies.

## What Ren Dialogue remembers

The bridge uses your companion's in-game name, current friendship and romance scores, and selected recent events. **Stay With Me controls gameplay and relationship progress.** Compliments and flirting through AI chat leave the mod's scores as they were.

The bridge keeps a short recent chat history. You can also say “Remember that I have 99 lemons” to bring that detail back in a later chat. Your companion may recall recent treatment, newly noticed bullet wounds, or possible crashes flagged by Stay With Me.

The PC running the AI saves chats to `data/rin.db`. In two-PC mode, the gaming PC keeps a synced copy at `data/host-rin.db`. On first start, the bridge can copy a database from the old `%LOCALAPPDATA%\RinDialogue` folder if the portable database is missing.

## Contributing

Testing, bug reports, documentation edits, and code changes are all welcome. Use **Issues** for bugs or ideas. To propose a change, fork the repository and open a pull request. Tell us what changed and how you tested it.

### Build the Windows bridge

Use Python 3.11 or newer. Run these commands from the project folder.

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python.exe build_windows.py
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The Windows build creates `release/RenDialogue-Windows-x64.zip`. The Workshop item is in [mod/RenDialogue-Workshop](mod/RenDialogue-Workshop). The add-on keeps the mod ID `RinDialogue` for existing saves.

### Run from source

Start Ollama and run `ollama pull qwen3.5:4b`. For one PC, run `start-ren.cmd` and leave the in-game **Ren Dialogue backend…** address blank. For two PCs, run `start-ren-lan.cmd` on the host and `start-ren-relay.cmd` on the gaming PC. Enter the host's access key in the relay, then set the host address in the game's right-click menu under **Ren Dialogue backend…**. For example, enter `http://192.168.1.16:8011`. The host listens on TCP port 8011.

| Setting | Default |
| --- | --- |
| `REN_MODEL` | `qwen3.5:4b` |
| `REN_OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` for the source launcher |
| `REN_LUA_DIR` | `%USERPROFILE%\Zomboid\Lua` |
| `REN_WARMUP` | `1`. Set to `0` to skip preloading the model. |
| `REN_BIND_HOST` | `0.0.0.0` for the source LAN launcher |
| `REN_API_TOKEN` | Optional preconfigured access key |

Older `RIN_*` settings still work when their `REN_*` equivalents are unset.
