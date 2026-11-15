// The camel (Play as Nabooru mod): Nabooru's mount.
//
// The camel is one of the game's horses: En_Horse runs it (riding, steering, mounting, following, obstacles), told
// by its type where it differs from Epona (see Camel.h for how it handles; it has no carrots and does not jump).
// This file is what En_Horse cannot do for it: Epona is a skinned mesh, the camel a plain skeleton of rigid limbs
// like most of the game's creatures, so it is drawn here, and what En_Horse reads off Epona's skin after drawing
// her is read off the camel's limbs instead: where the rider sits, where its head is (its voice, the collider
// there), where its feet come down (dust).
//
// Model, skeleton and animations are made by tools/build_camel.py (Blender and Fast64) and live in camel.o2r in the
// mods folder. Without that archive there is no camel.

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/ResourceManagerHelpers.h"
#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include "soh/Enhancements/PlayAsNabooru/Camel.h"
#include "soh/Enhancements/PlayAsNabooru/ModSound.h"
#include "soh/frame_interpolation.h" // gives OPEN_DISPS' interpolation calls C linkage in this C++ file

extern "C" {
#include "z64.h"
#include "macros.h"
#include "functions.h"
#include "variables.h"
#include "overlays/actors/ovl_En_Horse/z_en_horse.h"
extern PlayState* gPlayState;
s32 Object_Spawn(ObjectContext* objectCtx, s16 objectId);
}

// Its limbs, in the order tools/build_camel.py prints (LIMB_ORDER there): every limb's children sorted by name.
enum CamelLimb {
    CAMEL_LIMB_NONE,
    CAMEL_LIMB_ROOT,
    CAMEL_LIMB_FRONT_LEG_L_A,
    CAMEL_LIMB_FRONT_LEG_L_B,
    CAMEL_LIMB_FRONT_LEG_L_C,
    CAMEL_LIMB_FRONT_LEG_L_D,
    CAMEL_LIMB_FRONT_LEG_R_A,
    CAMEL_LIMB_FRONT_LEG_R_B,
    CAMEL_LIMB_FRONT_LEG_R_C,
    CAMEL_LIMB_FRONT_LEG_R_D,
    CAMEL_LIMB_HIND_LEG_L_A,
    CAMEL_LIMB_HIND_LEG_L_B,
    CAMEL_LIMB_HIND_LEG_L_C,
    CAMEL_LIMB_HIND_LEG_L_D,
    CAMEL_LIMB_HIND_LEG_R_A,
    CAMEL_LIMB_HIND_LEG_R_B,
    CAMEL_LIMB_HIND_LEG_R_C,
    CAMEL_LIMB_HIND_LEG_R_D,
    CAMEL_LIMB_NECK_1,
    CAMEL_LIMB_NECK_2,
    CAMEL_LIMB_HEAD,
    CAMEL_LIMB_EAR_L,
    CAMEL_LIMB_EAR_R,
    CAMEL_LIMB_JAW,
    CAMEL_LIMB_SEAT, // no geometry: the model's own axes, carried along by its body
    CAMEL_LIMB_TAIL_1,
    CAMEL_LIMB_TAIL_2,
    CAMEL_LIMB_MAX
};

namespace {

const char kSkeleton[] = CAMEL_ASSET("gCamelSkel");

// In model units (100 a game unit), in a limb's own axes: a foot limb runs from the ankle along +y to the toes, the
// head from the back of the skull to the muzzle; the seat limb's axes are the model's (x left, y up, z forward).
Vec3f kSole = { 0.0f, 500.0f, 0.0f };
Vec3f kHeadMiddle = { 0.0f, 900.0f, 0.0f };
// Where the player's actor rides: En_Horse's "rider position". The player's actor is put 27 units under it, and
// Link's hips are 33.8 units above his actor: this sits him in the saddle (the seat is 79.7 units up, 5 behind the
// model's middle: SEAT in tools/build_camel.py). Nabooru is not placed by it: she is drawn in the seat's frame (see
// Camel_GetSeat). Link's own mounting starts, and his dismounting ends, 35 units to the side of it and 7 ahead.
Vec3f kRiderPoint = { 0.0f, 7640.0f, -495.0f };
// Dust under a foot that comes down while it runs.
constexpr f32 kDustSpeed = 5.0f;
constexpr f32 kFootDown = 2.5f; // a foot this near the ground (units) is on it

struct Seat {
    Actor* camel = nullptr;
    MtxF mtx;
    u32 frame = 0;
};
Seat sSeat; // of the camel the player rides (or else the one last drawn)

struct Feet {
    Actor* camel = nullptr;
    bool down[4] = { true, true, true, true };
};
Feet sFeet;

s32 FootIndex(s32 limb) {
    switch (limb) {
        case CAMEL_LIMB_FRONT_LEG_R_D:
            return 0;
        case CAMEL_LIMB_FRONT_LEG_L_D:
            return 1;
        case CAMEL_LIMB_HIND_LEG_R_D:
            return 2;
        case CAMEL_LIMB_HIND_LEG_L_D:
            return 3;
    }
    return -1;
}

void CamelPostLimbDraw(PlayState* play, s32 limbIndex, Gfx** dList, Vec3s* rot, void* data) {
    EnHorse* horse = (EnHorse*)data;
    Actor* actor = &horse->actor;

    if (limbIndex == CAMEL_LIMB_SEAT) {
        Player* player = GET_PLAYER(play);
        if (sSeat.camel == actor || player->rideActor == actor || player->rideActor == nullptr) {
            sSeat.camel = actor;
            Matrix_Get(&sSeat.mtx);
            sSeat.frame = play->gameplayFrames;
        }
        // (En_Horse works the rider's place out itself while it jumps)
        if (!(horse->stateFlags & ENHORSE_CALC_RIDER_POS)) {
            Matrix_MultVec3f(&kRiderPoint, &horse->riderPos);
            horse->riderPos.x -= actor->world.pos.x;
            horse->riderPos.y -= actor->world.pos.y;
            horse->riderPos.z -= actor->world.pos.z;
        } else {
            horse->stateFlags &= ~ENHORSE_CALC_RIDER_POS;
        }
    } else if (limbIndex == CAMEL_LIMB_HEAD) {
        // its voice comes from its head, where it can also be hit
        Vec3f head;
        f32 w;
        Matrix_MultVec3f(&kHeadMiddle, &head);
        SkinMatrix_Vec3fMtxFMultXYZW(&play->viewProjectionMtxF, &head, &horse->unk_228, &w);
        for (s32 i = 0; i < horse->jntSph.count; i++) {
            ColliderJntSphElement* element = &horse->jntSph.elements[i];
            element->dim.worldSphere.center.x = (s16)head.x;
            element->dim.worldSphere.center.y = (s16)head.y;
            element->dim.worldSphere.center.z = (s16)head.z;
            element->dim.worldSphere.radius = (s16)(element->dim.modelSphere.radius * element->dim.scale);
        }
    } else if (s32 foot = FootIndex(limbIndex); foot >= 0) {
        Vec3f sole;
        Matrix_MultVec3f(&kSole, &sole);
        if (sFeet.camel != actor) {
            sFeet = Feet{ actor };
        }
        bool down = sole.y - actor->world.pos.y < kFootDown;
        bool running = actor->speedXZ > kDustSpeed || horse->action == ENHORSE_ACT_STOPPING;
        if (down && !sFeet.down[foot] && running) {
            Vec3f* places[] = { &horse->frontRightHoof, &horse->frontLeftHoof, &horse->backRightHoof,
                                &horse->backLeftHoof };
            *places[foot] = sole;
            horse->dustFlags |= 1 << foot; // (En_Horse raises the dust)
        }
        sFeet.down[foot] = down;
    }
}

} // namespace

void Camel_Init(EnHorse* horse, PlayState* play, AnimationHeader* idleAnim) {
    SkelAnime_InitFlex(play, &horse->skin.skelAnime, (FlexSkeletonHeader*)kSkeleton, idleAnim, NULL, NULL, 0);
}

void Camel_Destroy(EnHorse* horse, PlayState* play) {
    SkelAnime_Free(&horse->skin.skelAnime, play);
    if (sSeat.camel == &horse->actor) {
        sSeat.camel = nullptr;
    }
    if (sFeet.camel == &horse->actor) {
        sFeet.camel = nullptr;
    }
}

void Camel_Draw(EnHorse* horse, PlayState* play) {
    SkelAnime* skel = &horse->skin.skelAnime;

    OPEN_DISPS(play->state.gfxCtx);
    SkelAnime_DrawFlexOpa(play, skel->skeleton, skel->jointTable, skel->dListCount, NULL, CamelPostLimbDraw, horse);
    CLOSE_DISPS(play->state.gfxCtx);

    // (as En_Horse does after drawing Epona)
    CollisionCheck_SetOC(play, &play->colChkCtx, &horse->jntSph.base);
    CollisionCheck_SetAC(play, &play->colChkCtx, &horse->jntSph.base);
}

Actor* Camel_Spawn(PlayState* play, f32 x, f32 y, f32 z, s16 yaw) {
    if (!ResourceMgr_FileExists(kSkeleton)) {
        return NULL;
    }
    // (En_Horse belongs to Epona's object, which the scene may not have)
    if (Object_GetIndex(&play->objectCtx, OBJECT_HORSE) < 0) {
        Object_Spawn(&play->objectCtx, OBJECT_HORSE);
    }
    return Actor_Spawn(&play->actorCtx, play, ACTOR_EN_HORSE, x, y, z, 0, yaw, 0, ENHORSE_PARAM_CAMEL | 1);
}

s32 Camel_IsCamel(Actor* actor) {
    return actor != NULL && actor->id == ACTOR_EN_HORSE && ((EnHorse*)actor)->type == HORSE_CAMEL;
}

s32 Camel_GetSeat(Actor* camel, MtxF* seat) {
    if (camel == NULL || sSeat.camel != camel || gPlayState == NULL ||
        gPlayState->gameplayFrames - sSeat.frame > 1) {
        return false;
    }
    *seat = sSeat.mtx;
    return true;
}

// ------------------------------------------------------------------------------------------------ voice
// A camel does not neigh. Where Epona whinnies or neighs, the camel's own sound files play if the mod has them
// (sounds/camel/*.wav in an archive); else it keeps quiet.
namespace {

struct Voice {
    u16 sfxId;
    const char* path;
    int sound = -2; // -2: not looked for yet
};
Voice sVoices[] = {
    { NA_SE_EV_HORSE_GROAN, "sounds/camel/grumble.wav" },
    { NA_SE_EV_HORSE_NEIGH, "sounds/camel/bellow.wav" },
};

EnHorse* CamelSounding(const Vec3f* pos) {
    if (gPlayState == nullptr || pos == nullptr) {
        return nullptr;
    }
    for (Actor* actor = gPlayState->actorCtx.actorLists[ACTORCAT_BG].head; actor != nullptr; actor = actor->next) {
        if (Camel_IsCamel(actor)) {
            EnHorse* horse = (EnHorse*)actor;
            if (pos == &horse->unk_21C || pos == &actor->projectedPos) {
                return horse;
            }
        }
    }
    return nullptr;
}

void RegisterCamel() {
    COND_VB_SHOULD(VB_PLAY_SFX, true, {
        u16* sfxId = va_arg(args, u16*);
        Vec3f* pos = va_arg(args, Vec3f*);
        for (Voice& voice : sVoices) {
            if (*sfxId != voice.sfxId) {
                continue;
            }
            EnHorse* camel = CamelSounding(pos);
            if (camel == nullptr) {
                break;
            }
            if (voice.sound == -2) {
                voice.sound = ModSound_Load(voice.path);
            }
            if (voice.sound >= 0) {
                ModSound_Play(voice.sound, &camel->unk_21C, 1.0f, 1200.0f, 1.0f);
            }
            *should = false;
            break;
        }
    });
}

RegisterShipInitFunc sInitFunc(RegisterCamel, {});

} // namespace
