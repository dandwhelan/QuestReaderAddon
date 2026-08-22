local addonName, addon = ...
local LDB = LibStub("LibDataBroker-1.1")
local icon = LibStub("LibDBIcon-1.0")

-- Ogg first: new content is generated as Ogg Vorbis, and where both exist the
-- smaller file is preferred.
SOUND_EXTENSIONS = { ".ogg", ".wav" }

local optionalSoundPacks = {
    "QuestReaderAddon_TWW_EN",
    "QuestReaderAddon_TWW_FR"
}

-- Default values for saved settings. SavedVariables are not populated until
-- ADDON_LOADED fires, so defaults are applied in InitializeAddonDB rather than
-- at file scope, where they would be overwritten by the saved variables file.
local defaultSettings = {
    showMinimapButton = true,
    autoPlayEnabled = true,
    autoPlayInQuestMap = false,
    muteGossip = true,
    stopDialogueOnClose = true,
}

local function InitializeAddonDB()
    QuestReaderAddonDB = QuestReaderAddonDB or {}
    QuestReaderAddonDB.minimapButton = QuestReaderAddonDB.minimapButton or { hide = false }
    QuestReaderAddonDB.minimapIconPosition = QuestReaderAddonDB.minimapIconPosition or {}

    for option, default in pairs(defaultSettings) do
        if QuestReaderAddonDB[option] == nil then
            QuestReaderAddonDB[option] = default
        end
    end

    QuestReaderAddonDB.IsPaused = false
    QuestReaderAddonDB.IsSoundPaused = false
end

-- Create the LDB launcher
local questReaderLauncher = LDB:NewDataObject("QuestReaderAddon", {
    type = "launcher",
    icon = "Interface\\AddOns\\" .. addonName .. "\\cs_icon",  -- Adjusted icon path
    OnClick = function(_, button)
        if button == "LeftButton" then
            addon:OpenSettings()
        elseif button == "RightButton" then
            if SlashCmdList["QUESTREADERHARVEST"] then
                SlashCmdList["QUESTREADERHARVEST"]("export")
            else
                addon:OpenSettings()
            end
        end
    end,
    OnTooltipShow = function(tooltip)
        tooltip:AddLine("Quest Reader")
        tooltip:AddLine("|cffffffffLeft-Click:|r Open Settings", 1, 1, 1)
        if SlashCmdList["QUESTREADERHARVEST"] or QuestReaderHarvesterDB then
            tooltip:AddLine("|cff00ff00Right-Click:|r Export Harvested Data", 0.2, 1, 0.2)
        end
    end,
})

local function UpdateMinimapButtonVisibility()
    if QuestReaderAddonDB.showMinimapButton then
        icon:Show("QuestReaderAddon")
        C_Timer.After(0.1, function()
            local minimapButton = icon:GetMinimapButton("QuestReaderAddon")
            if minimapButton and QuestReaderAddonDB.minimapIconPosition.point then
                minimapButton:ClearAllPoints()
                minimapButton:SetPoint(
                    QuestReaderAddonDB.minimapIconPosition.point,
                    Minimap,
                    QuestReaderAddonDB.minimapIconPosition.relPoint,
                    QuestReaderAddonDB.minimapIconPosition.x,
                    QuestReaderAddonDB.minimapIconPosition.y
                )
            end
        end)
    else
        icon:Hide("QuestReaderAddon")
    end
end
addon.UpdateMinimapButtonVisibility = UpdateMinimapButtonVisibility

-- Create a frame to listen for the ADDON_LOADED event
local loadingFrame = CreateFrame("Frame")
loadingFrame:RegisterEvent("ADDON_LOADED")
loadingFrame:RegisterEvent("PLAYER_LOGIN")
loadingFrame:SetScript("OnEvent", function(self, event, loadedAddonName)
    if event == "ADDON_LOADED" and loadedAddonName == addonName then
        InitializeAddonDB()
        if questReaderLauncher then
            icon:Register("QuestReaderAddon", questReaderLauncher, {
                hide = not QuestReaderAddonDB.showMinimapButton,
                position = QuestReaderAddonDB.minimapIconPosition
            })
            C_Timer.After(0.5, function()
                UpdateMinimapButtonVisibility()
                local minimapButton = icon:GetMinimapButton("QuestReaderAddon")
                if minimapButton then
                    minimapButton:HookScript("OnDragStop", function()
                        local point, _, relPoint, x, y = minimapButton:GetPoint()
                        QuestReaderAddonDB.minimapIconPosition = {
                            point = point,
                            relPoint = relPoint,
                            x = x,
                            y = y
                        }
                    end)
                end
            end)
        else
            print("Error: questReaderLauncher is nil")
        end
        loadingFrame:UnregisterEvent("ADDON_LOADED")
    elseif event == "PLAYER_LOGIN" then
        -- All addons should now be loaded, call DetectSoundPacks safely here
        DetectSoundPacks()
    end
end)
-- Quest frame buttons are created from InitializeQuestFrameButtons on
-- ADDON_LOADED. Creating them at file scope as well produced a second,
-- identical button stacked on the first under the same global name.
local questButtonFrame = CreateFrame("Frame")
questButtonFrame:RegisterEvent("ADDON_LOADED")

local function InitializeQuestFrameButtons()
    -- Create the button for the QuestFrame
    local questFrameButton = CreateFrame("Button", "QuestReaderButtonFrame", QuestFrame, "UIPanelButtonTemplate")
    questFrameButton:SetSize(90, 21)
    questFrameButton:SetText("Read Quest")
    questFrameButton:SetPoint("BOTTOMRIGHT", QuestFrame, "BOTTOMRIGHT", -120, 4)

    questFrameButton:SetScript("OnClick", function()
        PlayQuestAudio(nil, true)
    end)

    -- Create the button for the Quest Log (QuestMapFrame)
    if QuestMapFrame and QuestMapFrame.DetailsFrame then
        local questLogButton = CreateFrame("Button", "QuestLogReaderButtonFrame", QuestMapFrame.DetailsFrame, "UIPanelButtonTemplate")
        questLogButton:SetSize(90, 21)
        questLogButton:SetText("Read Quest")
        -- Position it at the top left of the details frame, offset to the right to be next to Back button
        questLogButton:SetPoint("TOPLEFT", QuestMapFrame.DetailsFrame, "TOPLEFT", 105, -10)

        questLogButton:SetScript("OnClick", function()
            PlayQuestAudio("description", true)
        end)
    end
end

questButtonFrame:SetScript("OnEvent", function(self, event, loadedAddonName)
    if loadedAddonName == addonName then
        InitializeQuestFrameButtons()
        questButtonFrame:UnregisterEvent("ADDON_LOADED")
    end
end)

-- Slash command to toggle minimap button
SLASH_QRTOGGLE1 = '/qrtoggle'
SlashCmdList["QRTOGGLE"] = function()
    QuestReaderAddonDB.showMinimapButton = not QuestReaderAddonDB.showMinimapButton
    UpdateMinimapButtonVisibility()
    print("Minimap button visibility: " .. tostring(QuestReaderAddonDB.showMinimapButton))
end

-- Function to automatically play quest audio
local function AutoPlayQuestAudio()
    if QuestReaderAddonDB.autoPlayEnabled then
        PlayQuestAudio()
    end
end

-- Hook the QuestFrame's OnShow event
-- QuestFrame:HookScript("OnShow", AutoPlayQuestAudio)

local function LoadSoundPackIfAvailable(addonName)
    local name, title, _, loadable = C_AddOns.GetAddOnInfo(addonName)
    if name and loadable then
        if not C_AddOns.IsAddOnLoaded(addonName) then
            local loaded, reason = C_AddOns.LoadAddOn(addonName)
            if not loaded then
                return nil
            end
        end

        return _G[addonName]  -- Return the global table for the addon
    end
    return nil
end

addon.soundSources = {}
addon.soundSources["QuestReaderAddon"] = QuestReaderSoundLengths
addon.activeSound = nil
-- Quest audio the player has encountered that no installed pack provides.
addon.reportedMissing = {}

-- Function to detect available sound packs
function DetectSoundPacks()
    local hasEntries = false
    
    for _, depName in ipairs(optionalSoundPacks) do
        if depName then
            local name, _, _, loadable = C_AddOns.GetAddOnInfo(depName)
            if loadable then
                local pack = LoadSoundPackIfAvailable(depName)
                if pack then
                    -- Merge rather than replace: overwriting dropped the sounds
                    -- bundled with the base addon, and breaking here meant only
                    -- one language pack could ever be active at a time.
                    addon.soundSources[name] = pack
                    hasEntries = true
                end
            end
        end
    end

--    if not hasEntries then
--        ShowNoSoundPacksDialog()
--    end
end

-- function ShowNoSoundPacksDialog()
--     -- Define the static popup dialog
--     StaticPopupDialogs["NO_SOUND_PACKS"] = {
--         text = "You're using Quest Reader Addon but you've not installed any Quest Reader sound packs.",
--         button1 = "OK",
--         timeout = 0,  -- No timeout
--         whileDead = true,  -- Allow showing while dead
--         hideOnEscape = true,  -- Allow closing by pressing the Escape key
--         preferredIndex = 3,  -- Prevents interference with other dialogs
--     }-- 

--     -- Show the popup dialog
--     StaticPopup_Show("NO_SOUND_PACKS")
-- end

function GetCurrentSound()
    return addon.activeSound
end

function DoPlaySound()
    soundData = GetCurrentSound()
    if not soundData then
        -- This can fire when a sound was canceled while in timer
        return
    end

    local audioChannel = "Dialog"
    if QuestReaderAddonDB.muteGossip then
        MuteDialogChannel()
        audioChannel = "Master"
    end

    soundData.isPlaying = true
    soundData.isPlaying, soundData.soundHandle = PlaySoundFile(soundData.soundPath, audioChannel)

    if soundData.soundHandle then
        --print("Playing audio: " .. soundData.soundPath)
    else
        print("Failed to play audio: " .. soundData.soundFile)
        soundData.isPlaying = false
    end
    addon.currentSound = soundData
end

function IsPlaying()
    local currentSound = GetCurrentSound()
    return currentSound and currentSound.isPlaying
end

function StopCurrentSound()
    local currentSound = GetCurrentSound()

    if not currentSound then
        return
    end
    
    if currentSound.nextSoundTimer then
        currentSound.nextSoundTimer:Cancel()
    end

    if currentSound.soundHandle then
        StopSound(currentSound.soundHandle)
    else
        -- print("No sound currently playing")
    end

    if QuestReaderAddonDB.muteGossip then
        UnmuteDialogChannel()
    end

    addon.activeSound = nil
end

function PlayQuestAudio(textType, skipDelay)
    -- Get quest ID from Quest Log if it's open, otherwise from quest giver
    local questID
    if QuestFrame and QuestFrame:IsVisible() then
        questID = GetQuestID()
    elseif QuestMapFrame and QuestMapFrame:IsVisible() then
        questID = C_QuestLog.GetSelectedQuest()
    else
        questID = GetQuestID()
    end

    if not textType then
        -- Initialize textType based on visible panels
        if QuestFrameDetailPanel:IsVisible() then
            textType = "description"
        elseif QuestFrameProgressPanel:IsVisible() then
            textType = "progress"
        elseif QuestFrameRewardPanel:IsVisible() then
            textType = "completion"
        -- Gossip has its own playback path, PlayGossipAudio: it has no
        -- questID to key on, so it cannot share this function's lookup.
        else
            if (lastTextType == "") then
                return
            else
                textType = lastTextType
            end
        end
    end

    -- Debug: Ensure questID and textType are valid
    if questID and textType ~= "" then
        -- Stop any sound that is currently playing
        if IsPlaying() then
            StopCurrentSound()
        end

        -- Newly generated audio ships as Ogg Vorbis, which is a fraction of the
        -- size of the original PCM library and is what the game itself uses.
        -- Both are accepted so a pack can mix them while it is converted over.
        local baseName = questID .. "_" .. textType
        local soundFile, soundPath

        for _, extension in ipairs(SOUND_EXTENSIONS) do
            local candidate = baseName .. extension
            for packName, soundLengths in pairs(addon.soundSources) do
                if soundLengths[candidate] then
                    soundFile = candidate
                    soundPath = "Interface\\AddOns\\" .. packName .. "\\Sounds\\" .. candidate
                    break
                end
            end
            if soundPath then
                break
            end
        end

        if not soundPath then
            -- Report the gap so players can tell "not recorded yet" apart from
            -- a broken addon, and can report the ID. Deduplicated per session so
            -- autoplay does not repeat the same line on every quest interaction.
            if not addon.reportedMissing[baseName] then
                addon.reportedMissing[baseName] = true
                print("Quest Reader: no audio for quest " .. questID .. " (" .. textType .. ")")
            end
            addon.activeSound = nil
            return
        end

        addon.activeSound = {
            questID = questID,
            textType = textType,
            soundFile = soundFile,
            soundPath = soundPath,
        }
        
        -- Delay shortly to account for greeting audio when using autoplay
        if QuestReaderAddonDB.autoPlayEnabled and not skipDelay and not QuestReaderAddonDB.muteGossip then
            addon.activeSound.nextSoundTimer = C_Timer.After(2, function()
                DoPlaySound()
            end)
        else
            DoPlaySound()
        end
    end
end

-- "Creature-0-<server>-<instance>-<zone>-<creatureID>-<spawn>"
-- Mirrors the harvester's own copy: the two never load together, so sharing
-- code between them is not an option without a third file just for this.
local function CreatureIDFromGUID(guid)
    if not guid then
        return nil
    end
    local unitType, _, _, _, _, creatureID = strsplit("-", guid)
    if unitType == "Creature" or unitType == "Vehicle" then
        return tonumber(creatureID)
    end
    return nil
end

function PlayGossipAudio()
    local npcID = CreatureIDFromGUID(UnitGUID("npc"))
    if not npcID then
        return
    end

    if IsPlaying() then
        StopCurrentSound()
    end

    -- An NPC can have several greeting variants, captured as gossip1,
    -- gossip2, and so on, but nothing here can tell which one the server
    -- just chose to show -- SoundLengths carries only a duration per file,
    -- not the text it was generated from. The first captured variant plays
    -- regardless. NPCs with a single greeting, the common case, are
    -- unaffected; NPCs with several will sometimes say the wrong line until
    -- variant matching is built.
    local baseName = "npc" .. npcID .. "_gossip1"
    local soundFile, soundPath

    for _, extension in ipairs(SOUND_EXTENSIONS) do
        local candidate = baseName .. extension
        for packName, soundLengths in pairs(addon.soundSources) do
            if soundLengths[candidate] then
                soundFile = candidate
                soundPath = "Interface\\AddOns\\" .. packName .. "\\Sounds\\" .. candidate
                break
            end
        end
        if soundPath then
            break
        end
    end

    if not soundPath then
        -- Same dedup as the quest path: report once per session, not once
        -- per NPC interaction.
        if not addon.reportedMissing[baseName] then
            addon.reportedMissing[baseName] = true
            print("Quest Reader: no gossip audio for NPC " .. npcID)
        end
        addon.activeSound = nil
        return
    end

    addon.activeSound = {
        questID = "npc" .. npcID,
        textType = "gossip",
        soundFile = soundFile,
        soundPath = soundPath,
    }
    DoPlaySound()
end

local currentItemName = nil
local currentItemPage = 1
local itemAudioTimer = nil

local function PlayItemAudioDirect(itemLink, page)
    if IsPlaying() then
        StopCurrentSound()
    end

    page = page or 1
    local itemID = tonumber(itemLink:match("item:(%d+)"))
    local baseNames = {}

    if itemID then
        table.insert(baseNames, "item" .. itemID .. "_page" .. page)
    else
        local clean = itemLink:lower():gsub("[^%w%s]", ""):gsub("%s+", "_")
        table.insert(baseNames, "item_" .. clean .. "_page" .. page)
        table.insert(baseNames, clean .. "_page" .. page)
    end

    local soundFile, soundPath
    for _, baseName in ipairs(baseNames) do
        for _, extension in ipairs(SOUND_EXTENSIONS) do
            local candidate = baseName .. extension
            for packName, soundLengths in pairs(addon.soundSources) do
                if soundLengths[candidate] then
                    soundFile = candidate
                    soundPath = "Interface\\AddOns\\" .. packName .. "\\Sounds\\" .. candidate
                    break
                end
            end
            if soundPath then
                break
            end
        end
        if soundPath then
            break
        end
    end

    if not soundPath then
        local displayName = itemID and ("item " .. itemID) or ("'" .. itemLink .. "'")
        local reportKey = baseNames[1]
        if not addon.reportedMissing[reportKey] then
            addon.reportedMissing[reportKey] = true
            print("Quest Reader: no audio for " .. displayName .. " (page " .. page .. ")")
        end
        addon.activeSound = nil
        return
    end

    addon.activeSound = {
        questID = baseNames[1],
        textType = "item",
        soundFile = soundFile,
        soundPath = soundPath,
    }
    print("Quest Reader: playing " .. (itemID and ("item " .. itemID) or ("'" .. itemLink .. "'")) .. " (page " .. page .. ")")
    DoPlaySound()
end

local function HookDialogueUI()
    if DUIBookFrame and not addon.duiHooked then
        if type(DUIBookFrame.ScrollToPage) == "function" and type(DUIBookFrame.ScrollTo) == "function" then
            addon.duiHooked = true
            hooksecurefunc(DUIBookFrame, "ScrollToPage", function(self, page)
                if self:IsShown() and page and page > 0 and page ~= currentItemPage and currentItemName then
                    PlayItemAudio(page)
                end
            end)
            hooksecurefunc(DUIBookFrame, "ScrollTo", function(self, offset)
                if self:IsShown() and offset == 0 and currentItemPage ~= 1 and currentItemName then
                    PlayItemAudio(1)
                end
            end)
        end
    end
end

function PlayItemAudio(page)
    local itemLink = ItemTextGetItem()
    if not itemLink or itemLink == "" then
        if DUIBookFrame and DUIBookFrame.Header and DUIBookFrame.Header.Title then
            itemLink = DUIBookFrame.Header.Title:GetText()
        end
    end
    if not itemLink or itemLink == "" then
        return
    end

    HookDialogueUI()

    -- Explicit page requested (e.g. user clicked page 1, 2, 3, 4 tab or scrolled)
    if page then
        currentItemPage = page
        currentItemName = itemLink
        if itemAudioTimer then
            itemAudioTimer:Cancel()
            itemAudioTimer = nil
        end
        PlayItemAudioDirect(itemLink, page)
        return
    end

    -- Initial opening event from game: always start on Page 1
    if itemLink ~= currentItemName then
        currentItemName = itemLink
        currentItemPage = 1

        if itemAudioTimer then
            itemAudioTimer:Cancel()
            itemAudioTimer = nil
        end

        itemAudioTimer = C_Timer.After(0.1, function()
            itemAudioTimer = nil
            if currentItemName == itemLink then
                PlayItemAudioDirect(itemLink, 1)
            end
        end)
    else
        -- Standard Blizzard UI (without DialogueUI)
        if not DUIBookFrame or not DUIBookFrame:IsShown() then
            local p = ItemTextGetPage() or 1
            if p ~= currentItemPage then
                currentItemPage = p
                PlayItemAudioDirect(itemLink, p)
            end
        end
    end
end

local function OnPlayerLogout()
    local currentSound = GetCurrentSound()
    if currentSound and currentSound.nextSoundTimer then
        currentSound.nextSoundTimer:Cancel()
    end
    UnmuteDialogChannel()
end

-- Keybindings
BINDING_HEADER_QUESTREADERADDON = "Quest Reader Addon"
BINDING_NAME_PLAYACTIVEQUEST = "Play active quest voiceover"

-- Event Handling for Quest Dialog Events
local questEventFrame = CreateFrame("Frame")
questEventFrame:RegisterEvent("ADDON_LOADED")
questEventFrame:RegisterEvent("QUEST_DETAIL")
questEventFrame:RegisterEvent("QUEST_PROGRESS")
questEventFrame:RegisterEvent("QUEST_COMPLETE")
questEventFrame:RegisterEvent("QUEST_FINISHED")
questEventFrame:RegisterEvent("GOSSIP_SHOW")
questEventFrame:RegisterEvent("GOSSIP_CLOSED")
questEventFrame:RegisterEvent("ITEM_TEXT_READY")
questEventFrame:RegisterEvent("ITEM_TEXT_CLOSED")

questEventFrame:SetScript("OnEvent", function(self, event, ...)
    if event == "ADDON_LOADED" then
        HookDialogueUI()
        return
    end
    -- Gossip and Items have no questID and their own lookup, so they are handled before
    -- anything below assumes one exists.
    if event == "GOSSIP_SHOW" then
        if QuestReaderAddonDB.autoPlayEnabled then
            PlayGossipAudio()
        end
        return
    elseif event == "GOSSIP_CLOSED" then
        if QuestReaderAddonDB.stopDialogueOnClose then
            StopCurrentSound()
        end
        return
    elseif event == "ITEM_TEXT_READY" then
        if QuestReaderAddonDB.autoPlayEnabled then
            PlayItemAudio()
        end
        return
    elseif event == "ITEM_TEXT_CLOSED" then
        currentItemName = nil
        currentItemPage = 1
        if itemAudioTimer then
            itemAudioTimer:Cancel()
            itemAudioTimer = nil
        end
        if QuestReaderAddonDB.stopDialogueOnClose then
            StopCurrentSound()
        end
        return
    end

    -- Map event to appropriate textType
    local textType = ""
    if event == "QUEST_DETAIL" then
        textType = "description"
    elseif event == "QUEST_PROGRESS" then
        textType = "progress"
    elseif event == "QUEST_COMPLETE" then
        textType = "completion"
    end

    if textType ~= "" and QuestReaderAddonDB.autoPlayEnabled then
        if QuestMapFrame and QuestMapFrame:IsVisible() and not (QuestFrame and QuestFrame:IsVisible()) then
            if not QuestReaderAddonDB.autoPlayInQuestMap then
                lastTextType = textType
                return
            end
        end
        PlayQuestAudio(textType)  -- Call PlayQuestAudio with textType from event
    elseif event == "QUEST_FINISHED" and QuestReaderAddonDB.stopDialogueOnClose then
        StopCurrentSound() -- Stop sound when the quest dialog finishes
    end

    lastTextType = textType
end)

lastTextType = textType
local logoutFrame = CreateFrame("Frame")
logoutFrame:RegisterEvent("PLAYER_LOGOUT")
logoutFrame:SetScript("OnEvent", OnPlayerLogout)

-- Slash command to toggle auto-play
SLASH_QUESTREADERAUTO1 = '/qrauto'
SlashCmdList["QUESTREADERAUTO"] = function(msg)
    if msg == "on" then
        QuestReaderAddonDB.autoPlayEnabled = true
    elseif msg == "off" then
        QuestReaderAddonDB.autoPlayEnabled = false
    else
        QuestReaderAddonDB.autoPlayEnabled = not QuestReaderAddonDB.autoPlayEnabled
    end
    print("Quest Reader Auto-Play: " .. (QuestReaderAddonDB.autoPlayEnabled and "Enabled" or "Disabled"))
end

-- Frame for copy-pasting missing quests
local missingCopyFrame = CreateFrame("Frame", "QuestReaderMissingCopyFrame", UIParent, "BasicFrameTemplateWithInset")
missingCopyFrame:SetSize(350, 400)
missingCopyFrame:SetPoint("CENTER")
missingCopyFrame:SetMovable(true)
missingCopyFrame:EnableMouse(true)
missingCopyFrame:RegisterForDrag("LeftButton")
missingCopyFrame:SetScript("OnDragStart", missingCopyFrame.StartMoving)
missingCopyFrame:SetScript("OnDragStop", missingCopyFrame.StopMovingOrSizing)
missingCopyFrame:Hide()

-- Set frame title
missingCopyFrame.TitleText:SetText("Missing Quest Audio")

-- ScrollFrame for the EditBox
local scrollFrame = CreateFrame("ScrollFrame", nil, missingCopyFrame, "UIPanelScrollFrameTemplate")
scrollFrame:SetPoint("TOPLEFT", missingCopyFrame.InsetBg, "TOPLEFT", 5, -5)
scrollFrame:SetPoint("BOTTOMRIGHT", missingCopyFrame.InsetBg, "BOTTOMRIGHT", -27, 5)

-- Multi-line EditBox
local editBox = CreateFrame("EditBox", nil, scrollFrame)
editBox:SetSize(scrollFrame:GetSize())
editBox:SetMultiLine(true)
editBox:SetAutoFocus(true)
editBox:SetFontObject("ChatFontNormal")
editBox:SetScript("OnEscapePressed", function(self) missingCopyFrame:Hide() end)
scrollFrame:SetScrollChild(editBox)

-- Slash command to list quest audio missing from the installed sound packs.
SLASH_QUESTREADERMISSING1 = '/qrmissing'
SlashCmdList["QUESTREADERMISSING"] = function()
    local missing = {}
    for soundFile in pairs(addon.reportedMissing) do
        table.insert(missing, soundFile)
    end
    table.sort(missing)

    if #missing == 0 then
        print("Quest Reader: no missing quest audio recorded this session.")
        return
    end

    local text = ""
    for _, soundFile in ipairs(missing) do
        text = text .. soundFile .. "\n"
    end
    
    editBox:SetText(text)
    missingCopyFrame:Show()
    editBox:HighlightText()
    
    print("Quest Reader: Opened window with " .. #missing .. " missing quest audio file(s).")
end

local function SaveMinimapIconPosition()
    local position = icon:GetMinimapButton("QuestReaderAddon"):GetPoint()
    QuestReaderAddonDB.minimapIconPosition.point, _, QuestReaderAddonDB.minimapIconPosition.relPoint, QuestReaderAddonDB.minimapIconPosition.x, QuestReaderAddonDB.minimapIconPosition.y = position
end

local minimapIcon = icon:GetMinimapButton("QuestReaderAddon")
if minimapIcon then
    minimapIcon:HookScript("OnDragStop", SaveMinimapIconPosition)
    minimapIcon:HookScript("OnMouseUp", SaveMinimapIconPosition)
end

-- Function to mute the dialog channel
function MuteDialogChannel()
    -- Check if the original volume has already been saved
    if not originalDialogVolume then
        -- Get the current dialog volume and store it
        originalDialogVolume = GetCVar("Sound_DialogVolume")
    end
    
    -- Set the dialog volume to 0 (mute)
    SetCVar("Sound_DialogVolume", 0)
end

-- Function to unmute the dialog channel
function UnmuteDialogChannel()
    if originalDialogVolume then
        -- Restore the original dialog volume
        SetCVar("Sound_DialogVolume", originalDialogVolume)
        
        -- Clear the original volume to avoid accidental overwriting in future
        originalDialogVolume = nil
    end
end
