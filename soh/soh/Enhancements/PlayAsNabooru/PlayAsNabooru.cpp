// Play as Nabooru
//
// Link's player actor keeps running all game logic (movement, collision, camera, items, hitboxes), but his model is
// hidden and Nabooru is drawn in his place with her own skeleton, proportions and animations. Link's (invisible)
// skeleton still animates, because his hand/sword positions drive hitboxes and held items.
//
// Her animation mirrors whatever Link's invisible skeleton is doing: Link's current animation is matched by name
// against a rule table (slash, spin attack, landing, hit, ...). Each rule names a frame range of one of her
// animations; "Sync" ranges follow Link's progress through his animation, so the hitboxes (still Link's) line up with
// her motion. Anything unmatched falls back to her locomotion, which looks at how she moves relative to where she
// faces (forward, backward, sideways) and whether her scimitars are out.
//
// A second layer can drive part of her body from another animation: her upper body while she shields on the move,
// and her legs while she slashes on the move (Link's attacks slide him forward).
//
// Animations she doesn't have herself are retargeted from other Gerudo by tools/build_nabooru_assets.py, which writes
// them to play_as_nabooru.o2r for the mods folder. Every one of them falls back gracefully if it's missing.

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include "soh/ResourceManagerHelpers.h"
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>
#include "soh/frame_interpolation.h" // gives OPEN_DISPS' interpolation calls C linkage in this C++ file

extern "C" {
#include "z64.h"
#include "macros.h"
#include "functions.h"
#include "variables.h"
#include "objects/object_nb/object_nb.h"
#include "objects/object_geldb/object_geldb.h"
#include "overlays/actors/ovl_En_Nb/z_en_nb.h"
extern PlayState* gPlayState;
void ResourceMgr_UnregisterSkeleton(SkelAnime* skelAnime);
}

#define CVAR_PLAY_AS_NABOORU_NAME CVAR_ENHANCEMENT("PlayAsNabooru")
#define CVAR_PLAY_AS_NABOORU_VALUE CVarGetInteger(CVAR_PLAY_AS_NABOORU_NAME, 0)

static void PlayAsNabooru_DrawModel();

namespace {

// ------------------------------------------------------------------------------------------------ moves

enum class Mode {
    Loop, // loops at its own pace (locomotion scales the pace with her speed)
    Sync, // frame start..end follows Link's progress through his current animation (end < start plays backwards)
    Once, // plays start..end at `speed` frames per frame, then holds
    Pose, // holds frame `start`
};

struct Move {
    const char* name;
    const char* anim;
    const char* swords; // companion resource with the scimitars relative to her hands, or nullptr
    Mode mode;
    f32 start = 0.0f;
    f32 end = -1.0f; // -1: last frame
    f32 speed = 1.0f;
    f32 lift = 0.0f;         // extra height (model units) at the middle of the move, for leaps
    f32 span = 1.0f;         // Sync: the fraction of Link's animation that start..end covers
    f32 swordsFrame = -1.0f; // >= 0: hold this frame of `swords` instead of following the body's frame
};

// The jump attack leaps higher than the fighter's flip (model units, her model is drawn at 1/100 scale).
constexpr f32 kJumpSlashLift = 2000.0f;

#define NB_ASSET(name) "__OTR__objects/object_nb/" #name
#define GEN(name) NB_ASSET(gPlayAsNabooru##name##Anim)
#define GEN_SWORDS(name) NB_ASSET(gPlayAsNabooru##name##SwordsAnim)

// locomotion and idles
const Move kHips = { "Hips", gNabooruStandingHandsOnHipsAnim, nullptr, Mode::Loop };
const Move kIdle = { "Idle", GEN(Relaxed), nullptr, Mode::Loop }; // empty-handed
// with scimitars: her relaxed stance, blades hanging along her legs
#define IDLE_SWORDS GEN_SWORDS(Resting)
constexpr f32 kIdleSwordsFrame = 0.0f;
const Move kIdleSwords = { .name = "IdleSwords",
                           .anim = GEN(Relaxed),
                           .swords = IDLE_SWORDS,
                           .mode = Mode::Loop,
                           .swordsFrame = kIdleSwordsFrame };
const Move kTired = { "Tired", GEN(Tired), nullptr, Mode::Loop };
const Move kTiredSwords = { .name = "TiredSwords",
                            .anim = GEN(Tired),
                            .swords = IDLE_SWORDS,
                            .mode = Mode::Loop,
                            .swordsFrame = kIdleSwordsFrame };
const Move kWalk = { "Walk", gNabooruWalkingAnim, nullptr, Mode::Loop };
const Move kRun = { "Run", GEN(Run), nullptr, Mode::Loop };
const Move kStance = { "Stance", GEN(Neutral), GEN_SWORDS(Neutral), Mode::Loop };
const Move kFightWalk = { "FightWalk", GEN(FightWalk), GEN_SWORDS(FightWalk), Mode::Loop };
const Move kStrafe = { "Strafe", GEN(Sidestep), GEN_SWORDS(Sidestep), Mode::Loop };
const Move kStrafeMirror = { "StrafeMirror", GEN(SidestepMirror), GEN_SWORDS(SidestepMirror), Mode::Loop };
// shield: the fighter's crossed-scimitar block, standing upright
const Move kBlock = { "Block", GEN(Guard), GEN_SWORDS(Guard), Mode::Pose, 0 };
const Move kKneel = { "Kneel", GEN(Crouching), nullptr, Mode::Loop };
// attacks: Link's slash animation is the swing, his "_end" animation the recovery
const Move kSlash = { "Slash", GEN(Slash), GEN_SWORDS(Slash), Mode::Sync, 0, 6 };
const Move kSlashEnd = { "SlashEnd", GEN(Slash), GEN_SWORDS(Slash), Mode::Sync, 6, 9 };
const Move kHeavy = { "Heavy", GEN(GuardSlash), nullptr, Mode::Sync, 0, 8 };
const Move kHeavyEnd = { "HeavyEnd", GEN(GuardSlashRecover), nullptr, Mode::Sync, 0, 10 };
const Move kSpinWindup = { "SpinWindup", GEN(SpinAttack), GEN_SWORDS(SpinAttack), Mode::Sync, 0, 11 };
const Move kSpinCharge = { "SpinCharge", GEN(SpinAttack), GEN_SWORDS(SpinAttack), Mode::Pose, 11 };
const Move kSpin = { "Spin", GEN(SpinAttack), GEN_SWORDS(SpinAttack), Mode::Sync, 12, 22 };
const Move kSpinEnd = { "SpinEnd", GEN(SpinAttack), GEN_SWORDS(SpinAttack), Mode::Sync, 22, 33 };
const Move kJumpSlashAir = { "JumpSlashAir", GEN(Flip), GEN_SWORDS(Flip), Mode::Sync, 9, 0, 1.0f, kJumpSlashLift };
// the landing strike is quick (Link's sword hits as he lands), then she rises out of the landing crouch
const Move kJumpSlashHit = { "JumpSlashHit", GEN(Slash), GEN_SWORDS(Slash), Mode::Once, 2, 6, 1.0f };
const Move kJumpSlashEnd = { "JumpSlashEnd", GEN(Jump), GEN_SWORDS(Jump), Mode::Sync, 6, 16 };
// acrobatics: Link lands his backflip about 70% into its animation
const Move kBackflip = { .name = "Backflip",
                         .anim = GEN(Flip),
                         .swords = GEN_SWORDS(Flip),
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = 9,
                         .span = 0.7f };
const Move kBackflipEnd = { "BackflipEnd", GEN(Flip), GEN_SWORDS(Flip), Mode::Sync, 9, 10 };
// jumping and falling (landings are the end of the fighter's drop-from-the-ceiling animation)
// in the air: the flight phase of her run
const Move kAirborne = { "Airborne", GEN(Run), nullptr, Mode::Pose, 3 };
const Move kLand = { "Land", GEN(Jump), GEN_SWORDS(Jump), Mode::Sync, 1, 16 };
const Move kLandShort = { "LandShort", GEN(Jump), GEN_SWORDS(Jump), Mode::Sync, 5, 16 };
// touching down while moving on: a quick dip, then her walk/run takes over
const Move kLandQuick = { "LandQuick", GEN(Jump), GEN_SWORDS(Jump), Mode::Once, 4, 14, 2.0f };
constexpr s32 kLandQuickFrames = 5;
// getting hurt
const Move kDamage = {
    "Damage", GEN(Damage), GEN_SWORDS(Damage), Mode::Sync, 0, 7
}; // stops before she raises her sword again
const Move kKnockdown = { "Knockdown", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 0, 12 };
const Move kDown = { "Down", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 12, 22 };
const Move kGetUp = { "GetUp", GEN(Crouch), nullptr, Mode::Once, 10, 0, 0.6f };
const Move kDeath = { "Death", GEN(Faint), nullptr, Mode::Once, 0, -1, 1.0f };
// how she holds the scimitars when the current animation has no sword data (e.g. running with swords drawn)
const char* const kDefaultSwords = GEN_SWORDS(Resting);

// Rules are checked in order against Link's animation name (after "gPlayerAnim_link_"); the first rule with a
// matching substring wins. A null move means "Link-only, not implemented": use the locomotion fallback.
struct Rule {
    std::vector<const char*> patterns;
    const Move* move;
};

const std::vector<Rule> kRules = {
    { { "climb", "water_fall", "uma_", "swimer_", "step_up", "landing_roll" }, nullptr },
    { { "power_kiru_start" }, &kSpinWindup },
    { { "power_kiru" }, &kSpinCharge }, // holding a charged spin, also while walking
    { { "rolling_kiru_end", "rollkiru_end" }, &kSpinEnd },
    { { "rolling_kiru", "rollkiru" }, &kSpin },
    { { "jump_kiru_hit" }, &kJumpSlashHit },
    { { "jump_kiru_end", "jump_kiru_finsh_end" }, &kJumpSlashEnd },
    { { "jump_kiru" }, &kJumpSlashAir },
    { { "hammer_hit_end", "hammer_side_hit_end" }, &kHeavyEnd },
    { { "hammer_hit", "hammer_side_hit" }, &kHeavy },
    // combos alternate between her quick slash and the guard's heavy slash
    { { "Rside_kiru_end", "Rside_kiru_finsh_end", "normal_kiru_end", "normal_kiru_finsh_end", "turn_kiruR_end" },
      &kHeavyEnd },
    { { "Rside_kiru", "normal_kiru", "turn_kiruR" }, &kHeavy },
    { { "kiru_end" }, &kSlashEnd },
    { { "_kiru" }, &kSlash },
    { { "backturn_jump_end" }, &kBackflipEnd },
    { { "backturn_jump" }, &kBackflip },
    { { "side_jump_end" }, &kLandShort },
    { { "side_jump" }, &kAirborne },
    { { "defense_hit", "defense_long_hit" }, &kBlock }, // a hit blocked with the shield
    { { "derth" }, &kDeath },
    { { "down_wake" }, &kGetUp },
    { { "_downB", "hip_down" }, &kDown },
    { { "_downA", "ice_down" }, &kKnockdown },
    { { "_hit", "_shit", "damage" }, &kDamage },
    { { "250jump" }, &kAirborne }, // jumping up to grab a ledge
    // Link's "landing" animations are his fall; "short_landing" is the touchdown
    { { "short_landing" }, &kLand },
    { { "run_jump_end", "front_jump_end", "anchor_landing" }, &kLandShort },
    { { "landing", "normal_jump", "run_jump", "front_jump" }, &kAirborne },
    { { "talk" }, &kHips },
    { { "wait_heat", "waitF_heat" }, &kTired }, // armed version picked in UpdateBody
};

const char* LinkAnimName(const void* anim) {
    const char* path = (const char*)anim;
    if (path == nullptr || strncmp(path, "__OTR__", 7) != 0) {
        return nullptr;
    }
    const char* name = strstr(path, "gPlayerAnim_link_");
    return name ? name + strlen("gPlayerAnim_link_") : nullptr;
}

// Generated animations live in a mod archive that may not be installed; checked once per path.
bool HasAnimation(const char* path) {
    static std::unordered_map<const char*, bool> sCache;
    auto it = sCache.find(path);
    if (it == sCache.end()) {
        it = sCache.emplace(path, ResourceMgr_FileExists(path)).first;
    }
    return it->second;
}

const Move* MatchRule(const void* linkAnim) {
    static std::unordered_map<const void*, const Move*> sCache; // animation paths are stable resource strings
    auto cached = sCache.find(linkAnim);
    if (cached != sCache.end()) {
        return cached->second;
    }
    const Move* result = nullptr;
    if (const char* name = LinkAnimName(linkAnim)) {
        for (const Rule& rule : kRules) {
            bool hit = false;
            for (const char* p : rule.patterns) {
                hit |= strstr(name, p) != nullptr;
            }
            if (hit) {
                result = rule.move;
                break;
            }
        }
    }
    if (result != nullptr && !HasAnimation(result->anim)) {
        result = nullptr;
    }
    sCache.emplace(linkAnim, result);
    return result;
}

// ------------------------------------------------------------------------------------------------ layers

// Player speeds (units per frame) at which her cycles play at their natural speed.
constexpr f32 kWalkReferenceSpeed = 3.0f;
constexpr f32 kRunReferenceSpeed = 4.0f;
constexpr f32 kFightWalkReferenceSpeed = 2.0f;
constexpr f32 kStrafeReferenceSpeed = 2.5f;
constexpr f32 kMovingThreshold = 0.5f;
constexpr f32 kRunThreshold = 4.5f;
constexpr f32 kAttackStepThreshold = 1.0f; // Link's attacks slide him; above this, her legs step along
constexpr f32 kMorphFrames = -4.0f;
constexpr f32 kLayerFadeStep = 0.25f;

struct Layer {
    SkelAnime skel;
    Vec3s joints[NB_LIMB_MAX];
    Vec3s morph[NB_LIMB_MAX];
    const Move* move = nullptr;
    f32 onceFrame = 0.0f;
    bool ready = false;
};

enum class Mask { Upper, Arms, Legs };

Layer sBody;    // whole body
Layer sOverlay; // drives part of the body (sMask), faded in/out by sOverlayWeight
Mask sMask = Mask::Upper;
f32 sOverlayWeight = 0.0f;
Vec3s sFinalJoints[NB_LIMB_MAX];

bool sShowSwords = false;
Vec3f sPrevPos;
const void* sLinkPrevAnim = nullptr; // Link's animation can stall while he moves (see Nabooru_OnPlayerUpdate)
f32 sLinkPrevFrame = 0.0f;
s32 sLinkStalledFrames = 0;
s32 sLandingFrames = 0;  // remaining frames of a quick landing
std::string sForcedName; // test harness: show one animation frame instead of mirroring Link
f32 sForcedFrame = 0.0f;
f32 sForcedSwordsFrame = -1.0f;
Move sForced;
std::string sForcedAnim, sForcedSwords;

s16 sBlinkTimer = 0;
s16 sEyeIndex = 0;
const char* sEyeTextures[] = { gNabooruEyeOpenTex, gNabooruEyeHalfTex, gNabooruEyeClosedTex };

bool InMask(Mask mask, s32 joint) {
    if (mask == Mask::Upper) {
        return joint >= NB_LIMB_TORSO && joint <= NB_LIMB_PONYTAIL;
    }
    if (mask == Mask::Arms) {
        return joint >= NB_LIMB_L_UPPER_ARM && joint <= NB_LIMB_R_HAND;
    }
    return (joint >= NB_LIMB_L_THIGH && joint <= NB_LIMB_R_FOOT) || joint == NB_LIMB_WAIST;
}

void InitLayer(Layer& layer) {
    if (!layer.ready) {
        SkelAnime_InitFlex(gPlayState, &layer.skel, (FlexSkeletonHeader*)gNabooruSkel, NULL, layer.joints, layer.morph,
                           NB_LIMB_MAX);
        layer.move = nullptr;
        layer.ready = true;
    }
}

void ReleaseLayer(Layer& layer) {
    if (layer.ready) {
        ResourceMgr_UnregisterSkeleton(&layer.skel);
        layer.ready = false;
    }
}

void ReleaseSkeleton() {
    ReleaseLayer(sBody);
    ReleaseLayer(sOverlay);
    sOverlayWeight = 0.0f;
}

f32 LastFrame(const Move* move) {
    return Animation_GetLastFrame((AnimationHeader*)move->anim);
}

f32 EndFrame(const Move* move) {
    return (move->end < 0.0f) ? LastFrame(move) : move->end;
}

void SetMove(Layer& layer, const Move* move, bool morph) {
    if (move == layer.move) {
        return;
    }
    f32 last = LastFrame(move);
    f32 morphFrames = (layer.move == nullptr || !morph) ? 0.0f : kMorphFrames;
    if (move->mode == Mode::Loop) {
        Animation_Change(&layer.skel, (AnimationHeader*)move->anim, 1.0f, 0.0f, last, ANIMMODE_LOOP, morphFrames);
    } else {
        // frames are set by hand every update
        Animation_Change(&layer.skel, (AnimationHeader*)move->anim, 0.0f, move->start, last, ANIMMODE_ONCE,
                         morphFrames);
    }
    layer.onceFrame = move->start;
    layer.move = move;
}

// fraction [0, 1] of Link's current animation that has played
f32 LinkAnimProgress(Player* player) {
    SkelAnime* link = &player->skelAnime;
    f32 end = (link->endFrame > 0.0f) ? link->endFrame : link->animLength - 1.0f;
    return (end > 0.0f) ? CLAMP(link->curFrame / end, 0.0f, 1.0f) : 0.0f;
}

// progress through a Sync move: it covers the first `span` of Link's animation
f32 MoveProgress(const Move* move, Player* player) {
    return MIN(LinkAnimProgress(player) / move->span, 1.0f);
}

void StepLayer(Layer& layer, Player* player, f32 loopSpeed) {
    const Move* move = layer.move;
    f32 end = EndFrame(move);
    switch (move->mode) {
        case Mode::Loop:
            layer.skel.playSpeed = loopSpeed;
            break;
        case Mode::Sync:
            layer.skel.curFrame = move->start + (end - move->start) * MoveProgress(move, player);
            break;
        case Mode::Once:
            if (end >= move->start) {
                layer.onceFrame = MIN(layer.onceFrame + move->speed, end);
            } else {
                layer.onceFrame = MAX(layer.onceFrame - move->speed, end);
            }
            layer.skel.curFrame = layer.onceFrame;
            break;
        case Mode::Pose:
            layer.skel.curFrame = move->start;
            break;
    }
    SkelAnime_Update(&layer.skel);
    if (move->lift > 0.0f) {
        layer.joints[0].y += (s16)(move->lift * sinf(M_PI * MoveProgress(move, player)));
    }
}

// ------------------------------------------------------------------------------------------------ choosing moves

enum class Dir { Forward, Back, Left, Right };

// direction of travel relative to the way she faces
Dir TravelDirection(Player* player) {
    s16 rel = player->actor.world.rot.y - player->actor.shape.rot.y;
    if (ABS(rel) <= 0x2800) {
        return Dir::Forward;
    }
    if (ABS(rel) >= 0x5800) {
        return Dir::Back;
    }
    return (rel > 0) ? Dir::Left : Dir::Right;
}

bool IsTargeting(Player* player) {
    return player->stateFlags1 & (PLAYER_STATE1_PARALLEL | PLAYER_STATE1_HOSTILE_LOCK_ON | PLAYER_STATE1_Z_TARGETING);
}

// Locomotion when no rule matches. Returns the move and its loop speed; `legsOnly` asks for her normal upper body
// (used when walking sideways without her scimitars, so she doesn't hold a fighting pose with empty hands).
const Move* Locomotion(Player* player, f32* loopSpeed, bool* legsOnly) {
    f32 speed = player->actor.speedXZ;
    *legsOnly = false;
    *loopSpeed = 1.0f;
    if (speed <= kMovingThreshold) {
        if (player->stateFlags1 & PLAYER_STATE1_TALKING) {
            return &kHips;
        }
        if (IsTargeting(player) && sShowSwords && HasAnimation(kStance.anim)) {
            return &kStance;
        }
        const Move* idle = sShowSwords ? &kIdleSwords : &kIdle;
        return HasAnimation(idle->anim) ? idle : &kHips;
    }

    // Without Z-targeting she faces where she goes, except when sliding backwards (e.g. out of a backflip).
    Dir dir = TravelDirection(player);
    if (!IsTargeting(player) && dir != Dir::Back) {
        dir = Dir::Forward;
    }
    switch (dir) {
        case Dir::Forward:
            if (speed > kRunThreshold && HasAnimation(kRun.anim)) {
                *loopSpeed = CLAMP(speed / kRunReferenceSpeed, 0.75f, 3.0f);
                return &kRun;
            }
            if (IsTargeting(player) && sShowSwords && HasAnimation(kFightWalk.anim)) {
                *loopSpeed = CLAMP(speed / kFightWalkReferenceSpeed, 0.5f, 2.5f);
                return &kFightWalk;
            }
            *loopSpeed = CLAMP(speed / kWalkReferenceSpeed, 0.5f, 3.0f);
            return &kWalk;
        case Dir::Back:
            if (speed > kRunThreshold && HasAnimation(kRun.anim)) {
                *loopSpeed = -CLAMP(speed / kRunReferenceSpeed, 0.75f, 3.0f);
                return &kRun;
            }
            if (sShowSwords && HasAnimation(kFightWalk.anim)) {
                *loopSpeed = -CLAMP(speed / kFightWalkReferenceSpeed, 0.5f, 2.5f);
                return &kFightWalk;
            }
            *loopSpeed = -CLAMP(speed / kWalkReferenceSpeed, 0.5f, 3.0f);
            return &kWalk;
        case Dir::Left:
        case Dir::Right:
            if (!HasAnimation(kStrafe.anim)) {
                *loopSpeed = CLAMP(speed / kWalkReferenceSpeed, 0.5f, 3.0f);
                return &kWalk;
            }
            // the fighter's circling steps go to her left when played backwards; right is the mirrored copy
            *loopSpeed = -CLAMP(speed / kStrafeReferenceSpeed, 0.5f, 3.0f);
            *legsOnly = !sShowSwords;
            return (dir == Dir::Right && HasAnimation(kStrafeMirror.anim)) ? &kStrafeMirror : &kStrafe;
    }
    return &kIdle;
}

bool IsAttack(const Move* move) {
    return move == &kSlash || move == &kSlashEnd || move == &kHeavy || move == &kHeavyEnd;
}

// moves that give way to her walk/run when Link moves while his animation is stalled (see Nabooru_OnPlayerUpdate)
bool YieldsToLocomotion(const Move* move) {
    return IsAttack(move) || move == &kSpinEnd || move == &kLandQuick || move == &kBackflipEnd ||
           move == &kJumpSlashEnd || move == &kLand || move == &kLandShort;
}

void UpdateEyes() {
    if (DECR(sBlinkTimer) == 0) {
        sBlinkTimer = Rand_S16Offset(60, 60);
    }
    sEyeIndex = (sBlinkTimer < ARRAY_COUNT(sEyeTextures)) ? sBlinkTimer : 0;
}

void BlendLayers() {
    for (s32 i = 0; i < NB_LIMB_MAX; i++) {
        sFinalJoints[i] = sBody.joints[i];
        if (sOverlayWeight > 0.0f && InMask(sMask, i)) {
            Vec3s& a = sFinalJoints[i];
            const Vec3s& b = sOverlay.joints[i];
            a.x += (s16)((s16)(b.x - a.x) * sOverlayWeight);
            a.y += (s16)((s16)(b.y - a.y) * sOverlayWeight);
            a.z += (s16)((s16)(b.z - a.z) * sOverlayWeight);
        }
    }
}

void Nabooru_OnPlayerUpdate() {
    Player* player = GET_PLAYER(gPlayState);
    if (player == nullptr) {
        return;
    }
    InitLayer(sBody);
    InitLayer(sOverlay);
    UpdateEyes();

    // Her scimitars are out whenever Link has a melee weapon in hand.
    sShowSwords = player->leftHandType == PLAYER_MODELTYPE_LH_SWORD ||
                  player->leftHandType == PLAYER_MODELTYPE_LH_SWORD_2 ||
                  player->leftHandType == PLAYER_MODELTYPE_LH_BGS || player->leftHandType == PLAYER_MODELTYPE_LH_HAMMER;

    if (!sForcedName.empty()) {
        SetMove(sBody, &sForced, false);
        StepLayer(sBody, player, 0.0f);
        sOverlayWeight = 0.0f;
        BlendLayers();
        return;
    }

    f32 stepDistance = sqrtf(SQ(player->actor.world.pos.x - sPrevPos.x) + SQ(player->actor.world.pos.z - sPrevPos.z));
    sPrevPos = player->actor.world.pos;

    // While Z-targeting, Link's walk/run is blended into his skeleton separately: his main animation then stalls on
    // whatever came before (often a slash recovery) while he moves. Treat that as locomotion.
    SkelAnime* link = &player->skelAnime;
    bool stalled = link->animation == sLinkPrevAnim && link->curFrame == sLinkPrevFrame;
    sLinkStalledFrames = stalled ? sLinkStalledFrames + 1 : 0;
    sLinkPrevAnim = link->animation;
    sLinkPrevFrame = link->curFrame;

    const Move* body = MatchRule(link->animation);
    const Move* overlay = nullptr;
    f32 bodySpeed = 1.0f, overlaySpeed = 1.0f;
    bool shielding = player->stateFlags1 & PLAYER_STATE1_SHIELDING;
    bool freezeBody = false;

    if (YieldsToLocomotion(body) && sLinkStalledFrames >= 2 && player->actor.speedXZ > kMovingThreshold) {
        body = nullptr;
    }
    if (body == &kAirborne && (sBody.move == &kBackflip || sBody.move == &kJumpSlashAir)) {
        // Link briefly plays his fall between a flip and its landing: finish the flip instead
        body = sBody.move;
        freezeBody = true;
    }
    if (body == &kTired && sShowSwords) {
        body = &kTiredSwords;
    }

    if (body == nullptr && player->actor.bgCheckFlags & BGCHECKFLAG_GROUND && HasAnimation(kLandQuick.anim)) {
        // Link lands straight into his run when the stick is held, without a landing of his own
        bool cutShort = (sBody.move == &kLand || sBody.move == &kLandShort) && sBody.skel.curFrame < 10.0f;
        if (sBody.move == &kAirborne || cutShort) {
            sLandingFrames = kLandQuickFrames;
        }
        if (sLandingFrames > 0) {
            sLandingFrames--;
            body = &kLandQuick;
        }
    } else {
        sLandingFrames = 0;
    }

    if (body == nullptr) {
        bool legsOnly = false;
        body = Locomotion(player, &bodySpeed, &legsOnly);
        bool moving = player->actor.speedXZ > kMovingThreshold;
        if (shielding && HasAnimation(kBlock.anim)) {
            if (moving) {
                overlay = &kBlock; // guard up while walking: her arms only, so she still looks ahead
                sMask = Mask::Arms;
            } else if (IsTargeting(player)) {
                body = &kBlock;
            } else {
                body = HasAnimation(kKneel.anim) ? &kKneel : &kBlock; // Link crouches behind his shield
                overlay = &kBlock;
                sMask = Mask::Upper;
            }
        } else if (legsOnly) {
            overlay = HasAnimation(kIdle.anim) ? &kIdle
                                               : &kHips; // empty-handed: her relaxed upper body over the sideways steps
            sMask = Mask::Upper;
        }
    } else if (IsAttack(body) && stepDistance > kAttackStepThreshold && HasAnimation(kFightWalk.anim)) {
        // Link's slashes carry him forward: let her legs step instead of sliding.
        overlay = &kFightWalk;
        overlaySpeed = CLAMP(stepDistance / kFightWalkReferenceSpeed, 0.5f, 2.5f);
        sMask = Mask::Legs;
    }

    SetMove(sBody, body, true);
    if (freezeBody) {
        SkelAnime_Update(&sBody.skel);
    } else {
        StepLayer(sBody, player, bodySpeed);
    }

    if (overlay != nullptr) {
        SetMove(sOverlay, overlay, sOverlayWeight > 0.0f);
        sOverlayWeight = MIN(sOverlayWeight + kLayerFadeStep, 1.0f);
    } else {
        sOverlayWeight = MAX(sOverlayWeight - kLayerFadeStep, 0.0f);
    }
    if (sOverlayWeight > 0.0f && sOverlay.move != nullptr) {
        StepLayer(sOverlay, player, overlaySpeed);
    }
    BlendLayers();
}

void Nabooru_OnPlayerDraw(bool isFirstPerson) {
    if (isFirstPerson || !sBody.ready || gPlayState == nullptr) {
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

// ------------------------------------------------------------------------------------------------ scimitars

// Rotation of joint `joint` at `frame` of a (generated) animation resource.
bool SampleJointRotation(const char* path, s32 frame, s32 joint, Vec3s* out) {
    if (path == nullptr || !HasAnimation(path)) {
        return false;
    }
    AnimationHeader* anim = (AnimationHeader*)ResourceMgr_LoadAnimByName(path);
    if (anim == nullptr || anim->frameData == nullptr || anim->jointIndices == nullptr) {
        return false;
    }
    frame = CLAMP(frame, 0, anim->common.frameCount - 1);
    JointIndex* index = &anim->jointIndices[joint];
    auto value = [&](u16 i) { return (i < anim->staticIndexMax) ? anim->frameData[i] : anim->frameData[i + frame]; };
    out->x = value(index->x);
    out->y = value(index->y);
    out->z = value(index->z);
    return true;
}

// Scimitar rotation relative to her hand: from the layer posing her arms if it has sword data, else her default grip.
bool GetSwordRotation(bool leftHand, Vec3s* rot) {
    s32 joint = leftHand ? 1 : 2;
    const Layer& arms = (sOverlayWeight >= 0.5f && (sMask == Mask::Upper || sMask == Mask::Arms)) ? sOverlay : sBody;
    f32 frame = (arms.move != nullptr && arms.move->swordsFrame >= 0.0f) ? arms.move->swordsFrame : arms.skel.curFrame;
    if (arms.move == &sForced && sForcedSwordsFrame >= 0.0f) {
        frame = sForcedSwordsFrame;
    }
    if (arms.move != nullptr && SampleJointRotation(arms.move->swords, (s32)frame, joint, rot)) {
        return true;
    }
    return SampleJointRotation(kDefaultSwords, 0, joint, rot);
}

} // namespace

// For tools: the test harness logs her current move and frame.
void PlayAsNabooru_DebugState(const char** move, float* frame) {
    *move = (sBody.move != nullptr) ? sBody.move->name : "-";
    *frame = sBody.skel.curFrame;
    if (sOverlayWeight > 0.0f && sOverlay.move != nullptr) {
        static std::string sBoth;
        sBoth = std::string(*move) + "+" + sOverlay.move->name;
        *move = sBoth.c_str();
    }
}

// For tools: hold one frame of one of her animations (`name` as in gPlayAsNabooru<name>Anim, or a full resource
// path); an empty name returns to normal.
void PlayAsNabooru_DebugForce(const char* name, float frame, const char* swords, float swordsFrame) {
    sForcedSwordsFrame = swordsFrame;
    sForcedName = name;
    if (sForcedName.empty()) {
        sBody.move = nullptr;
        return;
    }
    if (sForcedName.rfind("__OTR__", 0) == 0) {
        sForcedAnim = sForcedName;
        sForcedSwords.clear();
    } else {
        sForcedAnim = "__OTR__objects/object_nb/gPlayAsNabooru" + sForcedName + "Anim";
        sForcedSwords =
            "__OTR__objects/object_nb/gPlayAsNabooru" + std::string((swords && *swords) ? swords : name) + "SwordsAnim";
    }
    sForced = { sForcedName.c_str(), sForcedAnim.c_str(), sForcedSwords.empty() ? nullptr : sForcedSwords.c_str(),
                Mode::Pose, frame };
    sBody.move = nullptr; // re-apply even if the same forced move was shown before
}

// ------------------------------------------------------------------------------------------------ drawing
// Kept outside the anonymous namespace: OPEN_DISPS/CLOSE_DISPS declare the frame-interpolation functions at block
// scope, and those declarations must resolve to the global extern "C" ones from frame_interpolation.h.

static void PlayAsNabooru_DrawSword(PlayState* play, bool leftHand) {
    Vec3s rot;
    if (!GetSwordRotation(leftHand, &rot)) {
        return;
    }
    // The current matrix is her hand's; the scimitar hangs off it with zero offset (the Gerudo fighter's rig).
    OPEN_DISPS(play->state.gfxCtx);
    Matrix_Push();
    Matrix_RotateZYX(rot.x, rot.y, rot.z, MTXMODE_APPLY);
    gSPMatrix(POLY_OPA_DISP++, MATRIX_NEWMTX(play->state.gfxCtx), G_MTX_NOPUSH | G_MTX_LOAD | G_MTX_MODELVIEW);
    gSPDisplayList(POLY_OPA_DISP++, (Gfx*)(leftHand ? gGerudoRedSkelLimbsLimb_00A39CDL_009460
                                                    : gGerudoRedSkelLimbsLimb_00A360DL_0097D0));
    Matrix_Pop();
    CLOSE_DISPS(play->state.gfxCtx);
}

// Records her ankles as the player's feet, so the foot shadows (drawn right after the player) follow her, not
// Link's invisible skeleton; and draws her scimitars in her hands.
static void PlayAsNabooru_PostLimbDraw(PlayState* play, s32 limbIndex, Gfx** dList, Vec3s* rot, void* data) {
    static Vec3f sAnkle = { 0.0f, 0.0f, 0.0f };
    Actor_SetFeetPos((Actor*)data, limbIndex, NB_LIMB_L_FOOT, &sAnkle, NB_LIMB_R_FOOT, &sAnkle);

    if (sShowSwords && (limbIndex == NB_LIMB_L_HAND || limbIndex == NB_LIMB_R_HAND)) {
        PlayAsNabooru_DrawSword(play, limbIndex == NB_LIMB_L_HAND);
    }
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
    SkelAnime_DrawFlexOpa(gPlayState, sBody.skel.skeleton, sFinalJoints, sBody.skel.dListCount, NULL,
                          PlayAsNabooru_PostLimbDraw, &player->actor);

    CLOSE_DISPS(gPlayState->state.gfxCtx);
}
