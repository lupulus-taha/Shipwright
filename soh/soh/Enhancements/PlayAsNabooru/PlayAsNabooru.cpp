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
    f32 lift = 0.0f;                // extra height (model units) at the middle of the move, for leaps
    f32 span = 1.0f;                // Sync: the fraction of Link's animation that start..end covers
    f32 swordsFrame = -1.0f;        // >= 0: hold this frame of `swords` instead of following the body's frame
    const char* bareAnim = nullptr; // the same move for empty hands (same frame count), if it differs
};

// The jump attack leaps higher than the fighter's flip (model units, her model is drawn at 1/100 scale).
constexpr f32 kJumpSlashLift = 2000.0f;

#define NB_ASSET(name) "__OTR__objects/object_nb/" #name
#define GEN(name) NB_ASSET(gPlayAsNabooru##name##Anim)
#define GEN_SWORDS(name) NB_ASSET(gPlayAsNabooru##name##SwordsAnim)

// locomotion and idles
const Move kHips = { "Hips", gNabooruStandingHandsOnHipsAnim, nullptr, Mode::Loop };
// empty-handed, standing still she folds her arms: the white-clothed Gerudo's (unused) motion, which is stored
// unfolding, played backwards
const Move kIdle = { "Idle", GEN(Relaxed), nullptr, Mode::Loop }; // arms at her sides
const Move kFoldArms = { "FoldArms", GEN(FoldArms), nullptr, Mode::Once, 6, 0, 0.6f };
const Move kCrossedArms = { "CrossedArms", GEN(CrossedArms), nullptr, Mode::Loop };
// with scimitars (moves without sword data hold them in the fighter's grip): arms at her sides, blades hanging
const Move kIdleSwords = { "IdleSwords", GEN(ArmedIdle), nullptr, Mode::Loop };
const Move kTired = { "Tired", GEN(Tired), nullptr, Mode::Loop };
const Move kTiredSwords = { "TiredSwords", GEN(ArmedTired), nullptr, Mode::Loop };
const Move kWalk = { "Walk", gNabooruWalkingAnim, nullptr, Mode::Loop };
const Move kRun = { "Run", GEN(Run), nullptr, Mode::Loop };
const Move kRunBack = { "RunBack", GEN(RunBack), nullptr, Mode::Loop }; // upright; played backwards
// with scimitars out: her own arm swing, wrists turned to keep the blades off her body
const Move kArmedWalk = { "ArmedWalk", GEN(ArmedWalk), nullptr, Mode::Loop };
const Move kArmedRun = { "ArmedRun", GEN(ArmedRun), nullptr, Mode::Loop };
const Move kArmedRunBack = { "ArmedRunBack", GEN(ArmedRunBack), nullptr, Mode::Loop };
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
                         .span = 0.7f,
                         .bareAnim = GEN(FlipBare) };
const Move kBackflipEnd = { .name = "BackflipEnd",
                            .anim = GEN(Flip),
                            .swords = GEN_SWORDS(Flip),
                            .mode = Mode::Sync,
                            .start = 9,
                            .end = 10,
                            .bareAnim = GEN(FlipBare) }; // empty-handed she stands up relaxed
// jumping and falling (landings are the end of the fighter's drop-from-the-ceiling animation)
const Move kAirborne = { "Airborne", GEN(Air), nullptr, Mode::Pose, 0 };       // knees drawn up, arms out
const Move kLand = { "Land", GEN(Jump), GEN_SWORDS(Jump), Mode::Sync, 1, 16 }; // after a long drop, standing
// after a hop or a medium drop: the same landing with her knees half as bent, settling into her idle
const Move kLandShort = { .name = "LandShort",
                          .anim = GEN(LandLowArmed),
                          .swords = nullptr,
                          .mode = Mode::Sync,
                          .start = 1,
                          .end = 16,
                          .bareAnim = GEN(LandLow) };
// after a long drop, moving on (Link rolls): a quick dip, then her walk/run takes over
const Move kLandQuick = { "LandQuick", GEN(Jump), GEN_SWORDS(Jump), Mode::Once, 4, 14, 2.0f };
constexpr s32 kLandQuickFrames = 5;
constexpr f32 kLongDrop = 80.0f; // Link's own threshold: shorter drops have no landing
// getting hurt
const Move kDamage = {
    "Damage", GEN(Damage), GEN_SWORDS(Damage), Mode::Sync, 0, 7
}; // stops before she raises her sword again
const Move kKnockdown = { "Knockdown", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 0, 12 };
const Move kDown = { "Down", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 12, 22 };
const Move kGetUp = { "GetUp", GEN(Crouch), nullptr, Mode::Once, 10, 0, 0.6f };
const Move kDeath = { "Death", GEN(Faint), nullptr, Mode::Once, 0, -1, 1.0f };
// how she holds the scimitars when the current animation has no sword data (e.g. running with swords drawn)
const char* const kDefaultSwords = GEN_SWORDS(Carry);

// Rules are checked in order against Link's animation name (after "gPlayerAnim_link_"); the first rule with a
// matching substring wins. A null move means "Link-only, not implemented": use the locomotion fallback.
struct Rule {
    std::vector<const char*> patterns;
    const Move* move;
};

const std::vector<Rule> kRules = {
    // "250jump" is the jump up to a ledge; "landing_roll" and "short_landing" are handled by her own landing logic
    { { "climb", "water_fall", "uma_", "swimer_", "step_up", "250jump", "landing_roll", "short_landing" }, nullptr },
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
    // Link's "normal_landing" is his fall, and also his touchdown after a long drop (see Nabooru_OnPlayerUpdate)
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
constexpr f32 kCrossfadeStep = 0.2f;       // a new move blends in over 5 frames
constexpr f32 kLayerFadeStep = 0.25f;

struct Layer {
    SkelAnime skel;
    Vec3s joints[NB_LIMB_MAX];
    Vec3s morph[NB_LIMB_MAX];
    const Move* move = nullptr;
    const char* anim = nullptr; // the animation playing `move`
    f32 onceFrame = 0.0f;
    bool ready = false;
};

enum class Mask { Upper, Arms, Legs };

Layer sBody;    // whole body
Layer sOverlay; // drives part of the body (sMask), faded in/out by sOverlayWeight
Mask sMask = Mask::Upper;
f32 sOverlayWeight = 0.0f;
Vec3s sFinalJoints[NB_LIMB_MAX];
Vec3s sFadeFrom[NB_LIMB_MAX]; // her pose when the current move started, blended out over a few frames
f32 sFadeWeight = 0.0f;

bool sShowSwords = false;
Vec3f sPrevPos;
const void* sLinkPrevAnim = nullptr; // Link's animation can stall while he moves (see Nabooru_OnPlayerUpdate)
f32 sLinkPrevFrame = 0.0f;
s32 sLinkStalledFrames = 0;
s32 sLandingFrames = 0; // remaining frames of a quick landing
bool sInAir = false;
f32 sAirPeakY = 0.0f;    // highest point since leaving the ground
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
    sFadeWeight = 0.0f;
}

f32 LastFrame(const Move* move) {
    return Animation_GetLastFrame((AnimationHeader*)move->anim);
}

// the animation for a move, armed or empty-handed
const char* AnimFor(const Move* move) {
    return (!sShowSwords && move->bareAnim != nullptr && HasAnimation(move->bareAnim)) ? move->bareAnim : move->anim;
}

f32 EndFrame(const Move* move) {
    return (move->end < 0.0f) ? LastFrame(move) : move->end;
}

// Joint rotations are blended as quaternions: blending the Euler angles directly can swing a limb the long way
// round (two animations may describe the same arm pose with very different angles).
struct Quat {
    f32 w, x, y, z;
};

// OoT joint rotations are Rz(z) * Ry(y) * Rx(x)
Quat ToQuat(const Vec3s& r) {
    f32 hx = BINANG_TO_RAD(r.x) * 0.5f, hy = BINANG_TO_RAD(r.y) * 0.5f, hz = BINANG_TO_RAD(r.z) * 0.5f;
    f32 cx = cosf(hx), sx = sinf(hx), cy = cosf(hy), sy = sinf(hy), cz = cosf(hz), sz = sinf(hz);
    return { cz * cy * cx + sz * sy * sx, cz * cy * sx - sz * sy * cx, cz * sy * cx + sz * cy * sx,
             sz * cy * cx - cz * sy * sx };
}

s16 ToBinang(f32 radians) {
    return (s16)(s32)lroundf(radians * (32768.0f / (f32)M_PI)); // wraps +pi to -pi
}

Vec3s ToEuler(const Quat& q) {
    f32 r20 = 2.0f * (q.x * q.z - q.w * q.y);
    f32 r21 = 2.0f * (q.y * q.z + q.w * q.x);
    f32 r22 = 1.0f - 2.0f * (q.x * q.x + q.y * q.y);
    f32 r10 = 2.0f * (q.x * q.y + q.w * q.z);
    f32 r00 = 1.0f - 2.0f * (q.y * q.y + q.z * q.z);
    return { ToBinang(atan2f(r21, r22)), ToBinang(asinf(CLAMP(-r20, -1.0f, 1.0f))), ToBinang(atan2f(r10, r00)) };
}

// joint `a` moved `weight` of the way towards `b`
void MixRotation(Vec3s& a, const Vec3s& b, f32 weight) {
    Quat p = ToQuat(a), q = ToQuat(b);
    f32 sign = (p.w * q.w + p.x * q.x + p.y * q.y + p.z * q.z < 0.0f) ? -1.0f : 1.0f;
    Quat m = { p.w + (sign * q.w - p.w) * weight, p.x + (sign * q.x - p.x) * weight, p.y + (sign * q.y - p.y) * weight,
               p.z + (sign * q.z - p.z) * weight };
    f32 len = sqrtf(m.w * m.w + m.x * m.x + m.y * m.y + m.z * m.z);
    a = ToEuler({ m.w / len, m.x / len, m.y / len, m.z / len });
}

void MixTranslation(Vec3s& a, const Vec3s& b, f32 weight) {
    a.x += (s16)((b.x - a.x) * weight);
    a.y += (s16)((b.y - a.y) * weight);
    a.z += (s16)((b.z - a.z) * weight);
}

// Switches a layer to a move; returns whether it changed. Blending into it is done on her final pose.
bool SetMove(Layer& layer, const Move* move) {
    const char* anim = AnimFor(move);
    if (move == layer.move) {
        if (anim != layer.anim) { // drew or put away her scimitars mid-move: same frame, other animation
            layer.skel.animation = (AnimationHeader*)anim;
            layer.anim = anim;
            return true;
        }
        return false;
    }
    f32 last = LastFrame(move);
    if (move->mode == Mode::Loop) {
        Animation_Change(&layer.skel, (AnimationHeader*)anim, 1.0f, 0.0f, last, ANIMMODE_LOOP, 0.0f);
    } else {
        // frames are set by hand every update
        Animation_Change(&layer.skel, (AnimationHeader*)anim, 0.0f, move->start, last, ANIMMODE_ONCE, 0.0f);
    }
    layer.onceFrame = move->start;
    layer.move = move;
    layer.anim = anim;
    return true;
}

// a Once move has reached its last frame
bool OnceDone(const Layer& layer) {
    f32 end = EndFrame(layer.move);
    return (end >= layer.move->start) ? layer.onceFrame >= end : layer.onceFrame <= end;
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

// her armed version of a move, if she has it
const Move* Armed(const Move* move) {
    const Move* armed = (move == &kWalk)      ? &kArmedWalk
                        : (move == &kRun)     ? &kArmedRun
                        : (move == &kRunBack) ? &kArmedRunBack
                                              : nullptr;
    return (sShowSwords && armed != nullptr && HasAnimation(armed->anim)) ? armed : move;
}

// Locomotion when no rule matches. Returns the move and its loop speed; `legsOnly` asks for her normal upper body
// (used when walking sideways without her scimitars, so she doesn't hold a fighting pose with empty hands).
const Move* LocomotionMove(Player* player, f32* loopSpeed, bool* legsOnly) {
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
            if (speed > kRunThreshold && HasAnimation(kRunBack.anim)) {
                *loopSpeed = -CLAMP(speed / kRunReferenceSpeed, 0.75f, 3.0f);
                return &kRunBack;
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

const Move* Locomotion(Player* player, f32* loopSpeed, bool* legsOnly) {
    return Armed(LocomotionMove(player, loopSpeed, legsOnly));
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

// her final pose: the body, the overlay over its masked joints, and the pose she's blending out of
void BlendLayers() {
    for (s32 i = 0; i < NB_LIMB_MAX; i++) {
        sFinalJoints[i] = sBody.joints[i];
        if (sOverlayWeight > 0.0f && InMask(sMask, i)) {
            MixRotation(sFinalJoints[i], sOverlay.joints[i], sOverlayWeight);
        }
    }
    if (sFadeWeight > 0.0f) {
        MixTranslation(sFinalJoints[0], sFadeFrom[0], sFadeWeight);
        for (s32 i = 1; i < NB_LIMB_MAX; i++) {
            MixRotation(sFinalJoints[i], sFadeFrom[i], sFadeWeight);
        }
    }
}

// blend from her current pose into whatever comes next
void StartCrossfade() {
    memcpy(sFadeFrom, sFinalJoints, sizeof(sFadeFrom));
    sFadeWeight = 1.0f;
}

bool IsLinkTouchdown(const void* anim) {
    const char* name = LinkAnimName(anim);
    return name != nullptr && (strcmp(name, "normal_landing") == 0 || strcmp(name, "normal_landing_free") == 0);
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
        SetMove(sBody, &sForced);
        StepLayer(sBody, player, 0.0f);
        sOverlayWeight = 0.0f;
        sFadeWeight = 0.0f;
        BlendLayers();
        return;
    }

    f32 stepDistance = sqrtf(SQ(player->actor.world.pos.x - sPrevPos.x) + SQ(player->actor.world.pos.z - sPrevPos.z));
    sPrevPos = player->actor.world.pos;
    bool grounded = player->actor.bgCheckFlags & BGCHECKFLAG_GROUND;
    bool moving = player->actor.speedXZ > kMovingThreshold;

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

    if (YieldsToLocomotion(body) && sLinkStalledFrames >= 2 && moving) {
        body = nullptr;
    }
    if (body == &kAirborne && (sBody.move == &kBackflip || sBody.move == &kJumpSlashAir)) {
        // Link briefly plays his fall between a flip and its landing: finish the flip instead
        body = sBody.move;
        freezeBody = true;
    } else if (body == &kAirborne && grounded && IsLinkTouchdown(link->animation)) {
        body = &kLand; // Link's fall animation played on the ground is his landing after a long drop
    }
    if (body == &kTired && sShowSwords) {
        body = &kTiredSwords;
    }

    // Landing after a long drop while moving on (Link rolls): a quick dip into her walk/run. Short drops have none.
    if (!grounded) {
        sAirPeakY = sInAir ? MAX(sAirPeakY, player->actor.world.pos.y) : player->actor.world.pos.y;
        sInAir = true;
        sLandingFrames = 0;
    } else if (sInAir) {
        sInAir = false;
        if (sAirPeakY - player->actor.world.pos.y > kLongDrop && moving && HasAnimation(kLandQuick.anim)) {
            sLandingFrames = kLandQuickFrames;
        }
    }
    if (sLandingFrames > 0 && (body == nullptr || body == &kAirborne || body == &kLand)) {
        sLandingFrames--;
        body = &kLandQuick;
    } else if (body != &kLandQuick) {
        sLandingFrames = 0;
    }

    if (body == nullptr) {
        bool legsOnly = false;
        body = Locomotion(player, &bodySpeed, &legsOnly);
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
            // empty-handed: her relaxed upper body over the sideways steps
            overlay = HasAnimation(kIdle.anim) ? &kIdle : &kHips;
            sMask = Mask::Upper;
        }
    } else if (IsAttack(body) && stepDistance > kAttackStepThreshold && HasAnimation(kFightWalk.anim)) {
        // Link's slashes carry him forward: let her legs step instead of sliding.
        overlay = &kFightWalk;
        overlaySpeed = CLAMP(stepDistance / kFightWalkReferenceSpeed, 0.5f, 2.5f);
        sMask = Mask::Legs;
    }

    // Standing still empty-handed, she folds her arms.
    if (body == &kIdle && overlay == nullptr && HasAnimation(kFoldArms.anim) && HasAnimation(kCrossedArms.anim)) {
        bool folded = sBody.move == &kCrossedArms || (sBody.move == &kFoldArms && OnceDone(sBody));
        body = folded ? &kCrossedArms : &kFoldArms;
    }

    // The fighter's moves end in her fighting stance; with empty hands, let her arms come down once she's landed.
    if (!sShowSwords && overlay == nullptr && HasAnimation(kIdle.anim)) {
        bool late = body == &kLand && sBody.move == body && sBody.skel.curFrame >= 9.0f;
        if (late) {
            overlay = &kIdle;
            sMask = Mask::Arms;
        }
    }

    bool hadMove = sBody.move != nullptr; // nothing to blend from on her first frame
    bool changed = SetMove(sBody, body) && hadMove;
    if (freezeBody) {
        SkelAnime_Update(&sBody.skel);
    } else {
        StepLayer(sBody, player, bodySpeed);
    }

    if (overlay != nullptr) {
        changed |= SetMove(sOverlay, overlay) && sOverlayWeight > 0.0f;
        sOverlayWeight = MIN(sOverlayWeight + kLayerFadeStep, 1.0f);
    } else {
        sOverlayWeight = MAX(sOverlayWeight - kLayerFadeStep, 0.0f);
    }
    if (sOverlayWeight > 0.0f && sOverlay.move != nullptr) {
        StepLayer(sOverlay, player, overlaySpeed);
    }

    if (changed) {
        StartCrossfade();
    }
    sFadeWeight = MAX(sFadeWeight - kCrossfadeStep, 0.0f);
    BlendLayers();
}

// ------------------------------------------------------------------------------------------------ voice

// Link's (adult) voice clips that have a counterpart in Nabooru's own (from her Spirit Temple cutscenes) or the Gerudo
// fighter's. Everything else (climbing, pushing, jumping off ledges, magic, sneezing, drinking, ...) stays Link's.
struct VoiceSwap {
    u16 link;
    u16 hers;
};

const VoiceSwap kVoiceSwaps[] = {
    { NA_SE_VO_LI_SWORD_N, NA_SE_EN_GERUDOFT_ATTACK },       // slashes and jumps
    { NA_SE_VO_LI_SWORD_L, NA_SE_EN_GERUDOFT_ATTACK },       // strong slashes, spin and jump attacks
    { NA_SE_VO_LI_DAMAGE_S, NA_SE_EN_GERUDOFT_DAMAGE },      // hit
    { NA_SE_VO_LI_LAND_DAMAGE_S, NA_SE_EN_GERUDOFT_DAMAGE }, // hurt by a long fall
    { NA_SE_VO_LI_DOWN, NA_SE_EN_GERUDOFT_DEAD },            // dying
    { NA_SE_VO_LI_FALL_L, NA_SE_VO_NB_CRY_0 },               // knocked flying, falling into a pit
    { NA_SE_VO_LI_TAKEN_AWAY, NA_SE_VO_NB_CRY_0 },           // carried off by a Wallmaster, electrocuted
    { NA_SE_VO_LI_HELD, NA_SE_VO_NB_AGONY },                 // grabbed by a ReDead
    { NA_SE_VO_LI_FREEZE, NA_SE_VO_NB_AGONY },               // frozen, slammed to the ground
    { NA_SE_VO_LI_DEMO_DAMAGE, NA_SE_VO_NB_AGONY },          // hurt in cutscenes (Twinrova's beams, ...)
};

// Dark Link speaks with Link's voice too, from his own position; he still looks like Link, so he keeps it.
bool IsDarkLink(const Vec3f* pos) {
    if (pos == nullptr || gPlayState == nullptr) {
        return false;
    }
    for (s32 category = 0; category < ACTORCAT_MAX; category++) {
        for (Actor* actor = gPlayState->actorCtx.actorLists[category].head; actor != nullptr; actor = actor->next) {
            if (actor->id == ACTOR_EN_TORCH2 && pos == &actor->projectedPos) {
                return true;
            }
        }
    }
    return false;
}

// Some sounds are played with SFX_FLAG cleared (repeated every frame, e.g. the scream while electrocuted); keep that.
void SwapVoice(u16* sfxId, const Vec3f* pos) {
    u16 id = *sfxId | SFX_FLAG;
    if (SFX_BANK_MASK(id) != SFX_BANK_MASK(NA_SE_VO_LI_SWORD_N)) {
        return;
    }
    for (const VoiceSwap& swap : kVoiceSwaps) {
        if (id == swap.link) {
            if (!IsDarkLink(pos)) {
                *sfxId = (swap.hers & ~SFX_FLAG) | (*sfxId & SFX_FLAG);
            }
            return;
        }
    }
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

    // Her voice instead of Link's, where she has a counterpart.
    COND_VB_SHOULD(VB_PLAY_SFX, enabled, {
        u16* sfxId = va_arg(args, u16*);
        Vec3f* pos = va_arg(args, Vec3f*);
        SwapVoice(sfxId, pos);
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
