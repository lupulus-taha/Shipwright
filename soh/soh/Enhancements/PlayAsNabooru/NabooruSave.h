#pragma once

// What the Play as Nabooru mod keeps in a save file (the section "playAsNabooru").

// Whether Nabooru is honoured by jackals: they do not attack her, they only come to watch her (EnJackal.cpp).
// Not honoured on a new file.
bool PlayAsNabooru_IsHonouredByJackals();
void PlayAsNabooru_SetHonouredByJackals(bool honoured);
// (for the menu's checkbox)
bool* PlayAsNabooru_HonouredByJackalsFlag();
