-- Stand-alone mocked Project Zomboid hooks. Load this file, then RenDialogue.lua,
-- then call exercise(). The packaged mod never loads this harness.
local files, hooks, spoken = {}, {}, {}
local now = 1000000
local multiplayer = false
local data = {}
local lastPanel
local lastStatus
local entryText = "Are you all right?"
local companion = "Liz"
local romance = true
local key
local chatAction = "Rin Dialogue Open Companion Chat"
local bullet = false
local player = {}
local body = {x = 1}
local random = 0

require = function(name) assert(name == "ISUI/ISTextBox") end
Keyboard = {KEY_F9 = 67}
keyBinding = {}
local core = {
    getKey = function(_, action)
        assert(action == chatAction)
        return key or 0
    end,
    addKeyBinding = function(_, action, code, alternate, shift, ctrl, alt)
        assert(action == chatAction and alternate == 0 and not shift and not ctrl and not alt)
        key = code
    end,
    isKey = function(_, action, code)
        return action == chatAction and code == key
    end,
}
getCore = function() return core end
Events = {}
for _, event in ipairs({"OnFillWorldObjectContextMenu", "OnTick", "OnGameStart", "OnKeyStartPressed"}) do
    Events[event] = {Add = function(handler) hooks[event] = handler end}
end
isClient = function() return multiplayer end
isServer = function() return false end
getTimestampMs = function() return now end
getSpecificPlayer = function() return player end
ZombRand = function() random = random + 1; return random end
player.getX = function() return 0 end
player.getY = player.getX
player.getZ = player.getX
player.isDead = function() return false end
player.getModData = function() return data end
player.getDisplayName = function() return "Alex" end
player.getBodyDamage = function()
    return {getBodyParts = function()
        return {size = function() return 1 end, get = function()
            return {haveBullet = function() return bullet end}
        end}
    end}
end
body.getX = function(self) return self.x end
body.getY = player.getY
body.getZ = player.getZ
Bridge = {
    body = body, store = {rel = {f = 82, r = 61, days = 10}, kills = 12}, mode = "follow",
    alive = function() return true end,
    companionName = function() return companion end,
    say = function(text) spoken[#spoken + 1] = text end,
}
BridgeData = {
    owner = function() return player end,
    relOf = function(store) return store.rel end,
    relTier = function(rel) return rel.f > 75 and "Close" or "Friend" end,
}
BridgeSocial = {
    romanceOn = function() return romance end,
    romanceOpen = function(rel) return rel.f >= 30 and rel.days >= 2 end,
}
BridgeHeal = {done = 0}
BridgeDrive = {lastAny = 10}
BridgeFight = {fatigue = 0.25}
HaloTextHelper = {addBadText = function(_, text) lastStatus = text end}
getFileReader = function(name)
    if not files[name] then return nil end
    local iter = files[name]:gmatch("([^\n]*)\n")
    return {readLine = function() return iter() end, close = function() end}
end
getFileWriter = function(name)
    return {
        write = function(_, value) files[name] = value end,
        close = function() end,
    }
end
ISTextBox = {
    new = function(_, x, y, w, h, label, value, target, callback)
        local panel = {
            entry = {setMaxTextLength = function(_, length) assert(length == 160 or length == 200) end,
                     focus = function(self) self.focused = true end,
                     getText = function() return entryText end},
            yes = {setTitle = function(self, title) self.title = title end, internal = "OK"},
            initialise = function() end,
            addToUIManager = function() end,
            getIsVisible = function() return true end,
            onClick = function(_, button) callback(target, button, 0) end,
        }
        panel.yes.parent = panel
        lastPanel = {target = target, callback = callback, panel = panel, value = value, label = label}
        return panel
    end,
}

function exercise()
    assert(#keyBinding == 2 and keyBinding[1].value == "[Rin Dialogue]")
    assert(keyBinding[2].value == chatAction and keyBinding[2].key == Keyboard.KEY_F9)
    assert(key == Keyboard.KEY_F9)
    local options
    local context = {
        addOption = function(_, label, target, callback)
            options[label] = {target = target, callback = callback}
            return options[label]
        end,
    }
    local function openMenu()
        options = {}
        hooks.OnFillWorldObjectContextMenu(0, context, {}, false)
        return options["Talk to " .. companion .. " (AI)"]
    end
    key = 68
    hooks.OnGameStart()
    assert(key == 68 and #keyBinding == 2)
    multiplayer = true
    assert(openMenu() == nil)
    multiplayer = false
    companion = ""
    openMenu()
    assert(options["Talk to Ren (AI)"])
    companion = "Liz"
    openMenu().callback(player)
    assert(lastPanel == nil and lastStatus:find("backend address", 1, true))
    files["rin_dialogue_ready.txt"] = "RIN1\n1000\nlocal\n"
    hooks.OnTick()
    assert(files["rin_dialogue_state.txt"]:find("\n82\n61\n1\n1\nAlex\nLiz\n", 1, true))
    hooks.OnKeyStartPressed(67)
    assert(lastPanel == nil)
    hooks.OnKeyStartPressed(68)
    local ui = lastPanel
    assert(ui.label == "Talk to Liz" and ui.panel.entry.focused and ui.panel.yes.title == "Send")
    ui.panel.entry:onCommandEntered()
    local request = files["rin_dialogue_request.txt"]
    assert(request:find("\n82\n61\n1\n1\nAlex\nAre you all right?\nLiz\n1000000\nClose\n10\n12\n0.25\nfollow\n\nEND\n", 1, true))
    local id = request:match("^RIN1\n([^\n]+)\n")
    assert(id and data.RinDialogueSaveId and openMenu().notAvailable)
    files["rin_dialogue_response.txt"] = "RIN1\nold-id\nOK\nStale reply\nEND\n"
    hooks.OnTick()
    assert(#spoken == 0)
    now = now + 250
    files["rin_dialogue_response.txt"] = "RIN1\n" .. id .. "\nOK\nI'm all right.\nEND\n"
    hooks.OnTick()
    assert(#spoken == 1 and spoken[1] == "I'm all right.")
    assert(Bridge.store.rel.f == 82 and Bridge.store.rel.r == 61)
    romance = false
    companion = "Liz Evans"
    Bridge.store.rel.f = 65
    BridgeHeal.done = 1
    BridgeDrive.lastAny = 11
    BridgeDrive.info = "EvCarCrashHard"
    bullet = true
    now = now + 2000
    hooks.OnTick()
    local state = files["rin_dialogue_state.txt"]
    assert(state:find("\n65\n61\n0\n0\nAlex\nLiz Evans\n", 1, true))
    assert(state:find("treatment:1002250,bullet_wound:1002250,possible_crash:1002250", 1, true))
    openMenu().callback(player)
    lastPanel.panel:onClick(lastPanel.panel.yes)
    assert(files["rin_dialogue_request.txt"]:find("\n65\n61\n0\n0\n", 1, true))
    local second = files["rin_dialogue_request.txt"]:match("^RIN1\n([^\n]+)\n")
    files["rin_dialogue_response.txt"] = "RIN1\n" .. second .. "\nOK\nWe should rest.\nEND\n"
    hooks.OnTick()
    assert(#spoken == 2)
    body.x = 5
    assert(openMenu() == nil)
    options["Ren Dialogue backend..."].callback(player)
    entryText = "192.168.1.16:8011"
    lastPanel.callback(player, {internal = "OK", parent = lastPanel.panel}, 0)
    assert(files["rin_dialogue_backend.txt"] == "RIN1\nhttp://192.168.1.16:8011\nEND\n")
    options["Ren Dialogue status"].callback(player)
    assert(lastStatus == "Start the dialogue relay first.")
    files["rin_dialogue_ready.txt"] = "RIN1\n1000\nrelay\n"
    files["rin_dialogue_connection.txt"] = "RIN1\nhttp://192.168.1.16:8011\nOK\nRin backend connected.\nEND\n"
    options["Ren Dialogue status"].callback(player)
    assert(lastStatus == "Ren Dialogue connected.")
    options["Ren Dialogue backend..."].callback(player)
    assert(lastPanel.value == "http://192.168.1.16:8011")
    return "Ren Lua hooks passed"
end
