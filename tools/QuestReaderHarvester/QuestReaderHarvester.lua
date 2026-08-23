-- Collects the quest text and speaker needed to generate quest voiceovers.
--
-- Quest description, progress and completion text is delivered by the server at
-- runtime and is not present in the client database, so it cannot be datamined.
-- It can, however, be read from the live UI through the public quest API. This
-- addon records it as the player encounters it, along with the NPC who speaks
-- each passage, and stores the result in SavedVariables for extraction.
--
-- The speaker is recorded per passage rather than per quest: the NPC who offers
-- a quest is frequently not the one who takes it back, and voicing both with the
-- giver's voice is a mistake that is expensive to correct after generation.

local addonName = ...

QuestReaderHarvesterDB = QuestReaderHarvesterDB or {}

local function InitializeDB()
    QuestReaderHarvesterDB = QuestReaderHarvesterDB or {}
    QuestReaderHarvesterDB.quests = QuestReaderHarvesterDB.quests or {}
    QuestReaderHarvesterDB.gossip = QuestReaderHarvesterDB.gossip or {}
    QuestReaderHarvesterDB.itemText = QuestReaderHarvesterDB.itemText or {}
    -- Text differs per language, so a capture is only meaningful alongside the
    -- locale it came from.
    QuestReaderHarvesterDB.locale = GetLocale()
    QuestReaderHarvesterDB.build = select(2, GetBuildInfo())
end

local function IsSecret(val)
    if val == nil then return false end
    if issecretvalue and issecretvalue(val) then return true end
    if SecretUtil and SecretUtil.IsSecretValue and SecretUtil.IsSecretValue(val) then return true end
    return false
end

-- "Creature-0-<server>-<instance>-<zone>-<creatureID>-<spawn>"
local function CreatureIDFromGUID(guid)
    if not guid or IsSecret(guid) or type(guid) ~= "string" then
        return nil
    end
    local ok, unitType, _, _, _, creatureID = pcall(strsplit, "-", guid)
    if ok and (unitType == "Creature" or unitType == "Vehicle") then
        return tonumber(creatureID)
    end
    return nil
end

local function CurrentSpeaker()
    -- During a quest frame the quest giver is the interacted unit, so the
    -- speaker comes for free alongside the text.
    local guid = UnitGUID("npc")
    local name = UnitName("npc")
    if IsSecret(guid) then
        guid = nil
    end
    if IsSecret(name) then
        name = nil
    end
    return {
        id = CreatureIDFromGUID(guid),
        name = name,
    }
end

-- Server text arrives with the player's own identity already substituted into
-- it: "$n" has become the character's name before the addon ever sees the
-- string. Left alone that has three costs. Two players' captures of the same
-- line are different text, so they can never confirm each other. A stranger's
-- character name gets baked into generated audio. And the name travels to
-- whoever the capture is shared with, for no purpose.
--
-- The name is put back as "$n" at capture time, so it never reaches
-- SavedVariables at all and nothing downstream has to know it existed.
--
-- Only the name is reverted here. Class and race substitutions are just as
-- real -- a Silvermoon Guard greets a priest as "priest", which is $c -- but
-- words like "priest" also occur naturally in quest text, and a single
-- capture cannot tell the two apart. Guessing would corrupt genuine lines, so
-- class and race travel as metadata instead (see PlayerMetadata) and are
-- resolved where several players' captures of one line can be compared.
local playerNamePattern = nil

local function EscapePattern(s)
    return (s:gsub("(%W)", "%%%1"))
end

local function Detokenize(text)
    if not text or text == "" then
        return text
    end
    if not playerNamePattern then
        local name = UnitName("player")
        if not name or name == "" then
            return text
        end
        local ok, escaped = pcall(EscapePattern, name)
        if ok and escaped then
            playerNamePattern = escaped
        else
            return text
        end
    end
    local ok, res = pcall(function() return (text:gsub(playerNamePattern, "$n")) end)
    if ok and res then
        return res
    end
    return text
end

-- Race, class and gender decide which branch of $r/$c/$g the server rendered
-- into a line, so a capture is only interpretable alongside them.
local function PlayerMetadata()
    local localizedClass, classToken = UnitClass("player")
    local localizedRace, raceToken = UnitRace("player")
    return {
        playerClass = classToken,
        playerClassName = localizedClass,
        playerRace = raceToken,
        playerRaceName = localizedRace,
        -- 1 unknown, 2 male, 3 female.
        playerSex = UnitSex("player"),
        faction = UnitFactionGroup("player"),
    }
end

local function RecordPassage(questID, passage, text)
    if not questID or questID == 0 or not text or text == "" then
        return
    end

    text = Detokenize(text)

    local quests = QuestReaderHarvesterDB.quests
    local entry = quests[questID]
    if not entry then
        entry = {}
        quests[questID] = entry
    end

    entry.title = GetTitleText() or entry.title

    local speaker = CurrentSpeaker()
    entry[passage] = {
        text = text,
        npcID = speaker.id,
        npcName = speaker.name,
    }
end

-- Gossip greeting text is server-delivered like quest text, and unlike quest
-- text is keyed by nothing at all — there is no questID for an NPC that gives
-- no quest, only the creature ID the greeting came from. A gossip-only NPC
-- has no questID, so entries are keyed on npcID directly rather than folded
-- into the quest table.
--
-- Some NPCs cycle between a handful of greeting variants, so a capture that
-- overwrote the last one would lose the rest on every revisit. Distinct
-- texts are kept as a list instead, deduplicated by exact match so replaying
-- the same greeting does not grow it unbounded.
local function RecordGossip(npcID, npcName, text)
    if not npcID or not text or text == "" then
        return
    end

    -- Before the duplicate check, not after: two greetings that differ only by
    -- the player's name are the same greeting, and comparing the raw strings
    -- would store both.
    text = Detokenize(text)

    local gossip = QuestReaderHarvesterDB.gossip
    local entry = gossip[npcID]
    if not entry then
        entry = { npcName = npcName, texts = {} }
        gossip[npcID] = entry
    end
    entry.npcName = npcName or entry.npcName

    for _, existing in ipairs(entry.texts) do
        if existing == text then
            return
        end
    end
    table.insert(entry.texts, text)
end

-- Books, letters, scrolls, and dungeon objects read through ItemTextFrame.
local function RecordItemText(itemID, itemName, page, text)
    if not text or text == "" then
        return
    end

    text = Detokenize(text)

    local items = QuestReaderHarvesterDB.itemText
    -- Plaques and world objects are read through the same frame as books but
    -- carry no item link, so they can only be keyed by name. Both key shapes
    -- have to be accepted; the ID is recorded whenever it is known so the two
    -- can be reconciled later.
    local key = itemID or itemName or "unknown"
    local entry = items[key]
    if not entry then
        entry = { itemID = itemID, itemName = itemName, pages = {} }
        items[key] = entry
    end
    entry.itemName = itemName or entry.itemName
    entry.itemID = itemID or entry.itemID
    entry.pages[page or 1] = text
end

local function ShouldPrintDebug()
    if QuestReaderAddonDB and QuestReaderAddonDB.showDebugMessages ~= nil then
        return QuestReaderAddonDB.showDebugMessages
    end
    if QuestReaderHarvesterDB and QuestReaderHarvesterDB.showDebugMessages ~= nil then
        return QuestReaderHarvesterDB.showDebugMessages
    end
    return false
end

local harvester = CreateFrame("Frame")
harvester:RegisterEvent("ADDON_LOADED")
harvester:RegisterEvent("QUEST_DETAIL")
harvester:RegisterEvent("QUEST_PROGRESS")
harvester:RegisterEvent("QUEST_COMPLETE")
harvester:RegisterEvent("GOSSIP_SHOW")
harvester:RegisterEvent("ITEM_TEXT_READY")

harvester:SetScript("OnEvent", function(self, event, loadedAddon)
    if event == "ADDON_LOADED" then
        if loadedAddon == addonName then
            InitializeDB()
            self:UnregisterEvent("ADDON_LOADED")
        end
        return
    end

    if event == "GOSSIP_SHOW" then
        local speaker = CurrentSpeaker()
        local text = C_GossipInfo.GetText()
        RecordGossip(speaker.id, speaker.name, text)
        return
    end

    if event == "ITEM_TEXT_READY" then
        local itemLink = ItemTextGetItem()
        local itemID, itemName
        if itemLink then
            pcall(function()
                itemID = tonumber(itemLink:match("item:(%d+)"))
                itemName = itemLink:match("%[(.-)%]")
            end)
        end
        if not itemName then
            local ok, rawItem = pcall(ItemTextGetItem)
            if ok and type(rawItem) == "string" then
                itemName = rawItem
            end
        end
        local page = ItemTextGetPage() or 1
        local text = ItemTextGetText()
        RecordItemText(itemID, itemName, page, text)
        if ShouldPrintDebug() then
            local display = "item"
            if type(itemName) == "string" then
                pcall(function() display = itemName end)
            end
            print("SpeakStone Harvester: captured '" .. display .. "' (Page " .. page .. ")")
        end
        return
    end

    local questID = GetQuestID()
    if event == "QUEST_DETAIL" then
        RecordPassage(questID, "description", GetQuestText())
    elseif event == "QUEST_PROGRESS" then
        RecordPassage(questID, "progress", GetProgressText())
    elseif event == "QUEST_COMPLETE" then
        RecordPassage(questID, "completion", GetRewardText())
    end
end)

local function CountCaptures()
    local quests, passages, unvoiced = 0, 0, 0
    for _, entry in pairs(QuestReaderHarvesterDB.quests or {}) do
        quests = quests + 1
        for _, passage in ipairs({ "description", "progress", "completion" }) do
            local captured = entry[passage]
            if captured then
                passages = passages + 1
                if not captured.npcID then
                    unvoiced = unvoiced + 1
                end
            end
        end
    end
    return quests, passages, unvoiced
end

local function CountGossip()
    local npcs, texts = 0, 0
    for _, entry in pairs(QuestReaderHarvesterDB.gossip or {}) do
        npcs = npcs + 1
        texts = texts + #entry.texts
    end
    return npcs, texts
end

local function CountItemText()
    local items, pages = 0, 0
    for _, entry in pairs(QuestReaderHarvesterDB.itemText or {}) do
        items = items + 1
        for _ in pairs(entry.pages) do
            pages = pages + 1
        end
    end
    return items, pages
end

local exportFrame = nil
local function ShowExportFrame()
    if not exportFrame then
        local f = CreateFrame("Frame", "QuestReaderHarvesterExportFrame", UIParent, "BackdropTemplate")
        f:SetSize(620, 440)
        f:SetPoint("CENTER")
        f:SetFrameStrata("FULLSCREEN_DIALOG")
        f:EnableMouse(true)
        f:SetMovable(true)
        f:RegisterForDrag("LeftButton")
        f:SetScript("OnDragStart", f.StartMoving)
        f:SetScript("OnDragStop", f.StopMovingOrSizing)

        f:SetBackdrop({
            bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
            edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
            tile = true, tileSize = 32, edgeSize = 32,
            insets = { left = 8, right = 8, top = 8, bottom = 8 }
        })

        local title = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightLarge")
        title:SetPoint("TOP", f, "TOP", 0, -16)
        title:SetText("SpeakStone Harvester — Export Data")

        local subtitle = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        subtitle:SetPoint("TOP", title, "BOTTOM", 0, -6)
        subtitle:SetText("Press Ctrl+A then Ctrl+C, then paste it at speakstone.beanw.co.uk")

        local privacy = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
        privacy:SetPoint("TOP", subtitle, "BOTTOM", 0, -4)
        privacy:SetText("Your character name is already removed from the text below.")

        local scroll = CreateFrame("ScrollFrame", "QRHExportScroll", f, "UIPanelScrollFrameTemplate")
        scroll:SetPoint("TOPLEFT", f, "TOPLEFT", 20, -80)
        scroll:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -40, 52)

        local editBox = CreateFrame("EditBox", nil, scroll)
        editBox:SetMultiLine(true)
        editBox:SetFontObject("GameFontHighlightSmall")
        editBox:SetWidth(530)
        editBox:SetAutoFocus(true)
        editBox:SetScript("OnEscapePressed", function() f:Hide() end)
        scroll:SetScrollChild(editBox)
        f.editBox = editBox

        local closeBtn = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
        closeBtn:SetSize(110, 26)
        closeBtn:SetPoint("BOTTOM", f, "BOTTOM", 0, 14)
        closeBtn:SetText("Close")
        closeBtn:SetScript("OnClick", function() f:Hide() end)

        exportFrame = f
    end

    local exportData = {
        locale = GetLocale(),
        build = select(2, GetBuildInfo()),
        addonVersion = (C_AddOns and C_AddOns.GetAddOnMetadata
                        and C_AddOns.GetAddOnMetadata(addonName, "Version")) or "unknown",
        quests = QuestReaderHarvesterDB.quests or {},
        gossip = QuestReaderHarvesterDB.gossip or {},
        itemText = QuestReaderHarvesterDB.itemText or {},
    }

    for key, value in pairs(PlayerMetadata()) do
        exportData[key] = value
    end

    local function SimpleSerialize(tbl, indent)
        indent = indent or ""
        local lines = {}
        for k, v in pairs(tbl) do
            local keyOk, keyStr = pcall(function()
                return type(k) == "number" and ("[" .. k .. "]") or ('["' .. tostring(k) .. '"]')
            end)
            if keyOk then
                if type(v) == "table" then
                    table.insert(lines, indent .. keyStr .. " = {\n" .. SimpleSerialize(v, indent .. "  ") .. indent .. "},")
                elseif type(v) == "string" then
                    local ok, escaped = pcall(function()
                        return v:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\r", "\\r"):gsub("\n", "\\n")
                    end)
                    if ok then
                        table.insert(lines, indent .. keyStr .. ' = "' .. escaped .. '",')
                    end
                elseif type(v) == "number" or type(v) == "boolean" then
                    local ok, valStr = pcall(tostring, v)
                    if ok then
                        table.insert(lines, indent .. keyStr .. " = " .. valStr .. ",")
                    end
                end
            end
        end
        return table.concat(lines, "\n") .. "\n"
    end

    local text = "QuestReaderHarvesterExport = {\n" .. SimpleSerialize(exportData, "  ") .. "}"
    exportFrame.editBox:SetText(text)
    exportFrame.editBox:HighlightText()
    exportFrame:Show()
end

SLASH_QUESTREADERHARVEST1, SLASH_QUESTREADERHARVEST2, SLASH_QUESTREADERHARVEST3 = "/qrharvest", "/ssharvest", "/speakstoneharvest"
SlashCmdList["QUESTREADERHARVEST"] = function(msg)
    if msg == "wipe" or msg == "clear" then
        QuestReaderHarvesterDB.quests = {}
        QuestReaderHarvesterDB.gossip = {}
        QuestReaderHarvesterDB.itemText = {}
        print("SpeakStone Harvester: cleared.")
        return
    elseif msg == "export" or msg == "copy" then
        ShowExportFrame()
        return
    end

    local quests, passages, unvoiced = CountCaptures()
    print("SpeakStone Harvester: " .. quests .. " quest(s), "
          .. passages .. " passage(s) captured.")
    if unvoiced > 0 then
        -- A passage with no creature ID cannot be matched to a voice later.
        print("  " .. unvoiced .. " passage(s) have no NPC recorded "
              .. "(offered by an object or auto-accepted).")
    end

    local gossipNPCs, gossipTexts = CountGossip()
    print("  " .. gossipNPCs .. " NPC(s), " .. gossipTexts
          .. " gossip line(s) captured.")

    local items, pages = CountItemText()
    print("  " .. items .. " item(s)/plaque(s), " .. pages
          .. " page(s) captured.")
    print("  Type '/ssharvest export' (or '/qrharvest export') to open copy-paste window.")
    print("  Paste it at speakstone.beanw.co.uk to have it voiced.")
    print("  Data is written to SavedVariables on logout or /reload.")
end
