# Nabooru vs Link: animation review (2026-11-14)

Her moveset compared with Link's, move by move, against the twelve principles of animation. The comparison used
in-game footage of both, filmed with the same inputs and camera (tests `survey2`, `ledge`, `drop` in
`~/oot-fast64/tests`), plus measurements of Link's animations on his own skeleton (`link_animetion` data).

## Done in this round (big wins)

| Move | Link | Nabooru before | Now | Principles |
|---|---|---|---|---|
| Side hop (Z + A sideways) | Hips lead 10 units into the hop; feet trail 3-12 units behind; hips turned away from the hop, shoulders less; arms on the side he came from; narrower in the air (23 → 16 units wide) | Upright and straight over spread legs, arms out wide, wider in the air (20 → 34 units) | Arc: the trailing toes stay where she took off while her hips go over; legs tucked and trailing; hips turned away; arms on the takeoff side; the leading foot plants where it will stand and she slides on over it into the landing (one animation, no crossfade) | Arcs, squash & stretch, follow-through, staging |
| Running off a ledge | Leap: push-off leaning in, then a running stride in the air (knees bent), arms swinging against the legs | Blended into the knees-up air pose and held it | Follows his `normal_run_jump` frame by frame (her limbs point where his do). A first hand-keyed version (straight-legged split) looked silly | Anticipation, arcs, exaggeration |
| Drops of 80 units or less, standing | `short_landing`: sinks to 61° knees for 11 frames | No landing: straight-legged touchdown, then folded her arms | A light dip (hips sink 4 units) over his first 6 frames, then her idle. Her full hop landing was too emphatic | Squash & stretch, follow-through |

## Smaller cases, for you to decide

Roughly by how often they show in play. Your answers (2026-11-14): 1 done (full stride on frame 4), 2 doesn't matter, 3 comparison GIF in `~/oot-fast64/tests/gifs/hits_from_behind.gif`, 4-6 not worth it yet. 7: fine on slopes; her legs changing pose while she hung in the air after hopping down a slope is fixed (she holds the hop's last frame).

1. **Run start is late and stiff** (timing, anticipation). Link leans into his run for 2 frames while his speed is still 0. She stays in her idle for those frames, then crossfades idle → walk → run (5 frames each). Her full stride arrives around frame 9; Link's arrives at frame 4.
   - *Fix:* start her locomotion as soon as Link's run animation plays, with a faster crossfade from idle. The 3 frames of folded arms can stay as they are.
2. **No stop** (follow-through, slow out, foot contact). Link plays `walk_endL/R` (about 7 game frames): the trailing foot comes up beside the other, there's a small settling step, and the arms settle. She crossfades her walk cycle into folding her arms, so her feet keep striding (and slide) for about 4 frames after he has stopped.
   - *Fix:* generated StopL/StopR synced to `walk_end`. Pick the one that matches whichever of her feet is ahead, and end in her idle.
3. **Hit from behind: she falls the wrong way** (physics, staging). From behind, Link lurches or pitches forward (`back_shit`, `back_downA/B`; his head ends 14 units ahead of his hips). She always plays the fighter's front reaction, recoiling or falling backward, while the knockback carries her forward.
   - *Fix:* this needs a forward flinch and a forward fall. None of her source animations has one, so it would be a generated animation plus a `back_` rule.
4. **Walking off a ledge while turning** (anticipation). Link's `normal_jump` (crouch → extend → tuck, 9 frames) is rare: from stick 60 up he does the running leap, and below that he just falls. Hers is still the static air pose.
   - *Fix:* same tooling as the leap. About 20 lines.
5. **Leap always leads with the left leg** (Link's does too). If she was mid-stride on the other foot, her legs swap during the 2-frame blend-in. A mirrored variant chosen by her run phase would fix it.
6. **Falling is one held pose** (secondary action). Link's fall loop barely moves either, but a slow drift of the arms or ponytail would stop long falls looking frozen.
7. **Side hop on uneven ground** (not tested). The new hop assumes Link's flat-ground travel: 12.75 units per frame, then an 11.2 unit slide on landing. On slopes or against walls her planted foot may slide a little. Its touchdown frame (6) only shows when the landing comes late.

## Missing moves (fall back to her idle/walk/run)

Bigger than any polish item, but new work rather than fixes:

- **Roll** (A while running). Link's most frequent evasive move; she just keeps running through it.
- Climbing, ledge grab and climb-up, ladders, swimming, crawling, pushing and pulling, horse riding.

## Attacks (left for the attack overhaul)

Found while comparing; not touched.

- **No lunge.** Every one of Link's slashes is a deep lunge (front knee bent, back leg long, body low into the cut). She swings from an upright stance. While Link slides forward, the FightWalk legs overlay replaces the fighter slash's own knee bend (her pelvis drops 2.2 units and goes 8.6 forward in that animation).
- **Combo finisher recovery replays the swing.** Link's `fighter_Lside_kiru_finsh_end` (also the LL/LR variants) matches no `_end` rule, because `kiru_end` isn't a substring of `kiru_finsh_end`. It falls through to `_kiru` → `kSlash`, so she swings again during his recovery, with a 119° one-frame pop. One-line fix: add `"kiru_finsh_end"` to the `kSlashEnd` rule.
- **Possible hitch at slash starts.** On the first frame of every slash the player moves about 8 units back along their facing (7.8; 6.4 for the finisher, 9.6 for the pierce), then advances during the swing. Her drawn root follows the actor. The new `fwd`/`lroot`/`nroot` log fields couldn't tell whether Link's drawn model compensates, so check by eye.
- Spin attack and jump attack compared well with Link's; nothing big there.

## Compared and fine

Idle (bare and armed), run cycle, turning around while running, backflip, knockdown and get-up from the front, front hits.
