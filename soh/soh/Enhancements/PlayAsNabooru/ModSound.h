// Sound files of the Play as Nabooru mod, played next to the game's own audio (see ModSound.cpp).

#ifndef PLAY_AS_NABOORU_MOD_SOUND_H
#define PLAY_AS_NABOORU_MOD_SOUND_H

#include <stdint.h>
#include "z64math.h"

#ifdef __cplusplus
extern "C" {
#endif

// Loads a .wav from the archives. Returns its number, or -1 if no archive has it (or it is not a wav).
int ModSound_Load(const char* path);
// Plays a sound coming from somewhere in the world. `projectedPos` is the source's place as seen from the camera (an
// actor's projectedPos), `range` how far it carries; `pitch` 1 plays it as recorded. Returns the playing sound's
// number for the calls below (0: nothing is playing).
int ModSound_Play(int sound, const Vec3f* projectedPos, float volume, float range, float pitch);
// Keeps a playing sound at its source, which may have moved (and the camera with it).
void ModSound_Move(int playing, const Vec3f* projectedPos);
// Fades a playing sound out.
void ModSound_Stop(int playing);
// The audio thread adds what is playing to a buffer the game has filled (stereo, 32 kHz).
void ModSound_Mix(int16_t* samples, uint32_t frames);
// For tools: the loudest sample of the game's own audio and of these sounds since the last call.
void ModSound_DebugPeaks(int* game, int* mod);

#ifdef __cplusplus
}
#endif

#endif
