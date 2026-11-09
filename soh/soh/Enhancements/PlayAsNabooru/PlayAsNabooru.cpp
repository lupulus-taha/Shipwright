// Play as Nabooru
//
// Milestone 1: Link's player actor keeps running all game logic (movement, collision, camera, items, hitboxes),
// but his model is hidden and Nabooru is drawn in his place with her own skeleton, proportions and animations.
// Link's (invisible) skeleton still animates, because his hand/sword positions drive hitboxes and held items.

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include "soh/frame_interpolation.h" // gives OPEN_DISPS' interpolation calls C linkage in this C++ file

extern "C" {
#include "z64.h"
#include "macros.h"
#include "functions.h"
#include "variables.h"
#include "objects/object_nb/object_nb.h"
#include "overlays/actors/ovl_En_Nb/z_en_nb.h"
extern PlayState* gPlayState;
void ResourceMgr_UnregisterSkeleton(SkelAnime* skelAnime);
}

#define CVAR_PLAY_AS_NABOORU_NAME CVAR_ENHANCEMENT("PlayAsNabooru")
#define CVAR_PLAY_AS_NABOORU_VALUE CVarGetInteger(CVAR_PLAY_AS_NABOORU_NAME, 0)

static void PlayAsNabooru_DrawModel();

namespace {

enum class NabooruAnim { None, Idle, Walk };

// Player speed (units per frame) at which her walk cycle plays at its natural speed.
constexpr f32 kWalkReferenceSpeed = 3.0f;
constexpr f32 kMovingThreshold = 0.5f;
constexpr f32 kMorphFrames = -6.0f;

SkelAnime sSkelAnime;
Vec3s sJointTable[NB_LIMB_MAX];
Vec3s sMorphTable[NB_LIMB_MAX];
bool sSkeletonReady = false;
NabooruAnim sCurrentAnim = NabooruAnim::None;

s16 sBlinkTimer = 0;
s16 sEyeIndex = 0;
const char* sEyeTextures[] = { gNabooruEyeOpenTex, gNabooruEyeHalfTex, gNabooruEyeClosedTex };

AnimationHeader* GetAnimation(NabooruAnim anim) {
    switch (anim) {
        case NabooruAnim::Walk:
            return (AnimationHeader*)gNabooruWalkingAnim;
        case NabooruAnim::Idle:
        default:
            return (AnimationHeader*)gNabooruStandingHandsOnHipsAnim;
    }
}

void EnsureSkeleton() {
    if (sSkeletonReady || gPlayState == nullptr) {
        return;
    }
    SkelAnime_InitFlex(gPlayState, &sSkelAnime, (FlexSkeletonHeader*)gNabooruSkel, NULL, sJointTable, sMorphTable,
                       NB_LIMB_MAX);
    sCurrentAnim = NabooruAnim::None;
    sSkeletonReady = true;
}

void ReleaseSkeleton() {
    if (sSkeletonReady) {
        ResourceMgr_UnregisterSkeleton(&sSkelAnime);
        sSkeletonReady = false;
    }
}

void ChangeAnimation(NabooruAnim anim) {
    if (anim == sCurrentAnim) {
        return;
    }
    AnimationHeader* header = GetAnimation(anim);
    Animation_Change(&sSkelAnime, header, 1.0f, 0.0f, Animation_GetLastFrame(header), ANIMMODE_LOOP,
                     sCurrentAnim == NabooruAnim::None ? 0.0f : kMorphFrames);
    sCurrentAnim = anim;
}

void UpdateEyes() {
    if (DECR(sBlinkTimer) == 0) {
        sBlinkTimer = Rand_S16Offset(60, 60);
    }
    sEyeIndex = (sBlinkTimer < ARRAY_COUNT(sEyeTextures)) ? sBlinkTimer : 0;
}

void Nabooru_OnPlayerUpdate() {
    Player* player = GET_PLAYER(gPlayState);
    if (player == nullptr) {
        return;
    }
    EnsureSkeleton();

    f32 speed = player->actor.speedXZ;
    bool moving = speed > kMovingThreshold;
    ChangeAnimation(moving ? NabooruAnim::Walk : NabooruAnim::Idle);
    sSkelAnime.playSpeed = moving ? CLAMP(speed / kWalkReferenceSpeed, 0.5f, 3.0f) : 1.0f;

    SkelAnime_Update(&sSkelAnime);
    UpdateEyes();
}

void Nabooru_OnPlayerDraw(bool isFirstPerson) {
    if (isFirstPerson || !sSkeletonReady || gPlayState == nullptr) {
        return;
    }
    ::PlayAsNabooru_DrawModel();
}

void RegisterPlayAsNabooru() {
    bool enabled = CVAR_PLAY_AS_NABOORU_VALUE;

    if (!enabled) {
        ReleaseSkeleton();
    }

    COND_HOOK(OnPlayerUpdate, enabled, Nabooru_OnPlayerUpdate);
    COND_HOOK(OnPlayerDraw, enabled, Nabooru_OnPlayerDraw);
    COND_HOOK(OnPlayDestroy, enabled, ReleaseSkeleton);

    // Hide Link's geometry while keeping his skeleton (and everything computed from it) running.
    COND_VB_SHOULD(VB_PLAYER_OVERRIDE_LIMB_DRAW, enabled, {
        va_arg(args, s32); // limbIndex
        Gfx** dList = va_arg(args, Gfx**);
        Player* player = (Player*)va_arg(args, void*);
        // Keep Link's first-person arms/weapons until she has her own.
        if (player->unk_6AD != 0) {
            return;
        }
        *dList = NULL;
    });
}

static RegisterShipInitFunc initFunc(RegisterPlayAsNabooru, { CVAR_PLAY_AS_NABOORU_NAME });

} // namespace

// Kept outside the anonymous namespace: OPEN_DISPS/CLOSE_DISPS declare the frame-interpolation functions at block
// scope, and those declarations must resolve to the global extern "C" ones from frame_interpolation.h.
// Records her ankles as the player's feet, so the foot shadows (drawn right after the player) follow her, not
// Link's invisible skeleton.
static void PlayAsNabooru_PostLimbDraw(PlayState* play, s32 limbIndex, Gfx** dList, Vec3s* rot, void* data) {
    static Vec3f sAnkle = { 0.0f, 0.0f, 0.0f };
    Actor_SetFeetPos((Actor*)data, limbIndex, NB_LIMB_L_FOOT, &sAnkle, NB_LIMB_R_FOOT, &sAnkle);
}

static void PlayAsNabooru_DrawModel() {
    Player* player = GET_PLAYER(gPlayState);

    OPEN_DISPS(gPlayState->state.gfxCtx);

    Gfx_SetupDL_25Opa(gPlayState->state.gfxCtx);
    gSPSegment(POLY_OPA_DISP++, 0x08, (uintptr_t)sEyeTextures[sEyeIndex]);
    gSPSegment(POLY_OPA_DISP++, 0x09, (uintptr_t)sEyeTextures[sEyeIndex]);
    gDPSetEnvColor(POLY_OPA_DISP++, 0, 0, 0, 255);
    gSPSegment(POLY_OPA_DISP++, 0x0C, (uintptr_t)&D_80116280[2]);
    // The player actor's model matrix (position, facing, 0.01 scale) is still current here.
    SkelAnime_DrawFlexOpa(gPlayState, sSkelAnime.skeleton, sSkelAnime.jointTable, sSkelAnime.dListCount, NULL,
                          PlayAsNabooru_PostLimbDraw, &player->actor);

    CLOSE_DISPS(gPlayState->state.gfxCtx);
}
