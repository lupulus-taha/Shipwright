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
#include "overlays/actors/ovl_En_M_Thunder/z_en_m_thunder.h"
#include "align_asset_macro.h"
extern PlayState* gPlayState;
void ResourceMgr_UnregisterSkeleton(SkelAnime* skelAnime);
}

#define CVAR_PLAY_AS_NABOORU_NAME CVAR_ENHANCEMENT("PlayAsNabooru")
#define CVAR_PLAY_AS_NABOORU_VALUE CVarGetInteger(CVAR_PLAY_AS_NABOORU_NAME, 0)

static void PlayAsNabooru_DrawModel();
static void PlayAsNabooru_DrawChargeGlow(PlayState* play, EnMThunder* charge, u8 alpha, f32 thickness);

namespace {

// ------------------------------------------------------------------------------------------------ moves

enum class Mode {
    Loop, // loops at its own pace (locomotion scales the pace with her speed)
    Sync, // frame start..end follows Link's progress through his current animation (end < start plays backwards)
    Once, // plays start..end at `speed` frames per frame, then holds
    Pose, // holds frame `start`
};

constexpr f32 kCrossfadeStep = 0.2f; // a new move blends in over 5 frames

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
    // Sync over part of Link's animation: from his frame `linkFrom` to `linkTo` (-1: his last frame), after which
    // `next` takes over, or without one her own idle (Link may stand still a while before his animation ends). Moves
    // without these follow all of it (or the first `span`).
    f32 linkFrom = 0.0f;
    f32 linkTo = -1.0f;
    const Move* next = nullptr;
    f32 blendIn = kCrossfadeStep; // how much of the way from her previous pose she comes each frame (1: at once)
    f32 armsBlendIn = 0.0f;       // > 0: her arms come this much of the way each frame instead
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
// acrobatics: on flat ground Link touches down from his backflip at 11 of its 14 frames; the fighter's flip touches
// down at frame 7, crouches (8) and rises (9, 10) while Link plays his landing
const Move kBackflip = { .name = "Backflip",
                         .anim = GEN(Flip),
                         .swords = GEN_SWORDS(Flip),
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = 7,
                         .span = 0.78f,
                         .bareAnim = GEN(FlipBare) };
const Move kBackflipEnd = { .name = "BackflipEnd",
                            .anim = GEN(Flip),
                            .swords = GEN_SWORDS(Flip),
                            .mode = Mode::Sync,
                            .start = 7,
                            .end = 10,
                            .bareAnim = GEN(FlipBare) }; // empty-handed she stands up relaxed
// jumping and falling (landings are the end of the fighter's drop-from-the-ceiling animation)
const Move kAirborne = { "Airborne", GEN(Air), nullptr, Mode::Pose, 0 };       // knees drawn up, arms out
// Running off a ledge Link leaps (13 frames, the last held while he falls on). Hers (generated, frame by frame with
// his): a push-off leaning into it, then a split in the air, arms swinging against her legs, gathering towards her
// pose in the air. It cuts in quickly, the push-off has to show.
const Move kRunJump = { .name = "RunJump",
                        .anim = GEN(RunJumpArmed),
                        .swords = nullptr,
                        .mode = Mode::Sync,
                        .start = 0,
                        .end = 12,
                        .bareAnim = GEN(RunJump),
                        .blendIn = 0.5f };
// Link's side hop leaves the ground at once and is over in 6 frames, under 7 units high, then he slides on a little as
// he lands. Hers (generated, frame by frame with his hop and his landing) goes in an arc: a crouch, a push-off from
// her trailing toes, which stay where she took off (kept on the ground, see KeepFeetDown) while her hips go on over;
// in the air her hips lead with her legs tucked and trailing, hips turned away from the hop, arms on the side she came
// from; her leading foot plants where it will stand and she slides on over it into the landing. Armed, from her
// fighting stance and back into it. No blending into it: the crouch has to show at once (only her arms take a few
// frames, they may have been folded). Frames 0-6 are the hop, 6-13 the landing.
constexpr f32 kHopArmsBlendIn = 0.34f;
constexpr f32 kSideHopTouchdown = 6.0f;
constexpr f32 kSideHopLanded = 13.0f;
const Move kSideHopR = { .name = "SideHopR",
                         .anim = GEN(SideHopArmedR),
                         .swords = GEN_SWORDS(SideHopArmedR),
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = kSideHopTouchdown,
                         .bareAnim = GEN(SideHopR),
                         .blendIn = 1.0f,
                         .armsBlendIn = kHopArmsBlendIn };
const Move kSideHopL = { .name = "SideHopL",
                         .anim = GEN(SideHopArmedL),
                         .swords = GEN_SWORDS(SideHopArmedL),
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = kSideHopTouchdown,
                         .bareAnim = GEN(SideHopL),
                         .blendIn = 1.0f,
                         .armsBlendIn = kHopArmsBlendIn };
const Move kSideHopLandR = { .name = "SideHopLandR",
                             .anim = GEN(SideHopArmedR),
                             .swords = GEN_SWORDS(SideHopArmedR),
                             .mode = Mode::Sync,
                             .start = kSideHopTouchdown,
                             .end = kSideHopLanded,
                             .bareAnim = GEN(SideHopR) };
const Move kSideHopLandL = { .name = "SideHopLandL",
                             .anim = GEN(SideHopArmedL),
                             .swords = GEN_SWORDS(SideHopArmedL),
                             .mode = Mode::Sync,
                             .start = kSideHopTouchdown,
                             .end = kSideHopLanded,
                             .bareAnim = GEN(SideHopL) };
constexpr f32 kSideHopGroundFrames = 1.0f; // Link's frames of the hop during which her feet stay on the ground
const Move kLand = { "Land", GEN(Jump), GEN_SWORDS(Jump), Mode::Sync, 1, 16 }; // after a long drop, standing
// after a hop or a short or medium drop: the same landing with her knees half as bent, settling into her idle
const Move kLandShort = { .name = "LandShort",
                          .anim = GEN(LandLowArmed),
                          .swords = nullptr,
                          .mode = Mode::Sync,
                          .start = 1,
                          .end = 16,
                          .bareAnim = GEN(LandLow) };
// the same while Z-targeting with scimitars (side hops): settling into her fighting stance, which follows
const Move kLandShortStance = { "LandShortStance", GEN(LandLowStance), nullptr, Mode::Sync, 1, 16 };
// After a drop of up to 80 units Link sinks deep into his landing for 11 frames ("short_landing"). Hers is a light dip
// of the same landing, over his first 6 frames, then on into her idle.
constexpr f32 kLandDropLinkTo = 9.0f;
const Move kLandDrop = { .name = "LandDrop",
                         .anim = GEN(LandLightArmed),
                         .swords = nullptr,
                         .mode = Mode::Sync,
                         .start = 1,
                         .end = 16,
                         .bareAnim = GEN(LandLight),
                         .linkTo = kLandDropLinkTo };
const Move kLandDropStance = { .name = "LandDropStance",
                               .anim = GEN(LandLightStance),
                               .swords = nullptr,
                               .mode = Mode::Sync,
                               .start = 1,
                               .end = 16,
                               .linkTo = kLandDropLinkTo };
// Hurt by a long fall, Link crouches (his landing up to frame 8), holds it shaking while he is stunned, then plays the
// rest to get up. Hers (generated): a hard landing into a deep squat, folded over her knees, head down, arms hanging;
// breathing hard (and shaking as he does); standing up into her idle.
constexpr f32 kHurtCrouchFrame = 8.0f;
const Move kHurtLand = { .name = "HurtLand",
                         .anim = GEN(HurtLandArmed),
                         .swords = nullptr,
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = 7,
                         .bareAnim = GEN(HurtLand),
                         .blendIn = 0.5f }; // the impact can't wait
const Move kHurtHold = { .name = "HurtHold", .anim = GEN(HurtHoldArmed), .mode = Mode::Loop, .bareAnim = GEN(HurtHold) };
const Move kHurtRise = { .name = "HurtRise",
                         .anim = GEN(HurtRiseArmed),
                         .swords = nullptr,
                         .mode = Mode::Sync,
                         .start = 0,
                         .end = 9,
                         .bareAnim = GEN(HurtRise),
                         .linkFrom = kHurtCrouchFrame };
// after a long drop, moving on (Link rolls): a quick dip, then her walk/run takes over
const Move kLandQuick = { "LandQuick", GEN(Jump), GEN_SWORDS(Jump), Mode::Once, 4, 14, 2.0f };
constexpr s32 kLandQuickFrames = 5;
constexpr f32 kLongDrop = 80.0f; // Link's own threshold: he rolls out of longer drops when moving on
// getting hurt
const Move kDamage = {
    "Damage", GEN(Damage), GEN_SWORDS(Damage), Mode::Sync, 0, 7
}; // stops before she raises her sword again
const Move kKnockdown = { "Knockdown", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 0, 12 };
const Move kDown = { "Down", GEN(Defeat), GEN_SWORDS(Defeat), Mode::Sync, 12, 22 };
// getting up from the kneel (the guard's kneel played backwards), over Link's whole get-up
const Move kGetUp = { "GetUp", GEN(Crouch), nullptr, Mode::Sync, 10, 0 };
// Rolling into a wall, Link bounces back onto his backside, sits a moment and gets up, all in one animation. Hers
// (generated) falls back onto her backside (the end of the fighter's defeat), bounces and settles, then rises through
// the guard's kneel into her idle; each part follows his. He then stands still for a few frames: she goes on into her
// idle at once.
const Move kBonkUp = { .name = "BonkUp",
                       .anim = GEN(BonkArmed),
                       .swords = GEN_SWORDS(BonkArmed),
                       .mode = Mode::Sync,
                       .start = 12,
                       .end = 23,
                       .bareAnim = GEN(Bonk),
                       .linkFrom = 13.5f,
                       .linkTo = 24.0f };
const Move kBonkSit = { .name = "BonkSit",
                        .anim = GEN(BonkArmed),
                        .swords = GEN_SWORDS(BonkArmed),
                        .mode = Mode::Sync,
                        .start = 3,
                        .end = 12,
                        .bareAnim = GEN(Bonk),
                        .linkFrom = 4.5f,
                        .linkTo = 13.5f,
                        .next = &kBonkUp };
const Move kBonk = { .name = "Bonk",
                     .anim = GEN(BonkArmed),
                     .swords = GEN_SWORDS(BonkArmed),
                     .mode = Mode::Sync,
                     .start = 0,
                     .end = 3,
                     .bareAnim = GEN(Bonk),
                     .linkTo = 4.5f,
                     .next = &kBonkSit };
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
    // "250jump" is the jump up to a ledge; "landing_roll" is handled by her own landing logic
    { { "climb", "water_fall", "uma_", "swimer_", "step_up", "250jump", "landing_roll" }, nullptr },
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
    { { "Rside_jump_end" }, &kSideHopLandR }, // (also his landing with an enemy targeted, "..._endR")
    { { "Lside_jump_end" }, &kSideHopLandL },
    { { "side_jump_end" }, &kLandShort },
    { { "Rside_jump" }, &kSideHopR },
    { { "Lside_jump" }, &kSideHopL },
    { { "side_jump" }, &kAirborne },
    { { "defense_hit", "defense_long_hit" }, &kBlock }, // a hit blocked with the shield
    { { "derth" }, &kDeath },
    { { "down_wake" }, &kGetUp },
    { { "hip_down" }, &kBonk }, // rolled into a wall
    { { "_downB" }, &kDown },
    { { "_downA", "ice_down" }, &kKnockdown },
    { { "_hit", "_shit", "damage" }, &kDamage },
    // Link's "normal_landing" is his fall, and also his touchdown after a long drop (see Nabooru_OnPlayerUpdate)
    { { "run_jump_end", "front_jump_end", "anchor_landing" }, &kLandShort },
    { { "short_landing" }, &kLandDrop }, // after a drop of up to 80 units
    { { "normal_run_jump" }, &kRunJump }, // leaping off a ledge from a run
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

// Generated resources live in a mod archive that may not be installed; checked once per path.
bool HasResource(const char* path) {
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
    if (result != nullptr && !HasResource(result->anim)) {
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
// Below those speeds she walks (or sidesteps) with quicker, shorter steps, like Link, instead of slow full strides:
// her step rate only drops to this fraction of the natural one at a standstill, her stride makes up the rest.
constexpr f32 kMinCadence = 0.55f;
constexpr f32 kMovingThreshold = 0.5f;
constexpr f32 kRunThreshold = 4.5f;
constexpr f32 kAttackStepThreshold = 1.0f; // Link's attacks slide him; above this, her legs step along
constexpr f32 kLayerFadeStep = 0.25f;
constexpr s32 kKeepFoldedFrames = 3; // starting to walk from her folded-arms idle, her arms stay folded this long
// Strafing slowly she leans much less than the fighter's sidestep: at up to kStraightSpeed this much of the lean is
// taken out, none of it from kLeanSpeed (Link's full sideways speed is 5.4).
constexpr f32 kStraightenMax = 0.8f;
constexpr f32 kStraightSpeed = 2.0f;
constexpr f32 kLeanSpeed = 4.5f;
constexpr f32 kStraightenStep = 0.15f;

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
f32 sFadeWeight = 0.0f; // her arms have their own (a move can bring them in more slowly than the rest of her)
f32 sFadeStep = kCrossfadeStep;
f32 sFadeArmsWeight = 0.0f;
f32 sFadeArmsStep = kCrossfadeStep;
f32 sStraighten = 0.0f; // how much of her sideways lean is taken out (strafing slowly)

bool sShowSwords = false;
Vec3f sPrevPos;
const void* sLinkPrevAnim = nullptr; // Link's animation can stall while he moves (see Nabooru_OnPlayerUpdate)
f32 sLinkPrevFrame = 0.0f;
s32 sLinkStalledFrames = 0;
s32 sLandingFrames = 0; // remaining frames of a quick landing
s32 sKeepFolded = 0;    // remaining frames her arms stay folded as she starts walking
bool sWasShielding = false;
bool sInAir = false;
f32 sAirPeakY = 0.0f;    // highest point since leaving the ground
bool sHurtLanding = false; // landing from a fall that hurt
const Move* sHop = nullptr; // the side hop under way, and the height she took off from
f32 sHopFloorY = 0.0f;
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
    sFadeWeight = sFadeArmsWeight = 0.0f;
}

f32 LastFrame(const Move* move) {
    return Animation_GetLastFrame((AnimationHeader*)move->anim);
}

// the animation for a move, armed or empty-handed
const char* AnimFor(const Move* move) {
    return (!sShowSwords && move->bareAnim != nullptr && HasResource(move->bareAnim)) ? move->bareAnim : move->anim;
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

Quat QMul(const Quat& a, const Quat& b) {
    return { a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
             a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w };
}

Quat QConj(const Quat& q) {
    return { q.w, -q.x, -q.y, -q.z };
}

Vec3f QRotate(const Quat& q, Vec3f v) {
    Quat r = QMul(QMul(q, { 0.0f, v.x, v.y, v.z }), QConj(q));
    return { r.x, r.y, r.z };
}

Vec3f Normalized(Vec3f v) {
    f32 len = sqrtf(v.x * v.x + v.y * v.y + v.z * v.z);
    return (len > 0.0f) ? Vec3f{ v.x / len, v.y / len, v.z / len } : v;
}

// `weight` of the shortest rotation that turns direction `from` into direction `to` (both unit length)
Quat PartialTurn(Vec3f from, Vec3f to, f32 weight) {
    Vec3f axis = { from.y * to.z - from.z * to.y, from.z * to.x - from.x * to.z, from.x * to.y - from.y * to.x };
    f32 sinAngle = sqrtf(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z);
    f32 angle = atan2f(sinAngle, from.x * to.x + from.y * to.y + from.z * to.z) * weight;
    if (sinAngle < 1e-6f) {
        return { 1.0f, 0.0f, 0.0f, 0.0f };
    }
    f32 s = sinf(angle * 0.5f) / sinAngle;
    return { cosf(angle * 0.5f), axis.x * s, axis.y * s, axis.z * s };
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
    // (interpolated: a move played slower than its frames, or synced to Link, would otherwise hold each frame and jump)
    if (move->mode == Mode::Loop) {
        Animation_Change(&layer.skel, (AnimationHeader*)anim, 1.0f, 0.0f, last, ANIMMODE_LOOP_INTERP, 0.0f);
    } else {
        // frames are set by hand every update
        Animation_Change(&layer.skel, (AnimationHeader*)anim, 0.0f, move->start, last, ANIMMODE_ONCE_INTERP, 0.0f);
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

f32 LinkEndFrame(Player* player) {
    SkelAnime* link = &player->skelAnime;
    return (link->endFrame > 0.0f) ? link->endFrame : link->animLength - 1.0f;
}

// fraction [0, 1] of Link's current animation that has played
f32 LinkAnimProgress(Player* player) {
    f32 end = LinkEndFrame(player);
    return (end > 0.0f) ? CLAMP(player->skelAnime.curFrame / end, 0.0f, 1.0f) : 0.0f;
}

// Link's frame where a Sync move ends
f32 LinkTo(const Move* move, Player* player) {
    return (move->linkTo >= 0.0f) ? move->linkTo : LinkEndFrame(player);
}

// progress through a Sync move: it covers Link's frames linkFrom..linkTo, or else the first `span` of his animation
f32 MoveProgress(const Move* move, Player* player) {
    if (move->linkFrom > 0.0f || move->linkTo >= 0.0f) {
        f32 to = LinkTo(move, player);
        return (to > move->linkFrom)
                   ? CLAMP((player->skelAnime.curFrame - move->linkFrom) / (to - move->linkFrom), 0.0f, 1.0f)
                   : 1.0f;
    }
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
    // Link's direction of travel turns towards the stick over several frames: starting a sidestep it still points
    // ahead, while he already plays his sidestep
    const char* name = LinkAnimName(player->skelAnime.animation);
    if (name != nullptr && strstr(name, "side_walk") != nullptr && rel != 0) {
        return (rel > 0) ? Dir::Left : Dir::Right;
    }
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
    return (sShowSwords && armed != nullptr && HasResource(armed->anim)) ? armed : move;
}

f32 sStride = 1.0f; // her stride, as a fraction of the walk's (or sidestep's) own, set by LocomotionMove

// The step rate for walking at `speed` with a cycle whose natural speed is `reference`; sets sStride.
f32 Cadence(f32 speed, f32 reference) {
    f32 natural = speed / reference;
    if (natural >= 1.0f) {
        sStride = 1.0f;
        return MIN(natural, 3.0f);
    }
    f32 cadence = kMinCadence + (1.0f - kMinCadence) * natural;
    sStride = natural / cadence;
    return cadence;
}

// Locomotion when no rule matches. Returns the move and its loop speed; `legsOnly` asks for her normal upper body
// (used when walking sideways without her scimitars, so she doesn't hold a fighting pose with empty hands).
const Move* LocomotionMove(Player* player, f32* loopSpeed, bool* legsOnly) {
    f32 speed = player->actor.speedXZ;
    *legsOnly = false;
    *loopSpeed = 1.0f;
    sStride = 1.0f;
    if (speed <= kMovingThreshold) {
        if (player->stateFlags1 & PLAYER_STATE1_TALKING) {
            return &kHips;
        }
        if (IsTargeting(player) && sShowSwords && HasResource(kStance.anim)) {
            return &kStance;
        }
        const Move* idle = sShowSwords ? &kIdleSwords : &kIdle;
        return HasResource(idle->anim) ? idle : &kHips;
    }

    // Without Z-targeting she faces where she goes, except when sliding backwards (e.g. out of a backflip).
    Dir dir = TravelDirection(player);
    if (!IsTargeting(player) && dir != Dir::Back) {
        dir = Dir::Forward;
    }
    switch (dir) {
        case Dir::Forward:
            if (speed > kRunThreshold && HasResource(kRun.anim)) {
                *loopSpeed = CLAMP(speed / kRunReferenceSpeed, 0.75f, 3.0f);
                return &kRun;
            }
            if (IsTargeting(player) && sShowSwords && HasResource(kFightWalk.anim)) {
                *loopSpeed = CLAMP(speed / kFightWalkReferenceSpeed, 0.5f, 2.5f);
                return &kFightWalk;
            }
            *loopSpeed = Cadence(speed, kWalkReferenceSpeed);
            return &kWalk;
        case Dir::Back:
            if (speed > kRunThreshold && HasResource(kRunBack.anim)) {
                *loopSpeed = -CLAMP(speed / kRunReferenceSpeed, 0.75f, 3.0f);
                return &kRunBack;
            }
            if (sShowSwords && HasResource(kFightWalk.anim)) {
                *loopSpeed = -CLAMP(speed / kFightWalkReferenceSpeed, 0.5f, 2.5f);
                return &kFightWalk;
            }
            *loopSpeed = -Cadence(speed, kWalkReferenceSpeed);
            return &kWalk;
        case Dir::Left:
        case Dir::Right:
            if (!HasResource(kStrafe.anim)) {
                *loopSpeed = Cadence(speed, kWalkReferenceSpeed);
                return &kWalk;
            }
            // the fighter's circling steps go to her left when played backwards; right is the mirrored copy
            *loopSpeed = -Cadence(speed, kStrafeReferenceSpeed);
            *legsOnly = !sShowSwords;
            return (dir == Dir::Right && HasResource(kStrafeMirror.anim)) ? &kStrafeMirror : &kStrafe;
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
           move == &kJumpSlashEnd || move == &kLand || move == &kLandShort || move == &kLandShortStance ||
           move == &kLandDrop || move == &kLandDropStance ||
           move == &kSideHopLandR || move == &kSideHopLandL;
}

void UpdateEyes() {
    if (DECR(sBlinkTimer) == 0) {
        sBlinkTimer = Rand_S16Offset(60, 60);
    }
    sEyeIndex = (sBlinkTimer < ARRAY_COUNT(sEyeTextures)) ? sBlinkTimer : 0;
}

bool SampleJointRotation(const char* path, s32 frame, s32 joint, Vec3s* out);

// The average pose of a cycle (each joint's mean rotation, the root's mean position); computed once per animation.
const Vec3s* MeanPose(const char* anim) {
    static std::unordered_map<const char*, std::vector<Vec3s>> sCache;
    auto it = sCache.find(anim);
    if (it != sCache.end()) {
        return it->second.data();
    }
    std::vector<Vec3s> mean(NB_LIMB_MAX);
    s32 frames = Animation_GetLastFrame((AnimationHeader*)anim) + 1;
    Vec3f root = { 0.0f, 0.0f, 0.0f };
    for (s32 j = 0; j < NB_LIMB_MAX; j++) {
        Quat sum = { 0.0f, 0.0f, 0.0f, 0.0f }, first = { 1.0f, 0.0f, 0.0f, 0.0f };
        for (s32 f = 0; f < frames; f++) {
            Vec3s value;
            SampleJointRotation(anim, f, j, &value);
            if (j == 0) {
                root = { root.x + value.x, root.y + value.y, root.z + value.z };
                continue;
            }
            Quat q = ToQuat(value);
            if (f == 0) {
                first = q;
            }
            f32 sign = (q.w * first.w + q.x * first.x + q.y * first.y + q.z * first.z < 0.0f) ? -1.0f : 1.0f;
            sum = { sum.w + sign * q.w, sum.x + sign * q.x, sum.y + sign * q.y, sum.z + sign * q.z };
        }
        if (j == 0) {
            mean[0] = { (s16)(root.x / frames), (s16)(root.y / frames), (s16)(root.z / frames) };
        } else {
            f32 len = sqrtf(sum.w * sum.w + sum.x * sum.x + sum.y * sum.y + sum.z * sum.z);
            mean[j] = ToEuler({ sum.w / len, sum.x / len, sum.y / len, sum.z / len });
        }
    }
    return sCache.emplace(anim, std::move(mean)).first->second.data();
}

// Shorter steps: every joint moves only `stride` of the way from the cycle's average pose to where the cycle has it.
void ShortenStride(Layer& layer, f32 stride) {
    if (!HasResource(layer.anim)) {
        return;
    }
    const Vec3s* mean = MeanPose(layer.anim);
    MixTranslation(layer.joints[0], mean[0], 1.0f - stride);
    for (s32 j = 1; j < NB_LIMB_MAX; j++) {
        MixRotation(layer.joints[j], mean[j], 1.0f - stride);
    }
}

// The fighter's sidestep (like her stance) leans to one side: hips rolled, spine tilted; mirrored for the other
// direction. Upright = the average of the two: her hips' up direction and her spine's direction, in model space.
bool StrafeUpright(Vec3f* rootUp, Vec3f* spine) {
    static bool sDone = false, sOk = false;
    static Vec3f sRootUp, sSpine;
    if (!sDone) {
        sDone = true;
        Vec3f up = { 0.0f, 0.0f, 0.0f }, sp = { 0.0f, 0.0f, 0.0f };
        for (const Move* move : { &kStrafe, &kStrafeMirror }) {
            if (!HasResource(move->anim)) {
                return false;
            }
            s32 frames = Animation_GetLastFrame((AnimationHeader*)move->anim) + 1;
            for (s32 f = 0; f < frames; f++) {
                Vec3s rootRot, torsoRot;
                SampleJointRotation(move->anim, f, NB_LIMB_ROOT, &rootRot);
                SampleJointRotation(move->anim, f, NB_LIMB_TORSO, &torsoRot);
                Quat root = ToQuat(rootRot);
                Vec3f u = QRotate(root, { 0.0f, 1.0f, 0.0f });
                Vec3f d = QRotate(QMul(root, ToQuat(torsoRot)), { 1.0f, 0.0f, 0.0f }); // the spine runs along +x
                up = { up.x + u.x, up.y + u.y, up.z + u.z };
                sp = { sp.x + d.x, sp.y + d.y, sp.z + d.z };
            }
        }
        sRootUp = Normalized(up);
        sSpine = Normalized(sp);
        sOk = true;
    }
    *rootUp = sRootUp;
    *spine = sSpine;
    return sOk;
}

// Takes `weight` of her sideways lean out: her hips and spine turn part of the way upright, her legs keep their
// direction (so her feet stay where they are) and everything above the torso goes along with it.
void Straighten(Vec3s* joints, f32 weight) {
    Vec3f uprightRoot, uprightSpine;
    if (!StrafeUpright(&uprightRoot, &uprightSpine)) {
        return;
    }
    Quat root = ToQuat(joints[NB_LIMB_ROOT]);
    Quat torso = QMul(root, ToQuat(joints[NB_LIMB_TORSO])); // in model space
    Quat newRoot = QMul(PartialTurn(QRotate(root, { 0.0f, 1.0f, 0.0f }), uprightRoot, weight), root);
    Quat newTorso = QMul(PartialTurn(QRotate(torso, { 1.0f, 0.0f, 0.0f }), uprightSpine, weight), torso);
    Quat undo = QMul(QConj(newRoot), root);
    for (s32 limb : { NB_LIMB_L_THIGH, NB_LIMB_R_THIGH, NB_LIMB_WAIST }) {
        joints[limb] = ToEuler(QMul(undo, ToQuat(joints[limb])));
    }
    joints[NB_LIMB_TORSO] = ToEuler(QMul(QConj(newRoot), newTorso));
    joints[NB_LIMB_ROOT] = ToEuler(newRoot);
}

// her final pose: the body, the overlay over its masked joints, and the pose she's blending out of
void BlendLayers() {
    for (s32 i = 0; i < NB_LIMB_MAX; i++) {
        sFinalJoints[i] = sBody.joints[i];
        if (sOverlayWeight > 0.0f && InMask(sMask, i)) {
            MixRotation(sFinalJoints[i], sOverlay.joints[i], sOverlayWeight);
        }
    }
    if (sStraighten > 0.0f) {
        Straighten(sFinalJoints, sStraighten);
    }
    if (sFadeWeight > 0.0f) {
        MixTranslation(sFinalJoints[0], sFadeFrom[0], sFadeWeight);
    }
    for (s32 i = 1; i < NB_LIMB_MAX; i++) {
        f32 weight = InMask(Mask::Arms, i) ? sFadeArmsWeight : sFadeWeight;
        if (weight > 0.0f) {
            MixRotation(sFinalJoints[i], sFadeFrom[i], weight);
        }
    }
}

// blend from her current pose into whatever comes next
void StartCrossfade() {
    memcpy(sFadeFrom, sFinalJoints, sizeof(sFadeFrom));
    sFadeWeight = sFadeArmsWeight = 1.0f;
}

bool IsLinkTouchdown(const void* anim) {
    const char* name = LinkAnimName(anim);
    return name != nullptr && (strcmp(name, "normal_landing") == 0 || strcmp(name, "normal_landing_free") == 0);
}

// Link's side hop leaves the ground on its first frame. While her hop still crouches and pushes off, her model is
// lowered by as much as he has risen, so her feet stay where she took off.
void KeepFeetDown(Player* player) {
    if (sBody.move != &kSideHopR && sBody.move != &kSideHopL) {
        sHop = nullptr;
        return;
    }
    if (sHop != sBody.move) {
        sHop = sBody.move;
        sHopFloorY = player->actor.world.pos.y; // (he only moves on the next frame)
    }
    const char* name = LinkAnimName(player->skelAnime.animation); // (not while she holds the hop as he falls on)
    if (name != nullptr && strstr(name, "side_jump") != nullptr && player->skelAnime.curFrame <= kSideHopGroundFrames &&
        player->actor.scale.y > 0.0f) {
        f32 risen = (player->actor.world.pos.y - sHopFloorY) / player->actor.scale.y; // model units
        sFinalJoints[0].y -= (s16)CLAMP(risen, 0.0f, 2000.0f);
    }
}

void RestoreSpinCharge();

void Nabooru_OnPlayerUpdate() {
    RestoreSpinCharge();
    Player* player = GET_PLAYER(gPlayState);
    if (player == nullptr) {
        return;
    }
    InitLayer(sBody);
    InitLayer(sOverlay);
    UpdateEyes();

    // Her scimitars are out whenever Link has a melee weapon in hand. She blocks with them too (Link with his shield,
    // whether his sword is out or not): only while she blocks, Link's sword stays where it was.
    bool shielding = player->stateFlags1 & PLAYER_STATE1_SHIELDING;
    sShowSwords = player->leftHandType == PLAYER_MODELTYPE_LH_SWORD ||
                  player->leftHandType == PLAYER_MODELTYPE_LH_SWORD_2 ||
                  player->leftHandType == PLAYER_MODELTYPE_LH_BGS ||
                  player->leftHandType == PLAYER_MODELTYPE_LH_HAMMER || shielding;
    // Link's shield is up the moment he raises it: so are her blades
    bool raisingGuard = shielding && !sWasShielding;
    sWasShielding = shielding;

    if (!sForcedName.empty()) {
        SetMove(sBody, &sForced);
        StepLayer(sBody, player, 0.0f);
        sOverlayWeight = 0.0f;
        sFadeWeight = sFadeArmsWeight = 0.0f;
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
    while (body != nullptr && body->next != nullptr && link->curFrame >= LinkTo(body, player)) {
        body = body->next; // the next part of Link's animation
    }
    if (body != nullptr && body->next == nullptr && body->linkTo >= 0.0f && link->curFrame > body->linkTo) {
        body = nullptr; // done before Link: on to her idle (e.g. folding her arms) rather than holding still
    }
    const Move* overlay = nullptr;
    f32 bodySpeed = 1.0f, overlaySpeed = 1.0f;
    bool freezeBody = false;
    bool startFolded = false; // her arms stay folded as she sets off

    if (YieldsToLocomotion(body) && sLinkStalledFrames >= 2 && moving) {
        body = nullptr;
    }
    if (body == &kAirborne && (sBody.move == &kBackflip || sBody.move == &kJumpSlashAir || sBody.move == &kSideHopR ||
                               sBody.move == &kSideHopL)) {
        // Link briefly plays his fall between a flip and its landing (or a side hop and its landing, when the ground
        // falls away): finish the flip, hold the hop's last frame in the air instead
        body = sBody.move;
        freezeBody = true;
    } else if (body == &kAirborne && grounded && IsLinkTouchdown(link->animation)) {
        body = &kLand; // Link's fall animation played on the ground is his landing after a long drop
    }
    // A fall that hurt: Link stops his landing at its crouch while he shakes, then plays the rest to get up.
    if (body == &kLand && HasResource(kHurtLand.anim)) {
        sHurtLanding |= link->endFrame <= kHurtCrouchFrame;
        if (sHurtLanding) {
            body = (link->endFrame > kHurtCrouchFrame)   ? &kHurtRise
                   : (link->curFrame < kHurtCrouchFrame) ? &kHurtLand
                                                         : &kHurtHold;
        }
    } else {
        sHurtLanding = false;
    }
    if (body == &kTired && sShowSwords) {
        body = &kTiredSwords;
    }
    if (body == &kLandShort && sShowSwords && IsTargeting(player) && HasResource(kLandShortStance.anim) &&
        HasResource(kStance.anim)) {
        body = &kLandShortStance;
    }

    // Landing after a long drop while moving on (Link rolls): a quick dip into her walk/run. (After shorter drops she
    // lands as from a hop, giving way to her walk/run if she moves on.)
    if (!grounded) {
        sAirPeakY = sInAir ? MAX(sAirPeakY, player->actor.world.pos.y) : player->actor.world.pos.y;
        sInAir = true;
        sLandingFrames = 0;
    } else if (sInAir) {
        sInAir = false;
        if (sAirPeakY - player->actor.world.pos.y > kLongDrop && moving && HasResource(kLandQuick.anim)) {
            sLandingFrames = kLandQuickFrames;
        }
    }
    if (sLandingFrames > 0 && (body == nullptr || body == &kAirborne || body == &kLand)) {
        sLandingFrames--;
        body = &kLandQuick;
    } else if (body != &kLandQuick) {
        sLandingFrames = 0;
    }
    if (body == &kLandDrop && sShowSwords && IsTargeting(player) && HasResource(kLandDropStance.anim) &&
        HasResource(kStance.anim)) {
        body = &kLandDropStance;
    }

    f32 stride = 1.0f;
    if (body == nullptr) {
        bool legsOnly = false;
        body = Locomotion(player, &bodySpeed, &legsOnly);
        stride = sStride;
        if (shielding && HasResource(kBlock.anim)) {
            if (moving) {
                overlay = &kBlock; // guard up while walking: her arms only, so she still looks ahead
                sMask = Mask::Arms;
            } else if (IsTargeting(player)) {
                body = &kBlock;
            } else {
                body = HasResource(kKneel.anim) ? &kKneel : &kBlock; // Link crouches behind his shield
                overlay = &kBlock;
                sMask = Mask::Upper;
            }
        } else if (legsOnly) {
            // empty-handed: her relaxed upper body over the sideways steps
            overlay = HasResource(kIdle.anim) ? &kIdle : &kHips;
            sMask = Mask::Upper;
        }
        // Setting off from her folded-arms idle, her arms stay folded for a moment: a light tap of the stick doesn't
        // swing them about.
        if (moving && !shielding && !sShowSwords && HasResource(kCrossedArms.anim)) {
            if (sBody.move == &kCrossedArms || (sBody.move == &kFoldArms && OnceDone(sBody))) {
                sKeepFolded = kKeepFoldedFrames;
                startFolded = true;
            }
            if (sKeepFolded > 0) {
                sKeepFolded--;
                overlay = &kCrossedArms;
                sMask = legsOnly ? Mask::Upper : Mask::Arms;
            }
        } else {
            sKeepFolded = 0;
        }
    } else {
        sKeepFolded = 0;
        if (IsAttack(body) && stepDistance > kAttackStepThreshold && HasResource(kFightWalk.anim)) {
            // Link's slashes carry him forward: let her legs step instead of sliding.
            overlay = &kFightWalk;
            overlaySpeed = CLAMP(stepDistance / kFightWalkReferenceSpeed, 0.5f, 2.5f);
            sMask = Mask::Legs;
        }
    }

    // Standing still empty-handed, she folds her arms (unless they still are: she only tapped the stick).
    if (body == &kIdle && overlay == nullptr && HasResource(kFoldArms.anim) && HasResource(kCrossedArms.anim)) {
        bool folded = sBody.move == &kCrossedArms || (sBody.move == &kFoldArms && OnceDone(sBody)) ||
                      (sOverlay.move == &kCrossedArms && sOverlayWeight >= 1.0f);
        body = folded ? &kCrossedArms : &kFoldArms;
    }
    f32 foldedFrame = (sBody.move == &kCrossedArms) ? sBody.skel.curFrame : 0.0f;

    // The fighter's moves end in her fighting stance; with empty hands, let her arms come down once she's landed.
    if (!sShowSwords && overlay == nullptr && HasResource(kIdle.anim)) {
        bool late = body == &kLand && sBody.move == body && sBody.skel.curFrame >= 9.0f;
        if (late) {
            overlay = &kIdle;
            sMask = Mask::Arms;
        }
    }

    bool hadMove = sBody.move != nullptr; // nothing to blend from on her first frame
    // a move that carries on with the same animation where the last one stopped (a flip's flight, then its landing)
    // needs no blending
    bool continues = hadMove && body != sBody.move && body->mode != Mode::Loop && AnimFor(body) == sBody.anim &&
                     fabsf(body->start - sBody.skel.curFrame) <= 1.0f;
    bool changed = SetMove(sBody, body) && hadMove && !continues;
    if (freezeBody) {
        SkelAnime_Update(&sBody.skel);
    } else {
        StepLayer(sBody, player, bodySpeed);
    }
    if (stride < 1.0f && (body == &kWalk || body == &kArmedWalk || body == &kStrafe || body == &kStrafeMirror)) {
        ShortenStride(sBody, stride);
    }

    if (startFolded) {
        // her arms stay as they were: folded, at once
        SetMove(sOverlay, overlay);
        Animation_Change(&sOverlay.skel, (AnimationHeader*)sOverlay.anim, 1.0f, foldedFrame, LastFrame(overlay),
                         ANIMMODE_LOOP_INTERP, 0.0f);
        sOverlayWeight = 1.0f;
    } else if (overlay != nullptr) {
        changed |= SetMove(sOverlay, overlay) && sOverlayWeight > 0.0f;
        sOverlayWeight = MIN(sOverlayWeight + kLayerFadeStep, 1.0f);
    } else {
        sOverlayWeight = MAX(sOverlayWeight - kLayerFadeStep, 0.0f);
    }
    if (sOverlayWeight > 0.0f && sOverlay.move != nullptr) {
        StepLayer(sOverlay, player, overlaySpeed);
    }

    if (raisingGuard) {
        sFadeWeight = sFadeArmsWeight = 0.0f;
        if (overlay != nullptr) {
            sOverlayWeight = 1.0f;
        }
    } else if (changed) {
        StartCrossfade();
        sFadeStep = sBody.move->blendIn;
        sFadeArmsStep = (sBody.move->armsBlendIn > 0.0f) ? sBody.move->armsBlendIn : sFadeStep;
    }
    sFadeWeight = MAX(sFadeWeight - sFadeStep, 0.0f);
    sFadeArmsWeight = MAX(sFadeArmsWeight - sFadeArmsStep, 0.0f);

    f32 straighten = 0.0f;
    if (sBody.move == &kStrafe || sBody.move == &kStrafeMirror) {
        straighten = kStraightenMax *
                     CLAMP((kLeanSpeed - player->actor.speedXZ) / (kLeanSpeed - kStraightSpeed), 0.0f, 1.0f);
    }
    Math_StepToF(&sStraighten, straighten, kStraightenStep);
    BlendLayers();
    KeepFeetDown(player);
    if (sBody.move == &kHurtHold) {
        // shaking as Link does (his model is moved up and down 40 units every other frame)
        sFinalJoints[0].y += (player->av2.fallDamageStunTimer & 1) ? 40 : -40;
    }
}

// ------------------------------------------------------------------------------------------------ voice

// Link's (adult) voice clips that have a counterpart in Nabooru's own (from her Spirit Temple cutscenes) or the Gerudo
// fighter's. Everything else (climbing, pushing, jumping off ledges, magic, sneezing, drinking, ...) stays Link's.
struct VoiceSwap {
    u16 link;
    u16 hers;
    // Link's shouts double as his grunt for rolls, hops, jumps, throws and lifts; she only shouts when she attacks and
    // is silent for the rest
    bool attackOnly = false;
};

const VoiceSwap kVoiceSwaps[] = {
    { NA_SE_VO_LI_SWORD_N, NA_SE_EN_GERUDOFT_ATTACK, true },  // slashes
    { NA_SE_VO_LI_SWORD_L, NA_SE_EN_GERUDOFT_ATTACK, true },  // strong slashes, spin and jump attacks
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

bool IsLinkVoice(u16 sfxId) {
    u16 id = sfxId | SFX_FLAG;
    return id >= NA_SE_VO_LI_SWORD_N && id <= NA_SE_VO_LI_ELECTRIC_SHOCK_LV;
}

// Link's attack animations are all named "...kiru..." (cut) or "hammer..."
bool IsAttacking(Player* player) {
    const char* name = LinkAnimName(player->skelAnime.animation);
    return name != nullptr && (strstr(name, "kiru") != nullptr || strstr(name, "hammer") != nullptr);
}

// Link has a single voice channel: a new line cuts the one playing unless that one is more important (a hit plays his
// fall scream and then his hurt grunt in the same frame; only the grunt is heard). Her clips are in other sound banks,
// so that is done by hand.
struct {
    u16 id; // as played, after the swap
    u8 importance;
    u32 frame;
} sLastVoice = {};

// Returns false to silence the sound. Some sounds are played with SFX_FLAG cleared (repeated every frame, e.g. the
// scream while electrocuted); that is kept.
bool SwapVoice(u16* sfxId, Vec3f* pos) {
    if (!IsLinkVoice(*sfxId) || IsDarkLink(pos)) {
        return true;
    }
    Player* player = (gPlayState != nullptr) ? GET_PLAYER(gPlayState) : nullptr;
    bool fromHer = player != nullptr && pos == &player->actor.projectedPos;
    u16 link = *sfxId;
    u16 id = *sfxId | SFX_FLAG;
    for (const VoiceSwap& swap : kVoiceSwaps) {
        if (id == swap.link) {
            // (Link's last blow on Ganon is played centered)
            if (swap.attackOnly && fromHer && !IsAttacking(player)) {
                return false;
            }
            *sfxId = (swap.hers & ~SFX_FLAG) | (*sfxId & SFX_FLAG);
            break;
        }
    }
    if (!fromHer) {
        return true;
    }

    u8 importance = gSoundParams[SFX_BANK_SHIFT(link)][SFX_INDEX(link)].importance;
    u32 frame = gPlayState->gameplayFrames;
    if (sLastVoice.id != 0 && sLastVoice.id != *sfxId &&
        (sLastVoice.frame == frame || Audio_IsSfxPlaying(sLastVoice.id))) {
        if (importance < sLastVoice.importance) {
            return false;
        }
        Audio_StopSfxByPosAndId(pos, sLastVoice.id);
    }
    sLastVoice = { *sfxId, importance, frame };
    return true;
}

// ------------------------------------------------------------------------------------------------ item icons

// Her weapons on the B button, in the equipment screen and in item boxes: her scimitars for the Master Sword, the
// Gerudo spear for the Biggoron Sword (generated by the asset tool; Link's icons stay if they're missing).
struct IconSwap {
    s32 item;
    const char* hers;
    void* original = nullptr;
};

IconSwap sIconSwaps[] = {
    { ITEM_SWORD_MASTER, "__OTR__textures/play_as_nabooru/gPlayAsNabooruScimitarsIconTex" },
    { ITEM_SWORD_BGS, "__OTR__textures/play_as_nabooru/gPlayAsNabooruSpearIconTex" },
};

// (done when a game is loaded, as well as when the enhancement is switched: at startup the mod archives may not be
// loaded yet)
void SwapItemIcons(bool hers) {
    for (IconSwap& swap : sIconSwaps) {
        if (swap.original == nullptr) {
            swap.original = gItemIcons[swap.item];
        }
        gItemIcons[swap.item] = (hers && ResourceMgr_FileExists(swap.hers)) ? (void*)swap.hers : swap.original;
    }
}

// Their names on the pause screen (English; other languages keep Link's). On the equipment screen the Biggoron Sword
// is named as ITEM_HEART_PIECE_2 and the (unbroken) Giant's Knife as ITEM_SWORD_BGS.
const char* ItemName(u32 item) {
    static const char* kScimitars = "__OTR__textures/play_as_nabooru/gPlayAsNabooruScimitarsNameENGTex";
    static const char* kSpear = "__OTR__textures/play_as_nabooru/gPlayAsNabooruSpearNameENGTex";
    if (gSaveContext.language != LANGUAGE_ENG || gPlayState == nullptr) {
        return nullptr;
    }
    bool equipment = gPlayState->pauseCtx.pageIndex == PAUSE_EQUIP;
    const char* name = (item == ITEM_SWORD_MASTER)                                              ? kScimitars
                       : (item == ITEM_SWORD_BGS || (equipment && item == ITEM_HEART_PIECE_2)) ? kSpear
                                                                                               : nullptr;
    return (name != nullptr && HasResource(name)) ? name : nullptr;
}

void ChargeAlongScimitars();

void Nabooru_OnPlayerDraw(bool isFirstPerson) {
    if (isFirstPerson || !sBody.ready || gPlayState == nullptr) {
        return;
    }
    ::PlayAsNabooru_DrawModel();
    ChargeAlongScimitars();
}

void RegisterPlayAsNabooru() {
    bool enabled = CVAR_PLAY_AS_NABOORU_VALUE;

    if (!enabled) {
        ReleaseSkeleton();
    }

    COND_HOOK(OnPlayerUpdate, enabled, Nabooru_OnPlayerUpdate);
    COND_HOOK(OnPlayerDraw, enabled, Nabooru_OnPlayerDraw);
    COND_HOOK(OnPlayDestroy, enabled, ReleaseSkeleton);
    COND_HOOK(OnLoadGame, enabled, [](int32_t) { SwapItemIcons(true); });
    if (enabled && gPlayState == nullptr) {
        // not playing yet (startup): the mod archives may still be loading, OnLoadGame swaps them in
    } else {
        SwapItemIcons(enabled);
    }

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

    COND_VB_SHOULD(VB_DRAW_CUSTOM_ITEM_NAME, enabled, {
        u32 item = va_arg(args, u32);
        if (const char* name = ItemName(item)) {
            *should = true;
            memcpy(gPlayState->pauseCtx.nameSegment, name, strlen(name) + 1);
        }
    });

    // Her voice instead of Link's, where she has a counterpart.
    COND_VB_SHOULD(VB_PLAY_SFX, enabled, {
        u16* sfxId = va_arg(args, u16*);
        Vec3f* pos = va_arg(args, Vec3f*);
        *should = SwapVoice(sfxId, pos);
    });
}

static RegisterShipInitFunc initFunc(RegisterPlayAsNabooru, { CVAR_PLAY_AS_NABOORU_NAME });

// ------------------------------------------------------------------------------------------------ scimitars

// Rotation of joint `joint` at `frame` of a (generated) animation resource.
bool SampleJointRotation(const char* path, s32 frame, s32 joint, Vec3s* out) {
    if (path == nullptr || !HasResource(path)) {
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

// Charging a spin attack, Link's sword glows (En_M_Thunder) and sparkles converge on it (Eff_Dust); both are drawn in
// his sword hand's matrix (Player::mf_9E0), where his blade runs along +X from (0, 400, 0). The sparkles go to her
// left scimitar: from the hilt at kBladeHilt of the scimitar limb along the chord of the curved blade (-Y), scaled
// from the length of Link's sword to hers. The glow, a straight tube, is drawn by her instead, bent along both blades.
constexpr Vec3f kBladeHilt = { 480.0f, -250.0f, 0.0f };
constexpr f32 kBladeAngle = -1.847f; // radians about Z from +X to the chord
constexpr f32 kBladeLength = 3300.0f;
constexpr f32 kLinkBladeY = 400.0f;
constexpr f32 kLinkBladeLengths[] = { 4000.0f, 3000.0f, 5500.0f }; // by En_M_Thunder's sword type: Master, Kokiri, BGS
MtxF sBladeMtx[2];    // her scimitars (left, right hand) at the hilt, along the chord...
MtxF sScimitarMtx[2]; // ...and the scimitar limbs themselves: recorded as she is drawn
bool sBladeDrawn[2];
EnMThunder* sHiddenCharge = nullptr; // the charge whose own (straight) glow is hidden for its draw, and its alpha
u8 sHiddenChargeAlpha = 0;

// The glow along a scimitar: En_M_Thunder's charge glow, a six-sided tube narrowing from the hilt end to the tip end
// (a ring of corners at each), rebuilt with kGlowRings rings along the curve of her blade.
constexpr s32 kGlowRings = 11;
constexpr s32 kGlowCorners = 7; // six, the first again where the texture wraps around
// the curve: from her hand through the middle of the blade to its tip (scimitar limb X, Y; the blade lies at
// Z = -kBladeZ for her left one, +kBladeZ for her right one)
constexpr f32 kGlowCurve[][2] = { { 380, 100 },   { 390, -300 },  { 400, -482 }, { 626, -1465 },
                                  { 428, -2462 }, { -67, -2984 }, { -882, -3350 } };
constexpr f32 kBladeZ = 110.0f;
// the original's corners at the hilt end and the tip end (its Y, Z and texture S), texture T at each end
struct GlowCorner {
    f32 y, z, s;
};
constexpr GlowCorner kGlowHiltEnd[kGlowCorners] = { { 0, -480, 512 }, { -312, -240, 643 }, { -312, 240, 893 },
                                                    { 0, 480, 1024 }, { 0, 480, 0 },       { 312, 240, 131 },
                                                    { 312, -240, 381 } };
constexpr GlowCorner kGlowTipEnd[kGlowCorners] = { { 0, -307, 512 }, { -175, -154, 632 }, { -175, 154, 904 },
                                                   { 0, 307, 1024 }, { 0, 307, 0 },       { 175, 154, 120 },
                                                   { 175, -154, 392 } };
constexpr f32 kGlowHiltT = 2054.0f, kGlowTipT = -30.0f;
// En_M_Thunder (Master Sword): the tube is turned 16384 radians about its length and its cross-section scaled thus
constexpr f32 kGlowTurn = 16384.0f;
constexpr f32 kGlowAcross = -1.0f, kGlowOut = -0.7f; // in the blade's plane, out of it
constexpr f32 kGlowPulse[] = { 0.1f, 0.15f, 0.2f, 0.25f, 0.3f, 0.25f, 0.2f, 0.15f };

struct GlowRing {
    f32 x, y;   // on the curve
    f32 nx, ny; // across the blade
    f32 u;      // 0 at the hilt end, 1 at the tip
};

// kGlowRings points spread evenly along a smooth curve (Catmull-Rom) through kGlowCurve
const GlowRing* GlowRings() {
    static GlowRing sRings[kGlowRings];
    static bool sDone = false;
    if (sDone) {
        return sRings;
    }
    constexpr s32 kPoints = ARRAY_COUNT(kGlowCurve), kSteps = 32, kSamples = (kPoints - 1) * kSteps + 1;
    f32 px[kSamples], py[kSamples], length[kSamples];
    for (s32 i = 0; i < kSamples; i++) {
        s32 seg = MIN(i / kSteps, kPoints - 2);
        f32 t = (f32)(i - seg * kSteps) / kSteps;
        const f32* p0 = kGlowCurve[MAX(seg - 1, 0)];
        const f32* p1 = kGlowCurve[seg];
        const f32* p2 = kGlowCurve[seg + 1];
        const f32* p3 = kGlowCurve[MIN(seg + 2, kPoints - 1)];
        f32 out[2];
        for (s32 c = 0; c < 2; c++) {
            out[c] = 0.5f * (2 * p1[c] + (p2[c] - p0[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t * t +
                             (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * t * t * t);
        }
        px[i] = out[0];
        py[i] = out[1];
        length[i] = (i == 0) ? 0.0f : length[i - 1] + sqrtf(SQ(px[i] - px[i - 1]) + SQ(py[i] - py[i - 1]));
    }
    s32 j = 0;
    for (s32 r = 0; r < kGlowRings; r++) {
        f32 at = length[kSamples - 1] * r / (kGlowRings - 1);
        while (j < kSamples - 2 && length[j + 1] < at) {
            j++;
        }
        f32 f = CLAMP((at - length[j]) / MAX(length[j + 1] - length[j], 1e-3f), 0.0f, 1.0f);
        f32 tx = px[j + 1] - px[j], ty = py[j + 1] - py[j], tl = sqrtf(tx * tx + ty * ty);
        sRings[r] = { px[j] + tx * f, py[j] + ty * f, -ty / tl, tx / tl, (f32)r / (kGlowRings - 1) };
    }
    sDone = true;
    return sRings;
}

// The glow's vertices along one blade for this frame (its thickness pulses).
void BuildChargeGlow(Vtx* vtx, bool left, f32 thickness) {
    const GlowRing* rings = GlowRings();
    f32 c = cosf(kGlowTurn), s = sinf(kGlowTurn);
    for (s32 r = 0; r < kGlowRings; r++) {
        const GlowRing& ring = rings[r];
        for (s32 k = 0; k < kGlowCorners; k++) {
            const GlowCorner& a = kGlowHiltEnd[k];
            const GlowCorner& b = kGlowTipEnd[k];
            f32 y = a.y + (b.y - a.y) * ring.u, z = a.z + (b.z - a.z) * ring.u;
            f32 across = (c * y - s * z) * kGlowAcross * thickness;
            f32 out = (s * y + c * z) * kGlowOut * thickness;
            Vtx_t* v = &vtx[r * kGlowCorners + k].v;
            v->ob[0] = (s16)(ring.x + ring.nx * across);
            v->ob[1] = (s16)(ring.y + ring.ny * across);
            v->ob[2] = (s16)((left ? -kBladeZ : kBladeZ) + out);
            v->flag = 0;
            v->tc[0] = (s16)(a.s + (b.s - a.s) * ring.u);
            v->tc[1] = (s16)(kGlowHiltT + (kGlowTipT - kGlowHiltT) * ring.u);
            v->cn[0] = v->cn[1] = v->cn[2] = v->cn[3] = 255;
        }
    }
}

EnMThunder* FindSpinCharge() {
    for (Actor* actor = gPlayState->actorCtx.actorLists[ACTORCAT_ITEMACTION].head; actor != nullptr;
         actor = actor->next) {
        if (actor->id == ACTOR_EN_M_THUNDER && actor->update != nullptr) {
            return (EnMThunder*)actor;
        }
    }
    return nullptr;
}

// Before the charge updates: give it back the alpha its glow had (only its draw saw it hidden).
void RestoreSpinCharge() {
    if (sHiddenCharge != nullptr && gPlayState != nullptr && FindSpinCharge() == sHiddenCharge) {
        sHiddenCharge->chargeAlpha = sHiddenChargeAlpha;
    }
    sHiddenCharge = nullptr;
}

// While a spin attack is charged or released: the glow bent along both her scimitars (the charge's own straight one,
// drawn after her, made invisible), the sparkles converging on her left one.
void ChargeAlongScimitars() {
    Player* player = GET_PLAYER(gPlayState);
    EnMThunder* charge = FindSpinCharge();
    if (!sShowSwords || charge == nullptr || !sBladeDrawn[0] || !sBladeDrawn[1]) {
        return;
    }
    f32 scale = kBladeLength / kLinkBladeLengths[MIN(charge->swordType, ARRAY_COUNT(kLinkBladeLengths) - 1)];
    Matrix_Push();
    Matrix_Put(&sBladeMtx[0]);
    Matrix_Scale(scale, scale, scale, MTXMODE_APPLY);
    Matrix_Translate(0.0f, -kLinkBladeY, 0.0f, MTXMODE_APPLY);
    Matrix_Get(&player->mf_9E0);
    Matrix_Pop();
    if (sHiddenCharge != charge) { // (drawn again without an update, e.g. paused: it's still hidden)
        sHiddenCharge = charge;
        sHiddenChargeAlpha = charge->chargeAlpha;
    }
    charge->chargeAlpha = 0;
    if (sHiddenChargeAlpha > 0) {
        f32 pulse = kGlowPulse[gPlayState->gameplayFrames & 7];
        f32 thickness = scale * ((charge->spinChargePercent >= 0.85f) ? pulse * 6.0f + 1.0f : pulse * 2.0f + 1.0f);
        ::PlayAsNabooru_DrawChargeGlow(gPlayState, charge, sHiddenChargeAlpha, thickness);
    }
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

// For tools: her root translation as drawn (model units).
void PlayAsNabooru_DebugRoot(short* x, short* y, short* z) {
    *x = sFinalJoints[0].x;
    *y = sFinalJoints[0].y;
    *z = sFinalJoints[0].z;
}

// For tools: how much her pose changed since the last call (once per frame): the largest joint rotation in degrees and
// which joint, and how far the root moved (model units). Freezes show up as zeros, pops as spikes.
void PlayAsNabooru_DebugMotion(float* maxDegrees, int* joint, float* rootMove) {
    static Vec3s sPrev[NB_LIMB_MAX];
    *maxDegrees = 0.0f;
    *joint = 0;
    for (s32 i = 1; i < NB_LIMB_MAX; i++) {
        Quat p = ToQuat(sPrev[i]), q = ToQuat(sFinalJoints[i]);
        f32 dot = fabsf(p.w * q.w + p.x * q.x + p.y * q.y + p.z * q.z);
        f32 degrees = 2.0f * acosf(MIN(dot, 1.0f)) * (180.0f / (f32)M_PI);
        if (degrees > *maxDegrees) {
            *maxDegrees = degrees;
            *joint = i;
        }
    }
    *rootMove = sqrtf(SQ((f32)(sFinalJoints[0].x - sPrev[0].x)) + SQ((f32)(sFinalJoints[0].y - sPrev[0].y)) +
                      SQ((f32)(sFinalJoints[0].z - sPrev[0].z)));
    memcpy(sPrev, sFinalJoints, sizeof(sPrev));
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
    Matrix_Get(&sScimitarMtx[leftHand ? 0 : 1]);
    Matrix_Push(); // where a spin attack's sparkles go
    Matrix_Translate(kBladeHilt.x, kBladeHilt.y, kBladeHilt.z, MTXMODE_APPLY);
    Matrix_RotateZ(kBladeAngle, MTXMODE_APPLY);
    Matrix_Get(&sBladeMtx[leftHand ? 0 : 1]);
    sBladeDrawn[leftHand ? 0 : 1] = true;
    Matrix_Pop();
    gSPMatrix(POLY_OPA_DISP++, MATRIX_NEWMTX(play->state.gfxCtx), G_MTX_NOPUSH | G_MTX_LOAD | G_MTX_MODELVIEW);
    gSPDisplayList(POLY_OPA_DISP++, (Gfx*)(leftHand ? gGerudoRedSkelLimbsLimb_00A39CDL_009460
                                                    : gGerudoRedSkelLimbsLimb_00A360DL_0097D0));
    Matrix_Pop();
    CLOSE_DISPS(play->state.gfxCtx);
}

// A spin attack's charge glow along both scimitars (see ChargeAlongScimitars), drawn as En_M_Thunder draws its own:
// with its display list, for the material (its textures are only known to the display list). That calls segment 9
// (the texture scroll) just before drawing its straight tube: there go her bent tube, then a transparent colour for
// the straight one.
static void PlayAsNabooru_DrawChargeGlow(PlayState* play, EnMThunder* charge, u8 alpha, f32 thickness) {
    static const ALIGN_ASSET(2) char kChargeDL[] = "__OTR__objects/gameplay_keep/gSpinAttackChargingDL";
    GraphicsContext* gfxCtx = play->state.gfxCtx;
    bool full = charge->spinChargePercent >= 0.85f;
    // (as the cosmetics editor may have them)
    Color_RGB8 prim = full ? Color_RGB8{ 255, 255, 170 } : Color_RGB8{ 170, 255, 255 };
    Color_RGB8 env = full ? Color_RGB8{ 255, 100, 0 } : Color_RGB8{ 0, 100, 255 };
    std::string cvar = std::string(CVAR_COSMETIC("SpinAttack.")) + (full ? "Level2" : "Level1");
    if (CVarGetInteger((cvar + "Primary.Changed").c_str(), 0)) {
        prim = CVarGetColor24((cvar + "Primary.Value").c_str(), prim);
    }
    if (CVarGetInteger((cvar + "Secondary.Changed").c_str(), 0)) {
        env = CVarGetColor24((cvar + "Secondary.Value").c_str(), env);
    }
    u32 frames = play->gameplayFrames;
    s32 spin = full ? 0x28 : 0x14;

    OPEN_DISPS(gfxCtx);
    Gfx_SetupDL_25Xlu(gfxCtx);
    Gfx* scroll = Gfx_TwoTexScrollEx(gfxCtx, 0, (frames * 5) & 0xFF, 0, 0x20, 0x20, 1, (frames * 20) & 0xFF,
                                     (frames * spin) & 0xFF, 8, 8, 5, 0, 20, spin);
    for (s32 i = 0; i < 2; i++) {
        Vtx* vtx = (Vtx*)Graph_Alloc(gfxCtx, sizeof(Vtx) * kGlowRings * kGlowCorners);
        BuildChargeGlow(vtx, i == 0, thickness);
        Gfx* tube = (Gfx*)Graph_Alloc(gfxCtx, sizeof(Gfx) * (3 + (kGlowRings - 1) * kGlowCorners));
        Gfx* g = tube;
        gSPDisplayList(g++, scroll);
        for (s32 r = 0; r + 1 < kGlowRings; r++) {
            // two rings at a time, corners 0-6 and 7-13: six sides (corners 3 and 4 are the texture's seam)
            gSPVertex(g++, (uintptr_t)&vtx[r * kGlowCorners], 2 * kGlowCorners, 0);
            for (s32 k = 0; k < kGlowCorners; k++) {
                if (k == 3) {
                    continue;
                }
                s32 next = (k + 1) % kGlowCorners;
                gSP2Triangles(g++, next, next + kGlowCorners, k + kGlowCorners, 0, next, k + kGlowCorners, k, 0);
            }
        }
        gDPSetPrimColor(g++, 0, 0x80, prim.r, prim.g, prim.b, 0);
        gSPEndDisplayList(g++);

        Matrix_Push();
        Matrix_Put(&sScimitarMtx[i]);
        gSPMatrix(POLY_XLU_DISP++, MATRIX_NEWMTX(gfxCtx), G_MTX_NOPUSH | G_MTX_LOAD | G_MTX_MODELVIEW);
        Matrix_Pop();
        gDPSetPrimColor(POLY_XLU_DISP++, 0, 0x80, prim.r, prim.g, prim.b, alpha);
        gDPSetEnvColor(POLY_XLU_DISP++, env.r, env.g, env.b, 128);
        gSPSegment(POLY_XLU_DISP++, 0x09, (uintptr_t)tube);
        gSPDisplayList(POLY_XLU_DISP++, (Gfx*)kChargeDL);
    }
    CLOSE_DISPS(gfxCtx);
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
    sBladeDrawn[0] = sBladeDrawn[1] = false;
    // The player actor's model matrix (position, facing, 0.01 scale) is still current here.
    SkelAnime_DrawFlexOpa(gPlayState, sBody.skel.skeleton, sFinalJoints, sBody.skel.dListCount, NULL,
                          PlayAsNabooru_PostLimbDraw, &player->actor);

    CLOSE_DISPS(gPlayState->state.gfxCtx);
}
