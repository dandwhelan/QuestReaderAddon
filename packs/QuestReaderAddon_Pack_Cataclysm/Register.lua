local addonName = ...

-- QuestReaderAddon_RegisterSoundPack is defined at the top of the base
-- addon's QuestReaderAddon.lua. If this pack happens to load first, that
-- global does not exist yet -- fall back to the same pending-queue table
-- the base addon drains on its own load, so the merge happens whichever
-- order the two addons come up in.
if QuestReaderAddon_RegisterSoundPack then
    QuestReaderAddon_RegisterSoundPack(addonName, QuestReaderSoundLengths_Pack_Cataclysm)
else
    QuestReaderPendingSoundPacks = QuestReaderPendingSoundPacks or {}
    table.insert(QuestReaderPendingSoundPacks,
        { name = addonName, index = QuestReaderSoundLengths_Pack_Cataclysm })
end
