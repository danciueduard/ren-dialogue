require "ISUI/ISTextBox"

local REQUEST = "rin_dialogue_request.txt"
local RESPONSE = "rin_dialogue_response.txt"
local HEARTBEAT = "rin_dialogue_ready.txt"
local SETTINGS = "rin_dialogue_backend.txt"
local CONNECTION = "rin_dialogue_connection.txt"
local STATE = "rin_dialogue_state.txt"
local VERSION = "RIN1"
local MAX_MESSAGE = 160
local KEY_SECTION = "[Rin Dialogue]"
local CHAT_KEY = "Rin Dialogue Open Companion Chat"
local pending, promptOpen
local pollAt = 0
local observeAt, syncAt, refreshAt = 0, 0, 0
local lastSnapshot, observedPlayer, lastHeal, lastCrash, lastBullets
local recentKinds = {"treatment", "bullet_wound", "possible_crash"}
local recentWindow = 15 * 60 * 1000

local function registerChatKey()
    if type(keyBinding) ~= "table" then return end
    local hasSection = false
    for _, binding in ipairs(keyBinding) do
        if type(binding) == "table" then
            if binding.value == CHAT_KEY then return end
            if binding.value == KEY_SECTION then hasSection = true end
        end
    end
    if not hasSection then table.insert(keyBinding, {value = KEY_SECTION}) end
    table.insert(keyBinding, {value = CHAT_KEY, key = Keyboard.KEY_F9})
    local core = getCore()
    if (tonumber(core:getKey(CHAT_KEY)) or 0) <= 0 then
        core:addKeyBinding(CHAT_KEY, Keyboard.KEY_F9, 0, false, false, false)
    end
end

local function singlePlayer()
    return not isClient() and not isServer()
end

local function status(player, text)
    HaloTextHelper.addBadText(player, text)
end

local function readLines(path, count)
    local ok, reader = pcall(getFileReader, path, false)
    if not ok or not reader then return nil end
    local readOK, lines = pcall(function()
        local result = {}
        for i = 1, count do
            result[i] = reader:readLine()
            if result[i] == nil then return nil end
        end
        return result
    end)
    pcall(function() reader:close() end)
    if readOK then return lines end
    return nil
end

local function bridgeReady(now)
    local ready = readLines(HEARTBEAT, 3)
    local epoch = ready and tonumber(ready[2])
    if ready and ready[1] == VERSION and epoch and math.abs(now / 1000 - epoch) < 12 then
        return ready[3]
    end
end

local function backendAddress()
    local setting = readLines(SETTINGS, 3)
    if setting and setting[1] == VERSION and setting[3] == "END" and setting[2] ~= "LOCAL" then
        return setting[2]
    end
    return ""
end

local function readyToTalk(player)
    local address = backendAddress()
    local mode = bridgeReady(getTimestampMs())
    if address ~= "" and mode ~= "relay" then
        status(player, "Start the dialogue relay first.")
        return false
    end
    if address == "" and mode ~= "local" then
        status(player, "Set the dialogue backend address or start the local engine.")
        return false
    end
    return true
end

local function activeCompanion(player)
    if not player or not singlePlayer() or not Bridge or not BridgeData or not BridgeSocial
        or not Bridge.store or player:isDead() or BridgeData.owner() ~= player then return false end
    return true
end

local function nearbyCompanion(player)
    if not activeCompanion(player) or not Bridge.body or not Bridge.alive() then return nil end
    local body = Bridge.body
    if math.floor(player:getZ()) ~= math.floor(body:getZ()) then return nil end
    local dx, dy = player:getX() - body:getX(), player:getY() - body:getY()
    if dx * dx + dy * dy <= 9 then return body end
    return nil
end

local function identifier(now)
    return string.format("%.0f-%09d-%09d", now, ZombRand(1000000000), ZombRand(1000000000))
end

local function singleLine(text, limit)
    text = tostring(text or ""):gsub("[%c]", " "):gsub("^%s+", ""):gsub("%s+$", "")
    -- The input box also enforces the length. Refuse oversize strings before writing.
    if #text > limit then return nil end
    return text
end

local function writeLines(path, lines)
    local writer = getFileWriter(path, true, false)
    if not writer then error("The dialogue request file is unavailable") end
    local ok, message = pcall(function() writer:write(table.concat(lines, "\n") .. "\n") end)
    writer:close()
    if not ok then error(message) end
end

local function companionName()
    local name = singleLine(Bridge.companionName(), 60)
    return name and name ~= "" and name or "Ren"
end

local function stamp(now)
    return string.format("%.0f", now)
end

local function numberField(value, low, high)
    local n = tonumber(value)
    if not n or n ~= n then return "" end
    return tostring(math.max(low, math.min(high, n)))
end

local function observeEvents(player, now)
    local data = player:getModData()
    if type(data.RinDialogueEvents) ~= "table" then data.RinDialogueEvents = {} end
    local recent = data.RinDialogueEvents
    if observedPlayer ~= player then
        observedPlayer, lastHeal, lastCrash, lastBullets = player, nil, nil, nil
    end
    local healed = BridgeHeal and tonumber(BridgeHeal.done)
    if healed and lastHeal and healed > lastHeal then recent.treatment = now end
    lastHeal = healed
    local crash = BridgeDrive and BridgeDrive.lastAny
    if crash and lastCrash and crash ~= lastCrash
        and (BridgeDrive.info == "EvCarCrash" or BridgeDrive.info == "EvCarCrashHard") then
        recent.possible_crash = now
    end
    lastCrash = crash
    local bullets
    if nearbyCompanion(player) then
        local ok, count = pcall(function()
            local parts = player:getBodyDamage():getBodyParts()
            local total = 0
            for i = 0, parts:size() - 1 do
                if parts:get(i):haveBullet() then total = total + 1 end
            end
            return total
        end)
        if ok then bullets = count end
    end
    if bullets and lastBullets and bullets > lastBullets then recent.bullet_wound = now end
    lastBullets = bullets
end

local function snapshot(player, now)
    local rel = BridgeData.relOf(Bridge.store)
    local data = player:getModData()
    if type(data.RinDialogueSaveId) ~= "string" then data.RinDialogueSaveId = identifier(now) end
    local playerName = singleLine(player:getDisplayName(), 60)
    if not playerName or playerName == "" then playerName = "Survivor" end
    local enabled = BridgeSocial.romanceOn() == true
    local opened = enabled and BridgeSocial.romanceOpen(rel) == true
    local fields = {
        data.RinDialogueSaveId, numberField(rel.f, -100, 100), numberField(rel.r, 0, 100),
        enabled and "1" or "0", opened and "1" or "0", playerName,
    }
    local events = {}
    for _, kind in ipairs(recentKinds) do
        local at = data.RinDialogueEvents and tonumber(data.RinDialogueEvents[kind])
        if at and now >= at and now - at <= recentWindow then
            table.insert(events, kind .. ":" .. stamp(at))
        end
    end
    local extra = {
        companionName(), stamp(now), BridgeData.relTier(rel),
        numberField(math.floor(tonumber(rel.days) or 0), 0, 1000000),
        numberField(math.floor(tonumber(Bridge.store.kills) or 0), 0, 1000000000),
        numberField(BridgeFight and BridgeFight.fatigue or Bridge.store.fatigue, 0, 1),
        singleLine(Bridge.mode, 40) or "", table.concat(events, ","),
    }
    return fields, extra
end

local function writeSnapshot(path, player, now, id, message)
    local fields, extra = snapshot(player, now)
    local lines = {VERSION}
    if id then table.insert(lines, id) end
    for _, value in ipairs(fields) do table.insert(lines, value) end
    if message then table.insert(lines, message) end
    for _, value in ipairs(extra) do table.insert(lines, value) end
    table.insert(lines, "END")
    if id then
        writeLines(path, lines)
        return
    end
    local signature = table.concat(fields, "\n") .. "\n" .. extra[1] .. "\n" .. table.concat(extra, "\n", 3)
    if signature == lastSnapshot and now < refreshAt then return end
    writeLines(path, lines)
    lastSnapshot, refreshAt = signature, now + 15000
end

local function submit(player, button)
    promptOpen = nil
    if not button or button.internal ~= "OK" or pending then return end
    local body = nearbyCompanion(player)
    if not body then return end
    local text = singleLine(button.parent.entry:getText(), MAX_MESSAGE)
    if not text or text == "" then return end
    local now = getTimestampMs()
    if not readyToTalk(player) then return end

    local id = identifier(now)
    observeEvents(player, now)
    local ok, errorText = pcall(writeSnapshot, REQUEST, player, now, id, text)
    if not ok then
        print("[Ren Dialogue] " .. tostring(errorText))
        status(player, "Couldn't send your message to " .. companionName() .. ".")
        return
    end
    pending = {id = id, player = player, body = body, name = companionName(), deadline = now + 65000}
end

local function openPrompt(player)
    if pending or promptOpen or not nearbyCompanion(player) then return end
    if not readyToTalk(player) then return end
    local panel = ISTextBox:new(0, 0, 360, 170, "Talk to " .. companionName(), "", player, submit, 0)
    panel:initialise()
    panel.entry:setMaxTextLength(MAX_MESSAGE)
    panel.yes:setTitle("Send")
    panel.noEmpty = true
    panel.entry.onCommandEntered = function(entry)
        local text = singleLine(entry:getText(), MAX_MESSAGE)
        if promptOpen == panel and text and text ~= "" then panel:onClick(panel.yes) end
    end
    panel:addToUIManager()
    promptOpen = panel
    panel.entry:focus()
end

local function onChatKey(key)
    if key <= 0 or not getCore():isKey(CHAT_KEY, key) then return end
    if MainScreen and MainScreen.instance and not MainScreen.instance.inGame then return end
    if MainOptions and MainOptions.instance and MainOptions.instance:isVisible() then return end
    if promptOpen and not promptOpen:getIsVisible() then promptOpen = nil end
    openPrompt(getSpecificPlayer(0))
end

local function saveBackend(player, button)
    promptOpen = nil
    if not button or button.internal ~= "OK" then return end
    local address = singleLine(button.parent.entry:getText(), 200)
    if not address or address:find("%s") then
        status(player, "Enter a backend address without spaces.")
        return
    end
    if address ~= "" and not address:find("://", 1, true) then address = "http://" .. address end
    if not pcall(writeLines, SETTINGS, {VERSION, address ~= "" and address or "LOCAL", "END"}) then
        status(player, "Could not save the dialogue backend address.")
        return
    end
    status(player, address == "" and "Dialogue set to local engine." or "Address saved. Check Ren Dialogue status.")
end

local function configureBackend(player)
    if pending or promptOpen then return end
    local panel = ISTextBox:new(0, 0, 450, 180, "Dialogue backend address (blank for local engine)",
        backendAddress(), player, saveBackend, 0)
    panel:initialise()
    panel.entry:setMaxTextLength(200)
    panel:addToUIManager()
    promptOpen = panel
end

local function showConnection(player)
    if not readyToTalk(player) then return end
    local address = backendAddress()
    if address == "" then
        status(player, "Local dialogue engine is running.")
        return
    end
    local result = readLines(CONNECTION, 5)
    if result and result[1] == VERSION and result[2] == address and result[5] == "END" then
        local message = result[4]:gsub("[<>]", "")
        status(player, (message:gsub("^Rin backend", "Ren Dialogue")))
    else
        status(player, "Checking the dialogue backend. Try again in a few seconds.")
    end
end

local function worldMenu(playerNum, context, _, test)
    if test or playerNum ~= 0 or not singlePlayer() then return end
    local player = getSpecificPlayer(playerNum)
    if not player or player:isDead() then return end
    if promptOpen and not promptOpen:getIsVisible() then promptOpen = nil end
    local setup = context:addOption("Ren Dialogue backend...", player, configureBackend)
    setup.notAvailable = pending ~= nil or promptOpen ~= nil
    context:addOption("Ren Dialogue status", player, showConnection)
    if nearbyCompanion(player) then
        local option = context:addOption("Talk to " .. companionName() .. " (AI)", player, openPrompt)
        option.notAvailable = pending ~= nil or promptOpen ~= nil
    end
end

local function onTick()
    local now = getTimestampMs()
    if now >= observeAt then
        observeAt = now + 250
        local player = getSpecificPlayer(0)
        if activeCompanion(player) then
            observeEvents(player, now)
            if now >= syncAt then
                syncAt = now + 2000
                local ok, errorText = pcall(writeSnapshot, STATE, player, now)
                if not ok then print("[Ren Dialogue] " .. tostring(errorText)) end
            end
        end
    end
    if not pending then return end
    if now < pollAt then return end
    pollAt = now + 200
    local turn = pending
    local response = readLines(RESPONSE, 5)
    if response and response[1] == VERSION and response[2] == turn.id and response[5] == "END" then
        pending = nil
        if getSpecificPlayer(0) ~= turn.player then return end
        local text = response[4]:gsub("[<>]", "")
        if response[3] == "OK" and nearbyCompanion(turn.player) == turn.body then
            Bridge.say(text)
        elseif response[3] == "OK" then
            status(turn.player, turn.name .. " is out of earshot.")
        else
            status(turn.player, text)
        end
    elseif now >= turn.deadline then
        pending = nil
        if getSpecificPlayer(0) == turn.player then status(turn.player, "The companion's reply timed out.") end
    end
end

registerChatKey()
Events.OnFillWorldObjectContextMenu.Add(worldMenu)
Events.OnTick.Add(onTick)
Events.OnKeyStartPressed.Add(onChatKey)
Events.OnGameStart.Add(function()
    registerChatKey()
    pending, promptOpen, pollAt = nil, nil, 0
    observeAt, syncAt, refreshAt = 0, 0, 0
    observedPlayer, lastSnapshot = nil, nil
end)

