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

local harvester = CreateFrame("Frame")
harvester:RegisterEvent("ADDON_LOADED")
harvester:RegisterEvent("QUEST_DETAIL")
harvester:RegisterEvent("QUEST_PROGRESS")
harvester:RegisterEvent("QUEST_COMPLETE")
harvester:RegisterEvent("GOSSIP_SHOW")

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

SLASH_QUESTREADERHARVEST1 = "/qrharvest"
SlashCmdList["QUESTREADERHARVEST"] = function(msg)
    if msg == "wipe" then
        QuestReaderHarvesterDB.quests = {}
        QuestReaderHarvesterDB.gossip = {}
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

    local gossipNPCs, gossipTexts = CountGossip()
    print("  " .. gossipNPCs .. " NPC(s), " .. gossipTexts
          .. " gossip line(s) captured.")
    print("  Data is written to SavedVariables on logout or /reload.")
end
