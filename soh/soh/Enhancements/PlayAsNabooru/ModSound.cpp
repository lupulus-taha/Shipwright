// Sound files for the Play as Nabooru mod.
//
// The game's sounds are notes of its sound fonts, played by scripts in its sequences: a new sound needs a new
// instrument, a new script and a new sound id. For a handful of recordings that is a lot of machinery, so these go
// round it: a .wav from a mod archive is decoded into memory and mixed into the audio the game has synthesized, on
// its way to the sound card (OTRAudio_Thread calls ModSound_Mix).
//
// A sound is placed like the game places its own: louder the nearer its source is to the camera, and panned to the
// side it is on. The volume sliders for everything and for sound effects apply.

#include "ModSound.h"

#include <libultraship/bridge/consolevariablebridge.h>
#include <ship/Context.h>
#include <ship/resource/ResourceManager.h>
#include <ship/resource/archive/ArchiveManager.h>
#include <dr_wav.h> // (implemented in resource/importer/AudioSampleFactory.cpp)
#include <algorithm>
#include <cmath>
#include <deque>
#include <mutex>
#include <vector>

#include "soh/cvar_prefixes.h"
#include "soh/Enhancements/audio/AudioSettings.h"

namespace {

constexpr float kOutputRate = 32000.0f; // the game's audio
// How loud a recording at full scale is next to the game's own sounds, at the same volume settings.
constexpr float kLevel = 0.7f;
constexpr int kVoices = 8;
// volume changes are spread over a few milliseconds, so that they do not click
constexpr float kGainStep = 1.0f / 256.0f;

struct Sound {
    std::vector<int16_t> samples; // mono
    float rate;
};

struct Voice {
    const Sound* sound = nullptr; // nullptr: free
    int id = 0;
    double at = 0.0;   // where in the sound it is, in samples
    double step = 1.0; // samples of the sound per sample of output
    float volume = 1.0f;
    float range = 1.0f;
    float left = 0.0f, right = 0.0f;             // how loud it is now
    float targetLeft = 0.0f, targetRight = 0.0f; // and is to be
    bool stopping = false;
};

std::mutex sMutex; // the game thread starts and moves sounds, the audio thread mixes them
std::deque<Sound> sSounds;
Voice sVoices[kVoices];
int sNextId = 1;
int sGamePeak = 0;
int sModPeak = 0;

// How loud a source is on each side.
void Place(Voice& voice, const Vec3f* pos) {
    float distance = sqrtf(pos->x * pos->x + pos->y * pos->y + pos->z * pos->z);
    float nearness = std::clamp(1.0f - distance / voice.range, 0.0f, 1.0f);
    // -1 left .. 1 right; straight ahead and far to one side are never all on one ear
    float side = (distance > 1.0f) ? std::clamp(pos->x / distance, -1.0f, 1.0f) * 0.8f : 0.0f;
    if (AudioSettings_GetMirroredWorld()) {
        side = -side;
    }
    float angle = (side + 1.0f) * (float)M_PI / 4.0f;
    float gain = voice.volume * nearness * nearness * kLevel * AudioSettings_GetMasterVolume() *
                 (CVarGetInteger(CVAR_SETTING("Volume.SFX"), 100) / 100.0f);

    voice.targetLeft = gain * cosf(angle);
    voice.targetRight = gain * sinf(angle);
}

Voice* FindVoice(int id) {
    for (Voice& voice : sVoices) {
        if (voice.sound != nullptr && voice.id == id) {
            return &voice;
        }
    }
    return nullptr;
}

} // namespace

int ModSound_Load(const char* path) {
    auto archives = Ship::Context::GetRawInstance()->GetResourceManager()->GetArchiveManager();
    if (!archives->HasFile(path)) {
        return -1;
    }
    auto file = archives->LoadFile(path);
    if (file == nullptr || file->Buffer == nullptr) {
        return -1;
    }
    unsigned int channels = 0, rate = 0;
    drwav_uint64 frames = 0;
    drwav_int16* data = drwav_open_memory_and_read_pcm_frames_s16(file->Buffer->data(), file->Buffer->size(),
                                                                  &channels, &rate, &frames, nullptr);
    if (data == nullptr || channels == 0 || frames == 0) {
        drwav_free(data, nullptr);
        return -1;
    }
    Sound sound;
    sound.rate = (float)rate;
    sound.samples.resize(frames);
    for (drwav_uint64 i = 0; i < frames; i++) {
        int sum = 0;
        for (unsigned int channel = 0; channel < channels; channel++) {
            sum += data[i * channels + channel];
        }
        sound.samples[i] = (int16_t)(sum / (int)channels);
    }
    drwav_free(data, nullptr);

    std::lock_guard<std::mutex> lock(sMutex);
    sSounds.push_back(std::move(sound));
    return (int)sSounds.size() - 1;
}

int ModSound_Play(int sound, const Vec3f* projectedPos, float volume, float range, float pitch) {
    std::lock_guard<std::mutex> lock(sMutex);
    if (sound < 0 || sound >= (int)sSounds.size()) {
        return 0;
    }
    // a free voice, or else the one that is nearest its end
    Voice* voice = &sVoices[0];
    double leastLeft = 1e30;
    for (Voice& candidate : sVoices) {
        if (candidate.sound == nullptr) {
            voice = &candidate;
            break;
        }
        double left = (candidate.sound->samples.size() - candidate.at) / candidate.step;
        if (left < leastLeft) {
            leastLeft = left;
            voice = &candidate;
        }
    }
    *voice = Voice();
    voice->sound = &sSounds[sound];
    voice->id = sNextId++;
    voice->step = voice->sound->rate / kOutputRate * pitch;
    voice->volume = volume;
    voice->range = range;
    Place(*voice, projectedPos);
    voice->left = voice->targetLeft;
    voice->right = voice->targetRight;
    return voice->id;
}

void ModSound_Move(int playing, const Vec3f* projectedPos) {
    std::lock_guard<std::mutex> lock(sMutex);
    Voice* voice = FindVoice(playing);
    if (voice != nullptr && !voice->stopping) {
        Place(*voice, projectedPos);
    }
}

void ModSound_Stop(int playing) {
    std::lock_guard<std::mutex> lock(sMutex);
    Voice* voice = FindVoice(playing);
    if (voice != nullptr) {
        voice->stopping = true;
        voice->targetLeft = voice->targetRight = 0.0f;
    }
}

void ModSound_Mix(int16_t* samples, uint32_t frames) {
    std::lock_guard<std::mutex> lock(sMutex);
    for (uint32_t i = 0; i < frames * 2; i++) {
        sGamePeak = std::max(sGamePeak, std::abs((int)samples[i]));
    }
    for (Voice& voice : sVoices) {
        if (voice.sound == nullptr) {
            continue;
        }
        const std::vector<int16_t>& data = voice.sound->samples;
        for (uint32_t i = 0; i < frames; i++) {
            size_t index = (size_t)voice.at;
            if (index + 1 >= data.size() || (voice.stopping && voice.left + voice.right < 0.0005f)) {
                voice.sound = nullptr;
                break;
            }
            float between = (float)(voice.at - (double)index);
            float value = data[index] + (data[index + 1] - data[index]) * between;
            voice.left += (voice.targetLeft - voice.left) * kGainStep;
            voice.right += (voice.targetRight - voice.right) * kGainStep;
            int left = (int)(value * voice.left);
            int right = (int)(value * voice.right);
            sModPeak = std::max({ sModPeak, std::abs(left), std::abs(right) });
            samples[i * 2] = (int16_t)std::clamp(samples[i * 2] + left, -32768, 32767);
            samples[i * 2 + 1] = (int16_t)std::clamp(samples[i * 2 + 1] + right, -32768, 32767);
            voice.at += voice.step;
        }
    }
}

void ModSound_DebugPeaks(int* game, int* mod) {
    std::lock_guard<std::mutex> lock(sMutex);
    *game = sGamePeak;
    *mod = sModPeak;
    sGamePeak = sModPeak = 0;
}
