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
    -- Text differs per language, so a capture is only meaningful alongside the
    -- locale it came from.
    QuestReaderHarvesterDB.locale = GetLocale()
    QuestReaderHarvesterDB.build = select(2, GetBuildInfo())
end

-- "Creature-0-<server>-<instance>-<zone>-<creatureID>-<spawn>"
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

local function CurrentSpeaker()
    -- During a quest frame the quest giver is the interacted unit, so the
    -- speaker comes for free alongside the text.
    return {
        id = CreatureIDFromGUID(UnitGUID("npc")),
        name = UnitName("npc"),
    }
end

local function RecordPassage(questID, passage, text)
    if not questID or questID == 0 or not text or text == "" then
        return
    end

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

local harvester = CreateFrame("Frame")
harvester:RegisterEvent("ADDON_LOADED")
harvester:RegisterEvent("QUEST_DETAIL")
harvester:RegisterEvent("QUEST_PROGRESS")
harvester:RegisterEvent("QUEST_COMPLETE")

harvester:SetScript("OnEvent", function(self, event, loadedAddon)
    if event == "ADDON_LOADED" then
        if loadedAddon == addonName then
            InitializeDB()
            self:UnregisterEvent("ADDON_LOADED")
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

SLASH_QUESTREADERHARVEST1 = "/qrharvest"
SlashCmdList["QUESTREADERHARVEST"] = function(msg)
    if msg == "wipe" then
        QuestReaderHarvesterDB.quests = {}
        print("Quest Reader Harvester: cleared.")
        return
    end

    local quests, passages, unvoiced = CountCaptures()
    print("Quest Reader Harvester: " .. quests .. " quest(s), "
          .. passages .. " passage(s) captured.")
    if unvoiced > 0 then
        -- A passage with no creature ID cannot be matched to a voice later.
        print("  " .. unvoiced .. " passage(s) have no NPC recorded "
              .. "(offered by an object or auto-accepted).")
    end
    print("  Data is written to SavedVariables on logout or /reload.")
end
