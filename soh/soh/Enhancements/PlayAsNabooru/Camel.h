// The camel of the Play as Nabooru mod: Nabooru's mount (see Camel.cpp).
//
// It is one of the game's horses (ovl_En_Horse), of the type HORSE_CAMEL: ridden, steered, mounted and dismounted
// like Epona. What is its own is here: how it handles, and (in Camel.cpp) its model, its voice and where its rider
// sits.

#ifndef PLAY_AS_NABOORU_CAMEL_H
#define PLAY_AS_NABOORU_CAMEL_H

#include "z64.h"

#ifdef __cplusplus
extern "C" {
#endif

struct EnHorse;

#define CAMEL_ASSET(name) "__OTR__objects/object_camel/" name

// En_Horse's actor parameter that makes it a camel
#define ENHORSE_PARAM_CAMEL 0x4000

// How it handles. It is not spurred on (no carrots): held at a gallop it works up to its full speed by itself, a
// little short of Epona's when she is spurred on (14, falling back to 8.9), but it is heavier than she is: it gathers
// speed more slowly (she gains 0.5 a frame), most slowly near its full speed, takes longer to slow down and to stop,
// and turns less sharply. From a standstill it is at full speed in a good 2 seconds (measured; Epona, spurred on at
// once and kept at it, is at hers in about 1). Speeds are in units a frame.
#define CAMEL_TOP_SPEED 13.0f
// (with the stick pushed all the way, En_Horse goes 60/54 of the speed its gait is given)
#define CAMEL_GALLOP_SPEED (CAMEL_TOP_SPEED * 54.0f / 60.0f)
#define CAMEL_ACCELERATION(speed) (0.55f - 0.25f * (speed) / CAMEL_TOP_SPEED)
#define CAMEL_DECELERATION 0.3f
#define CAMEL_TURNING 0.85f // of how fast Epona turns
// Reined in from a gallop it digs in: it loses this much speed a frame (Epona 0.6) and stands still from this frame
// of its animation (HALT_STILL in tools/build_camel.py).
#define CAMEL_HALT_DECELERATION 0.45f
#define CAMEL_HALT_STILL_FRAME 44.0f
// How fast its gaits play, in animation frames a frame for each unit of speed: the pace at which its feet do not
// slide (frames of the animation / (game frames a cycle * speed), as in tools/build_camel.py).
#define CAMEL_WALK_PACE (35.0f / (20.0f * 3.0f))
#define CAMEL_TROT_PACE (29.0f / (14.0f * 6.0f))
#define CAMEL_GALLOP_PACE (24.0f / (9.0f * 13.0f))

// Called by En_Horse for a horse that is a camel.
void Camel_Init(struct EnHorse* horse, PlayState* play, AnimationHeader* idleAnim);
void Camel_Destroy(struct EnHorse* horse, PlayState* play);
void Camel_Draw(struct EnHorse* horse, PlayState* play);

// A camel standing there, to be ridden. NULL if the mod's camel archive is not installed.
Actor* Camel_Spawn(PlayState* play, f32 x, f32 y, f32 z, s16 yaw);
s32 Camel_IsCamel(Actor* actor);
// Where a rider of this camel is: the frame of its model as carried along by its body (its origin on the ground
// under it while it stands at rest), from when it was last drawn. False if it has not been drawn lately.
s32 Camel_GetSeat(Actor* camel, MtxF* seat);

#ifdef __cplusplus
}
#endif

#endif
