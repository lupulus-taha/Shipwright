// Jackal (Play as Nabooru mod): a new enemy, first seen in Jackal Refuge.
//
// Its model, skeleton and animations are made by tools/build_jackal.py (Blender + Fast64) into mods/jackal.o2r; without
// that archive no jackal appears. It is a custom actor, registered with the game's actor database the first time one
// is spawned (Jackal_Spawn).
//
// Left alone, a jackal stands watch and wanders about its home at a walk. What it does about the player depends on
// whether she is honoured by jackals (the CVar gPlayAsNabooru.HonouredByJackals, off unless set):
//  - Not honoured, it is an enemy. When it notices her it stops, stares and howls. When she comes nearer, or hurts
//    it, it turns on her: it runs up, snarls, leaps at her to bite, darts aside and comes again, until she gets away
//    from it or kills it. It dies of a few blows.
//  - Honoured, it is only curious: it runs up, keeps a few steps away and watches her, and backs off when
//    approached. It never attacks and cannot be hurt.
//
// Its voice is cut from recordings of real jackals (tools/build_jackal_sounds.py -> mods/jackal_sounds.o2r, played by
// ModSound.cpp). Without that archive it borrows the Wolfos'.
//
// For looking at its animations, the CVar gPlayAsNabooru.JackalGait makes every jackal ignore the player:
// 1 = stand, 2 = walk in a circle, 3 = run in a circle, 4 = walk on the spot, 5 = run on the spot.

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/ActorDB.h"
#include "soh/ResourceManagerHelpers.h"
#include "soh/Enhancements/game-interactor/GameInteractor_Hooks.h"
#include "soh/Enhancements/PlayAsNabooru/ModSound.h"
#include "soh/frame_interpolation.h" // gives OPEN_DISPS' interpolation calls C linkage in this C++ file

extern "C" {
#include "z64.h"
#include "macros.h"
#include "functions.h"
#include "variables.h"
Actor* Jackal_Spawn(PlayState* play, f32 x, f32 y, f32 z, s16 yaw);
}

#define JACKAL_ASSET(name) "__OTR__objects/object_jackal/" name
#define JACKAL_GAIT_CVAR "gPlayAsNabooru.JackalGait"
#define JACKAL_HONOURED_CVAR "gPlayAsNabooru.HonouredByJackals"

// Limbs, in the order tools/build_jackal.py lists them in (each limb's children sorted by name).
enum JackalLimb {
    JACKAL_LIMB_NONE,
    JACKAL_LIMB_ROOT,
    JACKAL_LIMB_CHEST,
    JACKAL_LIMB_FRONT_LEG_L_UPPER,
    JACKAL_LIMB_FRONT_LEG_L_LOWER,
    JACKAL_LIMB_FRONT_LEG_L_PAW,
    JACKAL_LIMB_FRONT_LEG_R_UPPER,
    JACKAL_LIMB_FRONT_LEG_R_LOWER,
    JACKAL_LIMB_FRONT_LEG_R_PAW,
    JACKAL_LIMB_NECK,
    JACKAL_LIMB_HEAD,
    JACKAL_LIMB_EAR_L,
    JACKAL_LIMB_EAR_R,
    JACKAL_LIMB_JAW,
    JACKAL_LIMB_HIND_LEG_L_UPPER,
    JACKAL_LIMB_HIND_LEG_L_LOWER,
    JACKAL_LIMB_HIND_LEG_L_PAW,
    JACKAL_LIMB_HIND_LEG_R_UPPER,
    JACKAL_LIMB_HIND_LEG_R_LOWER,
    JACKAL_LIMB_HIND_LEG_R_PAW,
    JACKAL_LIMB_TAIL_1,
    JACKAL_LIMB_TAIL_2,
    JACKAL_LIMB_MAX
};

// Its animations (ANIMATIONS in build_jackal.py). The first three are its gaits; a jackal that is wary or angry
// stands in Alert or Threat instead of Idle.
enum JackalAnim {
    JACKAL_ANIM_IDLE,
    JACKAL_ANIM_WALK,
    JACKAL_ANIM_RUN,
    JACKAL_ANIM_ALERT,
    JACKAL_ANIM_HOWL,
    JACKAL_ANIM_THREAT,
    JACKAL_ANIM_ATTACK,
    JACKAL_ANIM_HIT,
    JACKAL_ANIM_DIE,
    JACKAL_ANIM_MAX
};

struct JackalAnimInfo {
    const char* path;
    bool loops;
};

static const JackalAnimInfo sAnims[JACKAL_ANIM_MAX] = {
    { JACKAL_ASSET("gJackalIdleAnim"), true },    { JACKAL_ASSET("gJackalWalkAnim"), true },
    { JACKAL_ASSET("gJackalRunAnim"), true },     { JACKAL_ASSET("gJackalAlertAnim"), true },
    { JACKAL_ASSET("gJackalHowlAnim"), false },   { JACKAL_ASSET("gJackalThreatAnim"), true },
    { JACKAL_ASSET("gJackalAttackAnim"), false }, { JACKAL_ASSET("gJackalHitAnim"), false },
    { JACKAL_ASSET("gJackalDieAnim"), false },
};

// The speeds its walk and run cycles are made for, in units a frame (WALK_SPEED / RUN_SPEED in build_jackal.py):
// the cycles are played faster or slower to match how fast it really moves, so its paws do not slide.
static const f32 WALK_SPEED = 1.2f;
static const f32 RUN_SPEED = 7.0f;
// Its leap (ATTACK_LEAP / ATTACK_LAND in build_jackal.py): the frames of the attack on which it leaves the ground and
// on which it is down again.
static const f32 ATTACK_LEAP_FRAME = 5.0f;
static const f32 ATTACK_LAND_FRAME = 10.0f;

static const f32 NOTICE_RANGE = 420.0f; // it notices the player when she is this near...
static const f32 LOSE_RANGE = 700.0f;   // ...and forgets her beyond this
static const f32 WANDER_RANGE = 220.0f; // how far from home it strolls
// honoured
static const f32 WATCH_RANGE = 170.0f;   // how near it comes
static const f32 FOLLOW_RANGE = 270.0f;  // watching, it lets the player get this far before following
static const f32 RETREAT_RANGE = 100.0f; // nearer than this, it backs off
// not honoured
static const f32 PROVOKE_RANGE = 250.0f; // staring at the player, it turns on her when she comes this near
static const f32 LEAP_RANGE = 115.0f;    // it runs up to this far from her, to leap from there
static const f32 CLOSE_RANGE = 60.0f;    // any nearer and it bites at once
static const f32 PURSUE_RANGE = 150.0f;  // snarling, it lets her get this far before running after her
static const s16 HOWL_PAUSE = 30 * 20;   // frames before it howls again
static const u8 HEALTH = 6;              // a slash of the Master Sword takes 2

enum JackalDamageEffect { JACKAL_DMGEFF_NONE, JACKAL_DMGEFF_STUN };

static DamageTable sDamageTable = {
    /* Deku nut      */ DMG_ENTRY(0, JACKAL_DMGEFF_STUN),
    /* Deku stick    */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Slingshot     */ DMG_ENTRY(1, JACKAL_DMGEFF_NONE),
    /* Explosive     */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Boomerang     */ DMG_ENTRY(0, JACKAL_DMGEFF_STUN),
    /* Normal arrow  */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Hammer swing  */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Hookshot      */ DMG_ENTRY(0, JACKAL_DMGEFF_STUN),
    /* Kokiri sword  */ DMG_ENTRY(1, JACKAL_DMGEFF_NONE),
    /* Master sword  */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Giant's Knife */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Fire arrow    */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Ice arrow     */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Light arrow   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Unk arrow 1   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Unk arrow 2   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Unk arrow 3   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Fire magic    */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Ice magic     */ DMG_ENTRY(3, JACKAL_DMGEFF_NONE),
    /* Light magic   */ DMG_ENTRY(3, JACKAL_DMGEFF_NONE),
    /* Shield        */ DMG_ENTRY(0, JACKAL_DMGEFF_NONE),
    /* Mirror Ray    */ DMG_ENTRY(0, JACKAL_DMGEFF_NONE),
    /* Kokiri spin   */ DMG_ENTRY(1, JACKAL_DMGEFF_NONE),
    /* Giant spin    */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Master spin   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Kokiri jump   */ DMG_ENTRY(2, JACKAL_DMGEFF_NONE),
    /* Giant jump    */ DMG_ENTRY(8, JACKAL_DMGEFF_NONE),
    /* Master jump   */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Unknown 1     */ DMG_ENTRY(0, JACKAL_DMGEFF_NONE),
    /* Unblockable   */ DMG_ENTRY(0, JACKAL_DMGEFF_NONE),
    /* Hammer jump   */ DMG_ENTRY(4, JACKAL_DMGEFF_NONE),
    /* Unknown 2     */ DMG_ENTRY(0, JACKAL_DMGEFF_NONE),
};

// Its body, from its hindquarters to its neck: in the way of everything, and what the player's weapons hit.
static ColliderCylinderInit sBodyCylinderInit = {
    {
        COLTYPE_HIT5,
        AT_NONE,
        AC_ON | AC_TYPE_PLAYER,
        OC1_ON | OC1_TYPE_ALL,
        OC2_TYPE_1,
        COLSHAPE_CYLINDER,
    },
    {
        ELEMTYPE_UNK1,
        { 0x00000000, 0x00, 0x00 },
        { 0xFFCFFFFF, 0x00, 0x00 },
        TOUCH_NONE,
        BUMP_ON,
        OCELEM_ON,
    },
    { 20, 32, 0, { 0, 0, 0 } },
};
static const f32 BODY_AHEAD = 6.0f; // the middle of its body is this far ahead of where it stands

// Its jaws, while it leaps: half a heart.
static ColliderCylinderInit sBiteCylinderInit = {
    {
        COLTYPE_NONE,
        AT_ON | AT_TYPE_ENEMY,
        AC_NONE,
        OC1_NONE,
        OC2_NONE,
        COLSHAPE_CYLINDER,
    },
    {
        ELEMTYPE_UNK0,
        { 0xFFCFFFFF, 0x00, 0x08 },
        { 0x00000000, 0x00, 0x00 },
        TOUCH_ON | TOUCH_SFX_NORMAL,
        BUMP_NONE,
        OCELEM_NONE,
    },
    { 13, 26, 0, { 0, 0, 0 } },
};
static const f32 BITE_AHEAD = 26.0f;
static const f32 BITE_HEIGHT = 6.0f;

static CollisionCheckInfoInit2 sColChkInfoInit = { HEALTH, 0, 0, 0, 80 };

// ------------------------------------------------------------------------------------------------ voice

enum JackalVoice { JACKAL_VOICE_HOWL, JACKAL_VOICE_BARK, JACKAL_VOICE_ATTACK, JACKAL_VOICE_HURT, JACKAL_VOICE_DIE };

struct JackalVoiceInfo {
    const char* clips[4]; // one of these is played (names of tools/build_jackal_sounds.py)
    f32 pitchLow, pitchHigh;
    f32 volume;
    f32 range;   // how far it carries
    s16 jaw;     // how far its jaws open for it (for the calls no animation opens them for)
    u16 wolfos;  // the sound it makes without the recordings
};

static const JackalVoiceInfo sVoices[] = {
    /* howl   */ { { "howl" }, 0.97f, 1.03f, 1.0f, 3500.0f, 0, NA_SE_EN_WOLFOS_APPEAR },
    /* bark   */ { { "bark1", "bark2", "bark3", "bark4" }, 0.95f, 1.05f, 0.8f, 2500.0f, 0x1300, NA_SE_EN_WOLFOS_CRY },
    /* attack */ { { "bark1", "bark3" }, 0.78f, 0.86f, 1.0f, 2500.0f, 0, NA_SE_EN_WOLFOS_ATTACK },
    /* hurt   */ { { "bark2", "bark4" }, 1.3f, 1.45f, 1.0f, 2500.0f, 0, NA_SE_EN_WOLFOS_DAMAGE },
    /* die    */ { { "whine" }, 1.05f, 1.1f, 1.0f, 3000.0f, 0, NA_SE_EN_WOLFOS_DEAD },
};

// The recordings: loaded the first time a jackal is spawned. -1: not there.
static int sClips[ARRAY_COUNT(sVoices)][4];
static bool sClipsLoaded = false;

static void Jackal_LoadVoice() {
    if (sClipsLoaded) {
        return;
    }
    sClipsLoaded = true;
    for (size_t voice = 0; voice < ARRAY_COUNT(sVoices); voice++) {
        for (size_t i = 0; i < 4; i++) {
            const char* name = sVoices[voice].clips[i];
            char path[64];

            snprintf(path, sizeof(path), "sounds/jackal/%s.wav", name != NULL ? name : "");
            sClips[voice][i] = (name != NULL) ? ModSound_Load(path) : -1;
        }
    }
}

// ------------------------------------------------------------------------------------------------ the actor

struct EnJackal;
typedef void (*EnJackalAction)(EnJackal*, PlayState*);

struct EnJackal {
    Actor actor;
    SkelAnime skelAnime;
    Vec3s jointTable[JACKAL_LIMB_MAX];
    Vec3s morphTable[JACKAL_LIMB_MAX];
    ColliderCylinder collider;
    ColliderCylinder biteCollider;
    EnJackalAction action;
    s32 anim;
    // the animation it stands in while its speed chooses its gait (Idle, Alert or Threat), or -1 while its action
    // plays an animation of its own
    s32 stand;
    bool honoured; // what its action took the player for
    s16 timer;
    Vec3f goal;
    s16 lookYaw; // how far its head is turned towards the player
    s16 lookPitch;
    s16 jaw;       // how far a bark has its jaws open
    s16 barks;     // barks it is still to give, and the frames until the next
    s16 barkTimer;
    s16 howlTimer; // frames until it may howl again
    bool leapt;    // (attacking) it has left the ground
    int voice;     // the recording it is giving (ModSound), 0 if none
};

static void EnJackal_SetupWait(EnJackal* self);
static void EnJackal_Wait(EnJackal* self, PlayState* play);
static void EnJackal_Wander(EnJackal* self, PlayState* play);
static void EnJackal_Chase(EnJackal* self, PlayState* play);
static void EnJackal_Watch(EnJackal* self, PlayState* play);
static void EnJackal_Retreat(EnJackal* self, PlayState* play);
static void EnJackal_SetupAlert(EnJackal* self, s16 untilHowl);
static void EnJackal_Alert(EnJackal* self, PlayState* play);
static void EnJackal_Howl(EnJackal* self, PlayState* play);
static void EnJackal_SetupPursue(EnJackal* self);
static void EnJackal_Pursue(EnJackal* self, PlayState* play);
static void EnJackal_SetupThreaten(EnJackal* self, s16 frames);
static void EnJackal_Threaten(EnJackal* self, PlayState* play);
static void EnJackal_Attack(EnJackal* self, PlayState* play);
static void EnJackal_Dart(EnJackal* self, PlayState* play);
static void EnJackal_Hurt(EnJackal* self, PlayState* play);
static void EnJackal_Stunned(EnJackal* self, PlayState* play);
static void EnJackal_Die(EnJackal* self, PlayState* play);

static void EnJackal_SetAnim(EnJackal* self, s32 anim, f32 morphFrames) {
    if (self->anim != anim || !sAnims[anim].loops) {
        AnimationHeader* header = (AnimationHeader*)sAnims[anim].path;

        self->anim = anim;
        Animation_Change(&self->skelAnime, header, 1.0f, 0.0f, Animation_GetLastFrame(header),
                         sAnims[anim].loops ? ANIMMODE_LOOP : ANIMMODE_ONCE, -morphFrames);
    }
}

// Picks the gait for how fast it is going, and plays it at that pace.
static void EnJackal_UpdateGait(EnJackal* self, f32 speed, s32 stand) {
    if (speed < 0.15f) {
        if (self->anim != stand) {
            EnJackal_SetAnim(self, stand, 6.0f);
        }
        self->skelAnime.playSpeed = 1.0f;
    } else if (speed < 2.6f) {
        EnJackal_SetAnim(self, JACKAL_ANIM_WALK, self->anim == JACKAL_ANIM_RUN ? 4.0f : 5.0f);
        self->skelAnime.playSpeed = CLAMP(speed / WALK_SPEED, 0.5f, 1.8f);
    } else {
        EnJackal_SetAnim(self, JACKAL_ANIM_RUN, 3.0f);
        self->skelAnime.playSpeed = CLAMP(speed / RUN_SPEED, 0.55f, 1.3f);
    }
}

// Heads for a point: turns towards it (faster the slower it goes) and takes up the given speed.
static void EnJackal_MoveTowards(EnJackal* self, Vec3f* point, f32 speed, f32 acceleration) {
    s16 turn = (self->actor.speedXZ > 3.0f) ? 0x600 : 0x900;
    s16 yaw = Actor_WorldYawTowardPoint(&self->actor, point);
    s16 off = yaw - self->actor.world.rot.y;

    Math_SmoothStepToS(&self->actor.world.rot.y, yaw, 4, turn, 0x80);
    // not at full speed until it faces where it is going
    if (ABS(off) > 0x3000) {
        speed = MIN(speed, WALK_SPEED);
    }
    Math_SmoothStepToF(&self->actor.speedXZ, speed, 0.4f, acceleration, 0.02f);
}

static void EnJackal_Stop(EnJackal* self, f32 deceleration) {
    Math_SmoothStepToF(&self->actor.speedXZ, 0.0f, 0.5f, deceleration, 0.02f);
}

// Standing, turns on the spot to face the player once she is more than `slack` off to one side.
static void EnJackal_FacePlayer(EnJackal* self, s16 slack, s16 turn) {
    s16 off = self->actor.yawTowardsPlayer - self->actor.world.rot.y;

    if (fabsf(self->actor.speedXZ) < 0.5f && ABS(off) > slack) {
        Math_SmoothStepToS(&self->actor.world.rot.y, self->actor.yawTowardsPlayer, 6, turn, 0x80);
    }
}

static bool EnJackal_NoticesPlayer(EnJackal* self) {
    return self->actor.xzDistToPlayer < NOTICE_RANGE && fabsf(self->actor.yDistToPlayer) < 200.0f;
}

// Gives voice: one of the recordings, or the Wolfos' sound for it.
static void EnJackal_Voice(EnJackal* self, s32 voice) {
    const JackalVoiceInfo* info = &sVoices[voice];
    s32 count = 0;

    while (count < 4 && sClips[voice][count] >= 0) {
        count++;
    }
    if (count > 0) {
        f32 pitch = info->pitchLow + Rand_ZeroOne() * (info->pitchHigh - info->pitchLow);

        ModSound_Stop(self->voice);
        self->voice = ModSound_Play(sClips[voice][(s32)Rand_ZeroFloat(count - 0.01f)], &self->actor.projectedPos,
                                    info->volume, info->range, pitch);
    } else {
        Audio_PlayActorSound2(&self->actor, info->wolfos);
    }
    self->jaw = info->jaw;
}

// ------------------------------------------------------------------------------------------------ left alone

static void EnJackal_SetupWait(EnJackal* self) {
    self->stand = JACKAL_ANIM_IDLE;
    self->timer = 50 + (s16)Rand_ZeroFloat(90.0f);
    self->barks = 0;
    self->actor.flags &= ~ACTOR_FLAG_HOSTILE;
    self->action = EnJackal_Wait;
}

// Strolls back to where it lives.
static void EnJackal_SetupGoHome(EnJackal* self) {
    EnJackal_SetupWait(self);
    self->goal = self->actor.home.pos;
    self->timer = 1200;
    self->action = EnJackal_Wander;
}

static void EnJackal_Noticed(EnJackal* self) {
    if (self->honoured) {
        self->action = EnJackal_Chase;
    } else {
        EnJackal_SetupAlert(self, 14);
    }
}

static void EnJackal_Wait(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 0.3f);
    if (EnJackal_NoticesPlayer(self)) {
        EnJackal_Noticed(self);
    } else if (--self->timer <= 0) {
        f32 angle = Rand_ZeroFloat(2.0f * M_PI);
        f32 distance = 60.0f + Rand_ZeroFloat(WANDER_RANGE - 60.0f);

        self->goal.x = self->actor.home.pos.x + distance * sinf(angle);
        self->goal.y = self->actor.home.pos.y;
        self->goal.z = self->actor.home.pos.z + distance * cosf(angle);
        self->timer = 400;
        self->action = EnJackal_Wander;
    }
}

static void EnJackal_Wander(EnJackal* self, PlayState* play) {
    EnJackal_MoveTowards(self, &self->goal, WALK_SPEED, 0.15f);
    if (EnJackal_NoticesPlayer(self)) {
        EnJackal_Noticed(self);
    } else if (Actor_WorldDistXZToPoint(&self->actor, &self->goal) < 20.0f || --self->timer <= 0 ||
               (self->actor.bgCheckFlags & BGCHECKFLAG_WALL)) {
        EnJackal_SetupWait(self);
    }
}

// ------------------------------------------------------------------------------------------------ honoured

static void EnJackal_Chase(EnJackal* self, PlayState* play) {
    Player* player = GET_PLAYER(play);

    EnJackal_MoveTowards(self, &player->actor.world.pos, RUN_SPEED, 0.9f);
    if (self->actor.xzDistToPlayer < WATCH_RANGE) {
        self->action = EnJackal_Watch;
    } else if (self->actor.xzDistToPlayer > LOSE_RANGE) {
        EnJackal_SetupGoHome(self); // lost interest
    }
}

static void EnJackal_Watch(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 1.2f);
    EnJackal_FacePlayer(self, 0x1800, 0x500);
    if (self->actor.xzDistToPlayer < RETREAT_RANGE) {
        // back off: away from the player, a little to one side
        s16 away = self->actor.yawTowardsPlayer + 0x8000 + (s16)(Rand_CenteredFloat(1.0f) * 0x3000);

        self->goal.x = self->actor.world.pos.x + 200.0f * Math_SinS(away);
        self->goal.y = self->actor.world.pos.y;
        self->goal.z = self->actor.world.pos.z + 200.0f * Math_CosS(away);
        self->timer = 40;
        self->action = EnJackal_Retreat;
    } else if (self->actor.xzDistToPlayer > FOLLOW_RANGE) {
        self->action = EnJackal_Chase;
    }
}

static void EnJackal_Retreat(EnJackal* self, PlayState* play) {
    EnJackal_MoveTowards(self, &self->goal, RUN_SPEED, 1.2f);
    if (Actor_WorldDistXZToPoint(&self->actor, &self->goal) < 30.0f || --self->timer <= 0 ||
        (self->actor.bgCheckFlags & BGCHECKFLAG_WALL)) {
        self->action = EnJackal_Watch;
    }
}

// ------------------------------------------------------------------------------------------------ not honoured

// It has noticed the player: it stops what it was doing, turns to her and stares, and howls after `untilHowl`
// frames (unless it has howled lately).
static void EnJackal_SetupAlert(EnJackal* self, s16 untilHowl) {
    self->stand = JACKAL_ANIM_ALERT;
    self->timer = untilHowl;
    self->action = EnJackal_Alert;
}

// Turns on the player: from now on it is after her.
static void EnJackal_Provoked(EnJackal* self) {
    self->actor.flags |= ACTOR_FLAG_HOSTILE;
    self->barks = 3;
    self->barkTimer = 2;
    EnJackal_SetupPursue(self);
}

static void EnJackal_Alert(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 1.0f);
    EnJackal_FacePlayer(self, 0x1000, 0x600);
    if (self->actor.xzDistToPlayer < PROVOKE_RANGE) {
        EnJackal_Provoked(self);
    } else if (self->actor.xzDistToPlayer > LOSE_RANGE) {
        EnJackal_SetupGoHome(self);
    } else if (self->timer > 0 && --self->timer == 0 && self->howlTimer == 0) {
        self->stand = -1;
        self->howlTimer = HOWL_PAUSE;
        EnJackal_SetAnim(self, JACKAL_ANIM_HOWL, 4.0f);
        EnJackal_Voice(self, JACKAL_VOICE_HOWL);
        self->action = EnJackal_Howl;
    }
}

static void EnJackal_Howl(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 1.0f);
    if (self->actor.xzDistToPlayer < PROVOKE_RANGE) {
        EnJackal_Provoked(self);
    } else if (self->skelAnime.curFrame >= self->skelAnime.endFrame) {
        EnJackal_SetupAlert(self, 0);
    }
}

// Whether the player has got away from it: far off, or far from where it lives.
static bool EnJackal_LostPlayer(EnJackal* self) {
    return self->actor.xzDistToPlayer > LOSE_RANGE ||
           Actor_WorldDistXZToPoint(&self->actor, &self->actor.home.pos) > 1500.0f;
}

static void EnJackal_SetupPursue(EnJackal* self) {
    self->stand = JACKAL_ANIM_THREAT;
    self->action = EnJackal_Pursue;
}

static void EnJackal_Pursue(EnJackal* self, PlayState* play) {
    Player* player = GET_PLAYER(play);
    s16 off = self->actor.yawTowardsPlayer - self->actor.world.rot.y;

    EnJackal_MoveTowards(self, &player->actor.world.pos, RUN_SPEED, 0.9f);
    if (EnJackal_LostPlayer(self)) {
        EnJackal_SetupGoHome(self);
    } else if (self->actor.xzDistToPlayer < LEAP_RANGE && ABS(off) < 0x2000) {
        EnJackal_SetupThreaten(self, 8 + (s16)Rand_ZeroFloat(16.0f));
    }
}

// Stands snarling at the player for some frames, then leaps at her.
static void EnJackal_SetupThreaten(EnJackal* self, s16 frames) {
    self->stand = JACKAL_ANIM_THREAT;
    self->timer = frames;
    self->action = EnJackal_Threaten;
}

static void EnJackal_SetupAttack(EnJackal* self) {
    self->stand = -1;
    self->leapt = false;
    EnJackal_SetAnim(self, JACKAL_ANIM_ATTACK, 2.0f);
    self->action = EnJackal_Attack;
}

static void EnJackal_Threaten(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 1.5f);
    EnJackal_FacePlayer(self, 0x400, 0xA00);
    if (self->actor.xzDistToPlayer > PURSUE_RANGE) {
        EnJackal_SetupPursue(self);
        return;
    }
    if (self->barks == 0 && Rand_ZeroOne() < 0.04f) {
        self->barks = 1 + (s16)Rand_ZeroFloat(1.99f);
        self->barkTimer = 0;
    }
    if (self->actor.xzDistToPlayer < CLOSE_RANGE) {
        self->timer = MIN(self->timer, 3);
    }
    // (it does not leap at her while the game has her attention)
    if (--self->timer <= 0 && !Player_InCsMode(play) && fabsf(self->actor.speedXZ) < 1.0f) {
        EnJackal_SetupAttack(self);
    }
}

static void EnJackal_Attack(EnJackal* self, PlayState* play) {
    f32 frame = self->skelAnime.curFrame;

    if (!self->leapt) {
        // crouching: still taking aim
        EnJackal_Stop(self, 1.5f);
        Math_SmoothStepToS(&self->actor.world.rot.y, self->actor.yawTowardsPlayer, 3, 0x800, 0x80);
        if (frame >= ATTACK_LEAP_FRAME) {
            // as far as it takes to land on her, within what its legs can do
            self->leapt = true;
            self->actor.speedXZ = CLAMP((self->actor.xzDistToPlayer - 40.0f) / 5.0f, 6.0f, 14.0f);
            self->actor.velocity.y = 4.2f;
            self->barks = 0;
            EnJackal_Voice(self, JACKAL_VOICE_ATTACK);
        }
    } else if (frame > ATTACK_LAND_FRAME && (self->actor.bgCheckFlags & BGCHECKFLAG_GROUND)) {
        EnJackal_Stop(self, 2.0f);
    }

    if (self->leapt && frame <= ATTACK_LAND_FRAME + 1.0f) {
        if (self->biteCollider.base.atFlags & AT_BOUNCED) {
            // its teeth met her shield: thrown back
            self->biteCollider.base.atFlags &= ~(AT_HIT | AT_BOUNCED);
            self->actor.speedXZ = -5.0f;
            self->stand = -1;
            EnJackal_SetAnim(self, JACKAL_ANIM_HIT, 2.0f);
            self->action = EnJackal_Hurt;
            return;
        }
        self->biteCollider.base.atFlags &= ~AT_HIT;
        CollisionCheck_SetAT(play, &play->colChkCtx, &self->biteCollider.base);
    }

    if (frame >= self->skelAnime.endFrame) {
        // off to one side of her, to come at her again from there
        Player* player = GET_PLAYER(play);
        s16 side = (Rand_ZeroOne() < 0.5f) ? 1 : -1;
        s16 bearing = self->actor.yawTowardsPlayer + 0x8000 + side * (0x2800 + (s16)Rand_ZeroFloat(0x2000));
        f32 distance = LEAP_RANGE + 10.0f;

        self->goal.x = player->actor.world.pos.x + distance * Math_SinS(bearing);
        self->goal.y = self->actor.world.pos.y;
        self->goal.z = player->actor.world.pos.z + distance * Math_CosS(bearing);
        self->timer = 35;
        self->stand = JACKAL_ANIM_THREAT;
        self->action = EnJackal_Dart;
    }
}

static void EnJackal_Dart(EnJackal* self, PlayState* play) {
    EnJackal_MoveTowards(self, &self->goal, RUN_SPEED, 1.2f);
    if (EnJackal_LostPlayer(self)) {
        EnJackal_SetupGoHome(self);
    } else if (Actor_WorldDistXZToPoint(&self->actor, &self->goal) < 30.0f || --self->timer <= 0 ||
               (self->actor.bgCheckFlags & BGCHECKFLAG_WALL)) {
        if (self->actor.xzDistToPlayer < LEAP_RANGE + 15.0f) {
            EnJackal_SetupThreaten(self, 10 + (s16)Rand_ZeroFloat(20.0f));
        } else {
            EnJackal_SetupPursue(self);
        }
    }
}

// Struck (or thrown back by a shield): it staggers back, then stands up to her again.
static void EnJackal_SetupHurt(EnJackal* self) {
    self->stand = -1;
    self->barks = 0;
    self->actor.flags |= ACTOR_FLAG_HOSTILE;
    self->actor.world.rot.y = self->actor.yawTowardsPlayer;
    self->actor.speedXZ = -7.0f;
    EnJackal_SetAnim(self, JACKAL_ANIM_HIT, 1.0f);
    EnJackal_Voice(self, JACKAL_VOICE_HURT);
    self->action = EnJackal_Hurt;
}

static void EnJackal_Hurt(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 0.9f);
    if (self->skelAnime.curFrame >= self->skelAnime.endFrame) {
        EnJackal_SetupThreaten(self, 6 + (s16)Rand_ZeroFloat(14.0f));
    }
}

// Stunned (by a Deku nut, the boomerang, the hookshot): frozen until the blue wears off.
static void EnJackal_SetupStunned(EnJackal* self) {
    self->stand = -1;
    self->barks = 0;
    self->actor.flags |= ACTOR_FLAG_HOSTILE;
    self->actor.speedXZ = 0.0f;
    self->skelAnime.playSpeed = 0.0f;
    Audio_PlayActorSound2(&self->actor, NA_SE_EN_GOMA_JR_FREEZE);
    self->action = EnJackal_Stunned;
}

static void EnJackal_Stunned(EnJackal* self, PlayState* play) {
    if (self->actor.colorFilterTimer == 0) {
        self->anim = -1; // (whatever it was frozen in the middle of)
        EnJackal_SetupThreaten(self, 10);
    }
}

static void EnJackal_SetupDie(EnJackal* self, PlayState* play) {
    self->stand = -1;
    self->barks = 0;
    self->timer = 0;
    self->actor.flags &= ~(ACTOR_FLAG_ATTENTION_ENABLED | ACTOR_FLAG_HOSTILE);
    self->actor.world.rot.y = self->actor.yawTowardsPlayer;
    self->actor.speedXZ = -5.0f;
    EnJackal_SetAnim(self, JACKAL_ANIM_DIE, 1.0f);
    EnJackal_Voice(self, JACKAL_VOICE_DIE);
    self->action = EnJackal_Die;
    Enemy_StartFinishingBlow(play, &self->actor);
    GameInteractor_ExecuteOnEnemyDefeat(&self->actor);
}

// Falls and lies still, then goes up in the flames every monster ends in.
static void EnJackal_Die(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 0.5f);
    if (self->skelAnime.curFrame < self->skelAnime.endFrame) {
        return;
    }
    self->timer++;
    if (self->timer >= 28) {
        Item_DropCollectibleRandom(play, &self->actor, &self->actor.world.pos, 0xD0);
        Actor_Kill(&self->actor);
    } else if (self->timer >= 8) {
        Vec3f rise = { 0.0f, 0.5f, 0.0f };

        for (s32 i = (self->timer - 8) / 5; i >= 0; i--) {
            // it lies to its own left of where it stood
            Vec3f pos = self->actor.world.pos;
            pos.x += 9.0f * Math_CosS(self->actor.shape.rot.y) + Rand_CenteredFloat(44.0f);
            pos.y += 6.0f + Rand_CenteredFloat(12.0f);
            pos.z += -9.0f * Math_SinS(self->actor.shape.rot.y) + Rand_CenteredFloat(44.0f);
            EffectSsDeadDb_Spawn(play, &pos, &rise, &rise, 70, 0, 255, 255, 255, 255, 0, 0, 255, 1, 9, true);
        }
    }
}

// What the player's weapons did to it since the last frame.
static void EnJackal_UpdateDamage(EnJackal* self, PlayState* play) {
    if (!(self->collider.base.acFlags & AC_HIT)) {
        return;
    }
    self->collider.base.acFlags &= ~AC_HIT;
    if (self->honoured || self->action == EnJackal_Die) {
        return;
    }
    Actor_SetDropFlag(&self->actor, &self->collider.info, 1);
    if (self->actor.colChkInfo.damageEffect == JACKAL_DMGEFF_STUN) {
        if (self->action != EnJackal_Stunned) {
            Actor_SetColorFilter(&self->actor, 0, 120, 0, 80);
            Actor_ApplyDamage(&self->actor);
            EnJackal_SetupStunned(self);
        }
    } else {
        Actor_SetColorFilter(&self->actor, 0x4000, 255, 0, 8);
        if (Actor_ApplyDamage(&self->actor) == 0) {
            EnJackal_SetupDie(self, play);
        } else {
            EnJackal_SetupHurt(self);
        }
    }
}

// ------------------------------------------------------------------------------------------------ the game's side

// For looking at the animations: stands, or walks or runs, round in a circle or on the spot.
// Returns the speed its gait is to be played for.
static f32 EnJackal_ShowGait(EnJackal* self, s32 gait) {
    f32 speed = (gait == 1) ? 0.0f : (gait == 2 || gait == 4) ? WALK_SPEED : RUN_SPEED;

    if (self->action != EnJackal_Die) {
        EnJackal_SetupWait(self);
        self->timer = 20;
    }
    if (gait == 2 || gait == 3) {
        f32 radius = (gait == 2) ? 110.0f : 260.0f;

        self->actor.world.rot.y += (s16)(speed / radius * (0x8000 / M_PI));
        Math_SmoothStepToF(&self->actor.speedXZ, speed, 0.4f, 0.6f, 0.02f);
        return self->actor.speedXZ;
    }
    self->actor.speedXZ = 0.0f;
    return speed;
}

static void EnJackal_Init(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;

    ActorShape_Init(&self->actor.shape, 0.0f, ActorShadow_DrawCircle, 22.0f);
    SkelAnime_InitFlex(play, &self->skelAnime, (FlexSkeletonHeader*)JACKAL_ASSET("gJackalSkel"),
                       (AnimationHeader*)sAnims[JACKAL_ANIM_IDLE].path, self->jointTable, self->morphTable,
                       JACKAL_LIMB_MAX);
    self->anim = JACKAL_ANIM_IDLE;
    Collider_InitCylinder(play, &self->collider);
    Collider_SetCylinder(play, &self->collider, &self->actor, &sBodyCylinderInit);
    Collider_InitCylinder(play, &self->biteCollider);
    Collider_SetCylinder(play, &self->biteCollider, &self->actor, &sBiteCylinderInit);
    CollisionCheck_SetInfo2(&self->actor.colChkInfo, &sDamageTable, &sColChkInfoInit);
    Actor_SetScale(&self->actor, 0.01f);
    self->actor.gravity = -1.5f;
    self->actor.minVelocityY = -12.0f;
    self->actor.targetMode = 3;
    self->actor.naviEnemyId = 0xFF; // Navi knows nothing about it
    self->honoured = CVarGetInteger(JACKAL_HONOURED_CVAR, 0) != 0;
    EnJackal_SetupWait(self);
}

static void EnJackal_Destroy(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;

    ModSound_Stop(self->voice);
    Collider_DestroyCylinder(play, &self->collider);
    Collider_DestroyCylinder(play, &self->biteCollider);
    ResourceMgr_UnregisterSkeleton(&self->skelAnime);
}

static void EnJackal_Update(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;
    Player* player = GET_PLAYER(play);
    s32 gait = CVarGetInteger(JACKAL_GAIT_CVAR, 0);
    bool honoured = CVarGetInteger(JACKAL_HONOURED_CVAR, 0) != 0;
    bool dying = self->action == EnJackal_Die;
    s16 lookYaw = 0;
    s16 lookPitch = 0;

    if (honoured != self->honoured && !dying) {
        // she has just become honoured, or lost her honour: it thinks again
        self->honoured = honoured;
        EnJackal_SetupWait(self);
    }
    EnJackal_UpdateDamage(self, play);
    dying = self->action == EnJackal_Die;

    if (gait != 0 && !dying) {
        EnJackal_UpdateGait(self, EnJackal_ShowGait(self, gait), JACKAL_ANIM_IDLE);
    } else {
        self->action(self, play);
        if (self->stand >= 0) {
            EnJackal_UpdateGait(self, self->actor.speedXZ, self->stand);
        }
    }
    self->actor.shape.rot.y = self->actor.world.rot.y;
    SkelAnime_Update(&self->skelAnime);
    Actor_MoveXZGravity(&self->actor);
    Actor_UpdateBgCheckInfo(play, &self->actor, 20.0f, 18.0f, 30.0f, 5);

    // its barks: a few in a row
    DECR(self->howlTimer);
    if (self->barks > 0 && DECR(self->barkTimer) == 0) {
        self->barks--;
        self->barkTimer = 8 + (s16)Rand_ZeroFloat(4.0f);
        EnJackal_Voice(self, JACKAL_VOICE_BARK);
    }
    Math_SmoothStepToS(&self->jaw, 0, 2, 0x500, 0x40);
    ModSound_Move(self->voice, &self->actor.projectedPos);

    // its head follows the player while it stands or walks
    if (gait == 0 && self->actor.xzDistToPlayer < NOTICE_RANGE + 80.0f &&
        (self->anim == JACKAL_ANIM_IDLE || self->anim == JACKAL_ANIM_WALK || self->anim == JACKAL_ANIM_ALERT ||
         self->anim == JACKAL_ANIM_THREAT)) {
        Vec3f eyes = self->actor.focus.pos;
        s16 off = self->actor.yawTowardsPlayer - self->actor.shape.rot.y;

        lookYaw = CLAMP(off, -0x3000, 0x3000);
        lookPitch = Math_Vec3f_Pitch(&eyes, &player->actor.focus.pos);
        lookPitch = CLAMP(lookPitch, -0x1000, 0x1800);
    }
    Math_SmoothStepToS(&self->lookYaw, lookYaw, 5, 0x700, 0x40);
    Math_SmoothStepToS(&self->lookPitch, lookPitch, 5, 0x400, 0x40);

    Actor_SetFocus(&self->actor, 30.0f);
    if (dying) {
        return; // nothing to bump into or hit any more
    }
    f32 sin = Math_SinS(self->actor.shape.rot.y);
    f32 cos = Math_CosS(self->actor.shape.rot.y);

    Collider_UpdateCylinder(&self->actor, &self->collider);
    self->collider.dim.pos.x += (s16)(BODY_AHEAD * sin);
    self->collider.dim.pos.z += (s16)(BODY_AHEAD * cos);
    Collider_UpdateCylinder(&self->actor, &self->biteCollider);
    self->biteCollider.dim.pos.x += (s16)(BITE_AHEAD * sin);
    self->biteCollider.dim.pos.y += (s16)BITE_HEIGHT;
    self->biteCollider.dim.pos.z += (s16)(BITE_AHEAD * cos);
    CollisionCheck_SetOC(play, &play->colChkCtx, &self->collider.base);
    // an enemy can be hurt, except for the moment it flashes red from the last blow
    if (!self->honoured && (self->actor.colorFilterTimer == 0 || !(self->actor.colorFilterParams & 0x4000))) {
        CollisionCheck_SetAC(play, &play->colChkCtx, &self->collider.base);
    }
}

static s32 EnJackal_OverrideLimbDraw(PlayState* play, s32 limbIndex, Gfx** dList, Vec3f* pos, Vec3s* rot, void* thisx) {
    EnJackal* self = (EnJackal*)thisx;

    // Looking at the player: shared between neck and head. Their limbs' x axis points to the animal's left and
    // their z axis down, so turning left and looking up are both negative.
    if (limbIndex == JACKAL_LIMB_NECK) {
        rot->z -= (s16)(self->lookYaw * 0.4f);
        rot->x -= (s16)(self->lookPitch * 0.4f);
    } else if (limbIndex == JACKAL_LIMB_HEAD) {
        rot->z -= (s16)(self->lookYaw * 0.6f);
        rot->x -= (s16)(self->lookPitch * 0.6f);
    } else if (limbIndex == JACKAL_LIMB_JAW) {
        rot->x += self->jaw; // barking
    }
    return false;
}

static void EnJackal_Draw(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;

    Gfx_SetupDL_25Opa(play->state.gfxCtx);
    SkelAnime_DrawSkeletonOpa(play, &self->skelAnime, EnJackal_OverrideLimbDraw, NULL, self);
}

Actor* Jackal_Spawn(PlayState* play, f32 x, f32 y, f32 z, s16 yaw) {
    static int sActorId = -1;

    // the jackal's resources come from a mod archive: no archive, no jackal
    if (!ResourceMgr_FileExists(JACKAL_ASSET("gJackalSkel")) || !ResourceMgr_FileExists(sAnims[JACKAL_ANIM_DIE].path)) {
        return NULL;
    }
    if (sActorId < 0) {
        ActorDBInit init;
        init.name = "En_Jackal";
        init.desc = "Jackal";
        init.category = ACTORCAT_ENEMY;
        init.flags = ACTOR_FLAG_ATTENTION_ENABLED | ACTOR_FLAG_UPDATE_CULLING_DISABLED;
        init.objectId = OBJECT_GAMEPLAY_KEEP;
        init.instanceSize = sizeof(EnJackal);
        init.init = EnJackal_Init;
        init.destroy = EnJackal_Destroy;
        init.update = EnJackal_Update;
        init.draw = EnJackal_Draw;
        sActorId = ActorDB::Instance->AddEntry(init).entry.id;
    }
    Jackal_LoadVoice();
    return Actor_Spawn(&play->actorCtx, play, sActorId, x, y, z, 0, yaw, 0, 0);
}
