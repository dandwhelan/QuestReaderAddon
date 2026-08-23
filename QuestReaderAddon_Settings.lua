local addonName, addon = ...
local QuestReader = {}

EventUtil.ContinueOnAddOnLoaded(addonName, function()
    QuestReaderAddonDB = QuestReaderAddonDB or {}
    QuestReader:CreateSettings()
end)

local function OpenAudioLibraryUI()
    if QuestReaderAudioLibraryUI then
        QuestReaderAudioLibraryUI:Show()
        QuestReaderAudioLibraryUI:PopulateList()
    else
        print("Quest Audio Library UI is not available.")
    end
end

function QuestReader:CreateSettings()
    local optionsFrame
    optionsFrame = CreateFrame("Frame", nil, nil, "VerticalLayoutFrame")
    optionsFrame.spacing = 4
    local category, layout = Settings.RegisterCanvasLayoutCategory(optionsFrame, "SpeakStone Narration |T" .. addonName .. "\\cs_icon:18:18:0:0|t")
    addon.settingsCategoryID = category.ID
    Settings.RegisterAddOnCategory(category)

    local layoutIndex = 0
    local function GetLayoutIndex()
        layoutIndex = layoutIndex + 1
        return layoutIndex
    end

    -- Header
    local Header = CreateFrame("Frame", nil, optionsFrame)
    Header:SetSize(150, 50)
    local headerText = Header:CreateFontString(nil, "ARTWORK", "GameFontHighlightHuge")
    headerText:SetPoint("TOPLEFT", 7, -22)
    headerText:SetText("SpeakStone Narration")
    local divider = Header:CreateTexture(nil, "ARTWORK")
    divider:SetAtlas("Options_HorizontalDivider", true)
    divider:SetPoint("BOTTOMLEFT", -50)
    Header.layoutIndex = GetLayoutIndex()
    Header.bottomPadding = 10

    local function makeCheckButton(name, text)
        local checkButton = CreateFrame("CheckButton", addonName .. "CheckBox_" .. name, optionsFrame, "SettingsCheckBoxTemplate")
        checkButton.text = checkButton:CreateFontString(nil, "ARTWORK", "GameFontNormal")
        checkButton.text:SetText(text)
        checkButton.text:SetPoint("LEFT", checkButton, "RIGHT", 4, 0)
        checkButton:SetSize(21, 20)
        return checkButton
    end

    local settingsInfo = {
        { option = "autoPlayEnabled", detail = "Auto-play quest audio" },
        { option = "autoPlayInQuestMap", detail = "Auto-play in Quest Map (Lore Maps)" },
        { option = "showMinimapButton", detail = "Show minimap button" },
        { option = "showDebugMessages", detail = "Show debug messages in chat" },
        { option = "muteGossip", detail = "Mute greetings (instant autoplay)" },
        { option = "stopDialogueOnClose", detail = "Stop Dialogue when closing Quest window" },
    }

    for _, keyInfo in ipairs(settingsInfo) do
        local checkButton = makeCheckButton(keyInfo.option, keyInfo.detail)
        checkButton.layoutIndex = GetLayoutIndex()
        checkButton:SetHitRectInsets(0, -checkButton.text:GetWidth(), 0, 0)
        checkButton.HoverBackground = nil
        checkButton:SetChecked(QuestReaderAddonDB[keyInfo.option])
        checkButton:SetScript("OnClick", function(self)
            QuestReaderAddonDB[keyInfo.option] = self:GetChecked()
            if keyInfo.option == "showMinimapButton" then
                addon.UpdateMinimapButtonVisibility()
            end
        end)
        checkButton:SetScript("OnShow", function(self)
            self:SetChecked(QuestReaderAddonDB[keyInfo.option])
        end)
    end

StaticPopupDialogs["QUESTREADER_CONFIRM_CLEAR_HARVEST"] = {
    text = "Are you sure you want to clear all harvested quest, gossip, and book data? This cannot be undone.",
    button1 = YES,
    button2 = NO,
    OnAccept = function()
        if SlashCmdList["QUESTREADERHARVEST"] then
            SlashCmdList["QUESTREADERHARVEST"]("wipe")
        elseif QuestReaderHarvesterDB then
            QuestReaderHarvesterDB.quests = {}
            QuestReaderHarvesterDB.gossip = {}
            QuestReaderHarvesterDB.itemText = {}
            print("SpeakStone Harvester: cleared.")
        end
    end,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    preferredIndex = 3,
}

    -- Add the Open Audio Library button
    local openLibraryButton = CreateFrame("Button", nil, optionsFrame, "UIPanelButtonTemplate")
    openLibraryButton:SetText("Open Audio Library")
    openLibraryButton:SetSize(160, 25)
    openLibraryButton.layoutIndex = GetLayoutIndex()
    openLibraryButton:SetScript("OnClick", OpenAudioLibraryUI)

    -- Harvest buttons container (Export & Clear side-by-side)
    local harvestButtonGroup = CreateFrame("Frame", nil, optionsFrame)
    harvestButtonGroup:SetSize(330, 25)
    harvestButtonGroup.layoutIndex = GetLayoutIndex()

    local exportHarvestButton = CreateFrame("Button", nil, harvestButtonGroup, "UIPanelButtonTemplate")
    exportHarvestButton:SetText("Export Harvested Data")
    exportHarvestButton:SetSize(160, 25)
    exportHarvestButton:SetPoint("LEFT", harvestButtonGroup, "LEFT", 0, 0)
    exportHarvestButton:SetScript("OnClick", function()
        if SlashCmdList["QUESTREADERHARVEST"] then
            SlashCmdList["QUESTREADERHARVEST"]("export")
        else
            print("SpeakStone Narration: SpeakStone Harvester addon is not enabled.")
        end
    end)

    local clearHarvestButton = CreateFrame("Button", nil, harvestButtonGroup, "UIPanelButtonTemplate")
    clearHarvestButton:SetText("Clear Harvested Data")
    clearHarvestButton:SetSize(160, 25)
    clearHarvestButton:SetPoint("LEFT", exportHarvestButton, "RIGHT", 10, 0)
    clearHarvestButton:SetScript("OnClick", function()
        if SlashCmdList["QUESTREADERHARVEST"] or QuestReaderHarvesterDB then
            StaticPopup_Show("QUESTREADER_CONFIRM_CLEAR_HARVEST")
        else
            print("SpeakStone Narration: SpeakStone Harvester addon is not enabled.")
        end
    end)

    optionsFrame:Layout()
end

-- Function to open settings
function addon:OpenSettings()
    if addon.settingsCategoryID then
        Settings.OpenToCategory(addon.settingsCategoryID)
    end
end

SLASH_QUESTREADER1, SLASH_QUESTREADER2, SLASH_QUESTREADER3, SLASH_QUESTREADER4 = '/qr', '/questreader', '/ss', '/speakstone'
SlashCmdList.QUESTREADER = function(msg)
    if addon.settingsCategoryID then
        Settings.OpenToCategory(addon.settingsCategoryID)
    end
end
