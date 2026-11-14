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

## Attacks (overhauled 2026-11-14)

Her sword attacks on the ground no longer mirror Link's. They are the Gerudo's own animations at the speed they were made for, chained as a combo of her own (see "attacks" in `PlayAsNabooru.cpp`):

| Step | Animation | Frames | Blade hits on |
|---|---|---|---|
| 1 | The fighter's slash (`gGerudoRedSlashAnim`): her right scimitar cuts down across her front | 10 (swing 0-7, then back to her stance) | 4-6 |
| 2 | The spear guard's unused horizontal slash (`gGerudoPurpleUnusedHorizontalSlashAnim`), from where her left foot steps in, then its recovery (`...UnusedSlashToStandingAnim`) with her arms easing into her armed idle instead of standing to attention | 7 + 11 | 2-4 |
| 3 | The fighter's leaping spin (`gGerudoRedSpinAttackAnim` from frame 10, already coiled), carrying her 40 units forward: the finisher | 24 (swing to 25, recovery to 33) | 14-23 |

- B during a swing goes on to the next step as soon as the blade is through; B during the recovery, or up to 12 frames after a swing, does too. Otherwise the combo starts over with the slash. The stick doesn't choose the attack, but a forward stab's lunge (22 units) still applies to whichever step is next.
- Mashing B, the three steps take 28 frames (Link's slash, slash, finisher: about 36). A single slash is 10 frames; Link's is 5 plus a 7-frame recovery.
- She stands her ground: Link's swing animations no longer move her (that was the 8-unit jump back at the start of every slash), and her legs are the animations' own.
- Holding B after a slash still charges a spin attack; walking, rolling, blocking or attacking cancels a recovery; walls and shields bounce her blade as they do Link's sword.
- Hitboxes and trails of every sword attack (combo, jump attack, spin attack, the stab from behind the shield) come from her scimitars, one hitbox and one trail per blade, instead of Link's hidden sword. The jump attack's landing cut stays active for its 4 frames (Link's sword: 2).
- The Megaton Hammer keeps Link's hitbox and the old mirrored swing.

Not done: the spin attack's shockwave ring stays at ground level while she leaps above it (it is where the shockwave hits). The crouch stab rises out of her kneel into the standing cut.

## Compared and fine

Idle (bare and armed), run cycle, turning around while running, backflip, knockdown and get-up from the front, front hits.
