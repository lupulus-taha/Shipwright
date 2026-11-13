// Jackal (Play as Nabooru mod): a new enemy, first seen in Jackal Refuge.
//
// Its model, skeleton and animations are made by tools/build_jackal.py (Blender + Fast64) into mods/jackal.o2r; without
// that archive no jackal appears. It is a custom actor, registered with the game's actor database the first time one
// is spawned (Jackal_Spawn).
//
// For now a jackal is only wary: it stands watch and wanders about its home at a walk; when the player comes near it
// runs up, keeps a few steps away and watches, and backs off when approached. It has no attacks and cannot be hurt
// yet.
//
// For looking at its animations, the CVar gPlayAsNabooru.JackalGait makes every jackal ignore the player:
// 1 = stand, 2 = walk in a circle, 3 = run in a circle.

#include <libultraship/bridge/consolevariablebridge.h>
#include "soh/ActorDB.h"
#include "soh/ResourceManagerHelpers.h"
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

enum JackalAnim { JACKAL_ANIM_IDLE, JACKAL_ANIM_WALK, JACKAL_ANIM_RUN };

// The speeds its walk and run cycles are made for, in units a frame (WALK_SPEED / RUN_SPEED in build_jackal.py):
// the cycles are played faster or slower to match how fast it really moves, so its paws do not slide.
static const f32 WALK_SPEED = 1.2f;
static const f32 RUN_SPEED = 7.0f;

static const f32 NOTICE_RANGE = 420.0f;  // it comes running when the player is this near...
static const f32 LOSE_RANGE = 700.0f;    // ...and gives up beyond this
static const f32 WATCH_RANGE = 170.0f;   // how near it comes
static const f32 FOLLOW_RANGE = 270.0f;  // watching, it lets the player get this far before following
static const f32 RETREAT_RANGE = 100.0f; // nearer than this, it backs off
static const f32 WANDER_RANGE = 220.0f;  // how far from home it strolls

struct EnJackal;
typedef void (*EnJackalAction)(EnJackal*, PlayState*);

struct EnJackal {
    Actor actor;
    SkelAnime skelAnime;
    Vec3s jointTable[JACKAL_LIMB_MAX];
    Vec3s morphTable[JACKAL_LIMB_MAX];
    ColliderCylinder collider;
    EnJackalAction action;
    s32 anim;
    s16 timer;
    Vec3f goal;
    s16 lookYaw; // how far its head is turned towards the player
    s16 lookPitch;
};

static ColliderCylinderInit sCylinderInit = {
    {
        COLTYPE_NONE,
        AT_NONE,
        AC_NONE,
        OC1_ON | OC1_TYPE_ALL,
        OC2_TYPE_1,
        COLSHAPE_CYLINDER,
    },
    {
        ELEMTYPE_UNK0,
        { 0x00000000, 0x00, 0x00 },
        { 0x00000000, 0x00, 0x00 },
        TOUCH_NONE,
        BUMP_NONE,
        OCELEM_ON,
    },
    { 15, 30, 0, { 0, 0, 0 } },
};

static CollisionCheckInfoInit2 sColChkInfoInit = { 1, 0, 0, 0, 80 };

static void EnJackal_SetupWait(EnJackal* self);
static void EnJackal_Wait(EnJackal* self, PlayState* play);
static void EnJackal_Wander(EnJackal* self, PlayState* play);
static void EnJackal_Chase(EnJackal* self, PlayState* play);
static void EnJackal_Watch(EnJackal* self, PlayState* play);
static void EnJackal_Retreat(EnJackal* self, PlayState* play);

static void EnJackal_SetAnim(EnJackal* self, s32 anim, f32 morphFrames) {
    static const char* sAnims[] = { JACKAL_ASSET("gJackalIdleAnim"), JACKAL_ASSET("gJackalWalkAnim"),
                                    JACKAL_ASSET("gJackalRunAnim") };

    if (self->anim != anim) {
        AnimationHeader* header = (AnimationHeader*)sAnims[anim];
        self->anim = anim;
        Animation_Change(&self->skelAnime, header, 1.0f, 0.0f, Animation_GetLastFrame(header), ANIMMODE_LOOP,
                         -morphFrames);
    }
}

// Picks the gait for how fast it is going, and plays it at that pace.
static void EnJackal_UpdateGait(EnJackal* self) {
    f32 speed = self->actor.speedXZ;

    if (speed < 0.15f) {
        EnJackal_SetAnim(self, JACKAL_ANIM_IDLE, 6.0f);
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

static bool EnJackal_NoticesPlayer(EnJackal* self) {
    return self->actor.xzDistToPlayer < NOTICE_RANGE && fabsf(self->actor.yDistToPlayer) < 200.0f;
}

static void EnJackal_SetupWait(EnJackal* self) {
    self->timer = 50 + (s16)Rand_ZeroFloat(90.0f);
    self->action = EnJackal_Wait;
}

static void EnJackal_Wait(EnJackal* self, PlayState* play) {
    EnJackal_Stop(self, 0.3f);
    if (EnJackal_NoticesPlayer(self)) {
        self->action = EnJackal_Chase;
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
        self->action = EnJackal_Chase;
    } else if (Actor_WorldDistXZToPoint(&self->actor, &self->goal) < 20.0f || --self->timer <= 0 ||
               (self->actor.bgCheckFlags & 8)) { // (walked into a wall)
        EnJackal_SetupWait(self);
    }
}

static void EnJackal_Chase(EnJackal* self, PlayState* play) {
    Player* player = GET_PLAYER(play);

    EnJackal_MoveTowards(self, &player->actor.world.pos, RUN_SPEED, 0.9f);
    if (self->actor.xzDistToPlayer < WATCH_RANGE) {
        self->action = EnJackal_Watch;
    } else if (self->actor.xzDistToPlayer > LOSE_RANGE) {
        // lost interest: stroll back home
        self->goal = self->actor.home.pos;
        self->timer = 1200;
        self->action = EnJackal_Wander;
    }
}

static void EnJackal_Watch(EnJackal* self, PlayState* play) {
    s16 off = self->actor.yawTowardsPlayer - self->actor.world.rot.y;

    EnJackal_Stop(self, 1.2f);
    // turns on the spot once the player is well off to one side
    if (self->actor.speedXZ < 0.5f && ABS(off) > 0x1800) {
        Math_SmoothStepToS(&self->actor.world.rot.y, self->actor.yawTowardsPlayer, 6, 0x500, 0x80);
    }
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
        (self->actor.bgCheckFlags & 8)) {
        self->action = EnJackal_Watch;
    }
}

// For looking at the animations: stands, or goes round in a circle at its walk or run.
static void EnJackal_ShowGait(EnJackal* self, s32 gait) {
    if (gait == 1) {
        EnJackal_Stop(self, 0.5f);
    } else {
        f32 speed = (gait == 2) ? WALK_SPEED : RUN_SPEED;
        f32 radius = (gait == 2) ? 110.0f : 260.0f;

        self->actor.world.rot.y += (s16)(speed / radius * (0x8000 / M_PI));
        Math_SmoothStepToF(&self->actor.speedXZ, speed, 0.4f, 0.6f, 0.02f);
    }
    self->action = EnJackal_Wait;
    self->timer = 20;
}

static void EnJackal_Init(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;

    ActorShape_Init(&self->actor.shape, 0.0f, ActorShadow_DrawCircle, 22.0f);
    SkelAnime_InitFlex(play, &self->skelAnime, (FlexSkeletonHeader*)JACKAL_ASSET("gJackalSkel"),
                       (AnimationHeader*)JACKAL_ASSET("gJackalIdleAnim"), self->jointTable, self->morphTable,
                       JACKAL_LIMB_MAX);
    self->anim = JACKAL_ANIM_IDLE;
    Collider_InitCylinder(play, &self->collider);
    Collider_SetCylinder(play, &self->collider, &self->actor, &sCylinderInit);
    CollisionCheck_SetInfo2(&self->actor.colChkInfo, NULL, &sColChkInfoInit);
    Actor_SetScale(&self->actor, 0.01f);
    self->actor.gravity = -1.5f;
    self->actor.minVelocityY = -12.0f;
    self->actor.targetMode = 3;
    EnJackal_SetupWait(self);
}

static void EnJackal_Destroy(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;

    Collider_DestroyCylinder(play, &self->collider);
    ResourceMgr_UnregisterSkeleton(&self->skelAnime);
}

static void EnJackal_Update(Actor* thisx, PlayState* play) {
    EnJackal* self = (EnJackal*)thisx;
    Player* player = GET_PLAYER(play);
    s32 gait = CVarGetInteger(JACKAL_GAIT_CVAR, 0);
    s16 lookYaw = 0;
    s16 lookPitch = 0;

    if (gait != 0) {
        EnJackal_ShowGait(self, gait);
    } else {
        self->action(self, play);
    }
    self->actor.shape.rot.y = self->actor.world.rot.y;
    EnJackal_UpdateGait(self);
    SkelAnime_Update(&self->skelAnime);
    Actor_MoveXZGravity(&self->actor);
    Actor_UpdateBgCheckInfo(play, &self->actor, 20.0f, 18.0f, 30.0f, 5);

    // its head follows the player while it is not running
    if (gait == 0 && self->anim != JACKAL_ANIM_RUN && self->actor.xzDistToPlayer < NOTICE_RANGE + 80.0f) {
        Vec3f eyes = self->actor.focus.pos;
        s16 off = self->actor.yawTowardsPlayer - self->actor.shape.rot.y;

        lookYaw = CLAMP(off, -0x3000, 0x3000);
        lookPitch = Math_Vec3f_Pitch(&eyes, &player->actor.focus.pos);
        lookPitch = CLAMP(lookPitch, -0x1000, 0x1800);
    }
    Math_SmoothStepToS(&self->lookYaw, lookYaw, 5, 0x700, 0x40);
    Math_SmoothStepToS(&self->lookPitch, lookPitch, 5, 0x400, 0x40);

    Actor_SetFocus(&self->actor, 30.0f);
    Collider_UpdateCylinder(&self->actor, &self->collider);
    CollisionCheck_SetOC(play, &play->colChkCtx, &self->collider.base);
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
    if (!ResourceMgr_FileExists(JACKAL_ASSET("gJackalSkel"))) {
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
    return Actor_Spawn(&play->actorCtx, play, sActorId, x, y, z, 0, yaw, 0, 0);
}
