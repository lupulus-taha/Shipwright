// What the Play as Nabooru mod keeps in a save file: its own section, "playAsNabooru", saved along with the rest of
// the file. A file from before the mod (or with the section missing) loads with everything at its default.

#include "NabooruSave.h"

#include "soh/SaveManager.h"
#include "soh/ShipInit.hpp"

namespace {

bool sHonouredByJackals = false;

// a new file, or one about to be loaded
void InitFile(bool isDebug) {
    sHonouredByJackals = false;
}

void LoadSection() {
    SaveManager::Instance->LoadData("honouredByJackals", sHonouredByJackals, false);
}

void SaveSection(const SaveContext& saveContext, int sectionID, bool fullSave) {
    SaveManager::Instance->SaveData("honouredByJackals", sHonouredByJackals);
}

void RegisterNabooruSave() {
    // (every ShipInit function runs again when a preset is applied: the save manager takes each section only once)
    static bool sRegistered = false;
    if (sRegistered) {
        return;
    }
    sRegistered = true;
    SaveManager::Instance->AddInitFunction(InitFile);
    SaveManager::Instance->AddLoadFunction("playAsNabooru", 1, LoadSection);
    SaveManager::Instance->AddSaveFunction("playAsNabooru", 1, SaveSection, true, SECTION_PARENT_NONE);
}

} // namespace

bool PlayAsNabooru_IsHonouredByJackals() {
    return sHonouredByJackals;
}

void PlayAsNabooru_SetHonouredByJackals(bool honoured) {
    sHonouredByJackals = honoured;
}

bool* PlayAsNabooru_HonouredByJackalsFlag() {
    return &sHonouredByJackals;
}

static RegisterShipInitFunc initFunc(RegisterNabooruSave, {});
