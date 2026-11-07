# ClaudeMark toolkit (for Claude)

Scripts behind the Analyse, Critique and Photo-montage presets. Rhino-side files run in the MCP's IronPython tool
by `exec(open(path).read())`, **cm_core.py first**. CPython files run from the shell with `python -I`.

| File | Runs in | Does |
|---|---|---|
| `cm_core.py` | Rhino | capture loading; `Cam` (pixel -> ray, world -> pixel from the saved matrix); `Scene` (visible meshes, blocks expanded, ray hits); `pick`, `snap`, `capture_from_meta`, `aspect_view`, `HiddenMarkers`, `SavedView` |
| `cm_analyse.py` | Rhino | `section`, `sun_hours`, `shadow_study`, `isovist`, `eye_level`, `photomatch_pairs`, `photomatch_apply`, `photomatch_render_start/finish`, `montage_occlusion`, `job_step` |
| `cm_reply.py` | Rhino | `post_reply`: a crit drawn on the user’s capture, into `outbox/` for ClaudeReply |
| `cm_photomatch.py` | CPython | camera solve from photo-match pairs (OpenCV + scipy), outlier search, leave-one-out check |
| `cm_post.py` | CPython | drawings: section SVG/PNG, sun sheet, shadow sheet, isovist SVG/PNG, edge overlay, montage |
| `cm_sketch.py` | CPython | options boards: massing primitives drawn over the user’s view with z-buffer occlusion |
| `cm_render.py` | Rhino | render styles: `style_begin` (snapshot), `style_light`, `style_preview`, `style_preview_end`, `style_restore`, `style_commit` |
| `cm_style.py` | CPython | render styles: read a reference, `resolve` suns (site-true dates), `.vropt` per style, `fit` / `grade` / `cube`, `board`, `post`, `bake`, `compare` |

Outputs go beside the user’s .3dm in `ClaudeMark\<stamp>\` when the file is saved, else in the capture's `analysis\`.

## Rules learnt building it (2026-10-18)

- **Keep every MCP call short.** A long MCP call can take Rhino down. Slow work is a job: start it, then
  `job_step(jid, 6.0)` until "done". (Rhino's crashes on 2026-10-18 were most likely ClaudeMark's own sidebar bug, a
  KeyError on the new tabs, not these tools. ClaudeMark's handlers are now `@guarded`.)
- **A capture's image can be older than the model.** World-anchored marks land where geometry is NOW, so
  `post_reply(background="auto")` recaptures from the saved camera and uses that image if the model changed. Never
  test placement by projecting current geometry and picking it back: that check is circular. Check against the image.
- **Section lines are plan lines:** their ends are read on the construction plane, not the first wall hit.
- **A floating view's drawing area is not the window rectangle:** use `aspect_view(W, H)` so captures and V-Ray
  renders keep the photo's exact field of view. A capture at another aspect keeps the horizontal FOV.
- **Pass the display mode to CaptureToBitmap** (`capture_from_meta(meta, out, "Shaded")`); setting the viewport's mode
  doesn't reach the capture. Pen/Technical captures drew hidden lines here: edges come from HiddenLineDrawing.
- **The HLD keeps the side away from its clip plane's normal.**
- **Analysis captures hide the `ClaudeMark::` layers** (section lines, isovists) via `HiddenMarkers`.
- **Isovist rays that hit nothing stop at the model's edge**, not at infinity; the count is reported.

## Recipes

**Section** (Analyse preset "Section along the line")
```
jid = section(stamp, name="Section A")        # flip=True looks the other way
job_step(jid)                                 # until "done: ...section.json"
python cm_post.py section <json> <outdir>     # Section A.svg/.png at a standard scale on A3, + cut line on the user’s view
```

**Sun hours**: loops or boxes on the capture.
```
jid = sun_hours(stamp)                        # dates default 21 Jun + 21 Dec, 30 min steps, site = doc sun, else SMM in cm_analyse.py (Greenwich: set your site in Rhino's Sun panel)
job_step(jid)  ->  python cm_post.py sun <json> <outdir>
```
Sanity check before sending: south of the equator a south face gets ~0 h on 21 June (north of the equator: a north face on 21 December).

**Shadow study**: `jid = shadow_study(stamp)`, then `job_step`, then `cm_post.py shadows`. The user’s sun and view are restored.

**Isovist**: `isovist(stamp)`, then `cm_post.py isovist`. Pins on walls drop to the floor below.

**Eye-level views**: `eye_level(stamp)` saves named views "CM eye level <stamp> pin N" + Rendered PNGs.

**Photo montage** (Render preset "Photo montage")
1. `photomatch_pairs(stamp)` -> pairs.json (model points snapped to visible corners within 10 px).
2. `python cm_photomatch.py solve pairs.json camera.json --photo <original>` -> rms, leave-one-out, dropped pairs.
   Report the warnings. Swapped numbers and stray clicks are found and named.
3. `photomatch_apply(stamp, camera.json)` -> named view + edges.json; `cm_post.py overlay <photo> edges.json out.png`.
   SHOW THE USER THE OVERLAY before rendering.
4. `photomatch_render_start(stamp, camera.json, proposal_ids, sun_from=<EXIF DateTimeOriginal>)`; wait for the
   PNG; `photomatch_render_finish()` (shows exactly what it hid).
5. `jid, n = montage_occlusion(stamp, camera.json, proposal_ids, render_png)`; `job_step` -> `_mask.png`.
6. `python cm_post.py montage <photo> <render.png> <out.jpg> <mask.png>`.
Not yet done: the proposal's shadows on the existing ground (needs a matte/shadow catcher).

**Options board** (Model preset "Options board (sketched)"). Sketched, NOT modelled: this saves the user’s Claude usage.
1. Decide three genuinely different principles (not variants), each with a one-line idea. Anchor them on the user’s marks
   with `pick()` / `snap()` (cm_core) to get world coordinates.
2. Write `options.json`: per option a handful of primitives (box, prism, cylinder, faces, loft, line) plus notes.
   Use only what conveys the idea; no detail. The schema is at the top of `cm_sketch.py`.
3. `depth_from_meta(meta, <capture>/analysis/depth.bin)` in Rhino: one z-buffer snapshot, under 1 s.
4. `python cm_sketch.py render <capture> options.json <out> --depth <depth.bin>` (~0.5 s). Look at `board.png`.
5. `python cm_sketch.py post <capture> <out>`, then tell the user to type ClaudeReply. The user flips with the arrows or the
   chips, marks on one, and sends. The answer carries `chosen_option`.
6. Model ONLY the picked option, with the user’s changes.
Occlusion: hidden only when clearly behind the user’s model (0.4%), so things ON existing surfaces stay visible.
**On a big model the z-buffer depth is noise (found 2026-10-20, SMM Building, capture 173749):** Rhino sets near/far
from the whole model, so `depth_from_meta` read the basin at 25 m with +-1.5 m of sawtooth error, and the sketch came
out ghosted in vertical bands. Look at the board for bands with stair-stepped edges; if you see them, use
`cm_depth_raycast.cs` (MCP C#, Idle job) instead: fill in the inverse matrix, camera, occluder box and screen region,
and it ray-casts the render meshes into `depth_rc.bin` in the same format. 174k rays over 396 meshes took 1.9 s.

**Crit reply** (Critique presets)
```
post_reply(stamp, title, [{"n": 1, "at": [x, y, z], "label": "short claim"},
                          {"n": 2, "px": [x, y], "label": "..."}], summary="...")
```
Then tell the user to type **ClaudeReply** in Rhino. The user’s answer comes back as a capture with `reply_to`, `claude_strokes`.

**Render styles** (Render preset "Style board (3 looks)"). Real V-Ray previews, not paint-overs: every look on the board
is something V-Ray rendered from settings that can be saved, so picking one can be set up for real. Order matters.
1. `python cm_style.py read <ref> <capture>/analysis/styles` -> ref_read.json + ref_read.png. Look at the reference
   yourself too: which side the sun is on, how high, sky colour, haze, what is grade and what is content.
2. Write `styles.json` (schema at the top of cm_style.py): A matches the reference as closely as V-Ray physically
   can; B and C each change ONE big thing about the light (time, sky, haze), not just the grade.
   A gets `"grade": "fit"`; B and C get `{"look": "A", "median_L": n}` (A's tone curve, their own exposure).
   `python cm_style.py resolve <capture> styles.json`: camera-relative suns become azimuths; `site_true` finds the
   real date and time at the site, or warns that none exists (say so on the board, it is printed there).
3. Rhino: `style_begin(stamp)` snapshots the user’s settings + sun (once; never overwritten).
4. Per style: `cm_style.py vropt before.vropt styles.json X style_X.vropt`; Rhino `style_light(stamp, style)` in ITS
   OWN MCP call; then `style_preview(stamp, "X", vropt)` (960 px, 1 min); wait for `X.denoiser.png`;
   `style_preview_end()`. Check each preview before the next: clipping or a black frame means the EV is wrong.
5. `cm_style.py fit ref_read.json previews/A.denoiser.png previews/grade_A.json`, then
   `cm_style.py board <capture> styles.json previews <out>`; LOOK at board.png; `cm_style.py post <capture> <out>`.
6. `style_restore(stamp)`. Tell the user to type ClaudeReply. The user flips, marks tweaks, sends; it arrives as `[Style X]`
   with `options_mode: style`.
7. Commit the pick: `cm_style.py bake styles.json X previews/grade_X.json styles_commit.json` (fitted exposure and
   white balance go into the camera), apply the user’s tweaks to styles_commit.json, `cm_style.py vropt before.vropt
   styles_commit.json X commit_X.vropt --lightmix`, `cm_style.py cube previews/grade_X.json look_X.cube --look-only`;
   Rhino `style_light` (own call), then `style_commit(stamp, style, vropt, name, long_side, minutes)` -> named view
   "CM style <name>", `final/<name>.vropt`, render. Wait, `style_preview_end()`, then
   `cm_style.py grade final/<name>.denoiser.png previews/grade_X.json final/<name> graded.png --look-only` and
   `cm_style.py compare <graded final> <out>/X_clean.png`: mean dE under ~3 means the user got what the user picked.
   Save the style as a ClaudeMark preset (presets/render/style-<name>.json, files: styles json, .cube, .vropt).
Rules learnt building it (2026-10-18):
- **V-Ray's frame-buffer layers can't be set by script.** A `/SettingsVFB` `vfb2_layers` string loaded through a
  .vropt is accepted and ignored by the render. So the look is a .cube (the user’s VFB LUT layer, Photoshop, Premiere);
  exposure and white balance are baked into the camera, where they are real.
- **LoadSettings merges.** Plugins the file doesn't name stay. Previews only add an unreferenced TexSky; Light Mix is
  added only at commit (it writes a PNG per light beside the output and gives the user the VFB Light Mix sliders).
- **A sun change reaches V-Ray on Rhino's next idle:** a render started in the same call rendered the old sun.
- **Haze (`VolumeAerialPerspective.distance`) is in model units:** 300 in a mm model is a 0.3 m white-out. Recipes
  give `distance_m`; `resolve` writes `units_per_m`.
- **Guessing EV is the weak step.** Rough EV100 with V-Ray's physical sky: clear noon 15, golden hour 12-13, sun 5 deg
  up 12, blue hour (sun -4 deg, PRG Clear Sky New) 8, CIE overcast at 18000 lx 15 (11.5 was a white frame).
  Clipped previews can't be fixed in post: re-render.
- **Only the tone curve carries from A to B and C.** A's tints and saturation correct A's light against the
  reference; on another light they went garish (blue hour turned saturated blue, overcast highlights cyan).
- **Tone percentiles are partly content.** The fit holds the curve near identity and matches the black and white
  ends hardest. Tints per band (shadow, mid, highlight) are capped at 12; 20 turned a sun glow green.
- **Rhino's manual sun takes a negative altitude** (blue hour), and Hosek (model 3) suits a dusty low sun.
- **At about 38 deg south a low sun never comes from north of about 300 deg:** resolve says so.
- V-Ray already has **Light Gen** (`vrayShowLightGen`): many lighting thumbnails of a view, no reference matching,
  no grade, no reasons. Worth suggesting when the user just wants to browse light.
- **NEVER assign a plugin list through rhVRay** (e.g. EnvironmentFog `lights`): it crashed the user’s Rhino (access violation).
- **Two-point views:** V-Ray only honours the shifted frustum on a view FLAGGED two-point; cm_core rebuilds them so.
  Check a preview's framing against clean.png before trusting it.
- **Big models:** renders start from a timer after the MCP call returns (`_render_later`); a direct `rhVRay.Render`
  blocks for the scene export (about a minute on SMM Building) and times the MCP out.
- **Light rigs:** `rig_build(stamp, rig, group)` -> MCP `run_command("_-vrayLightMesh _Enter")` -> `rig_params`; one group
  per call. `rig_remove(stamp)` deletes the surfaces and switches their plugins off. The rig is in styles.json "rig".


**Modelled fixtures** (`cm_fixtures.py`, Rhino; after cm_core + cm_render). Light "from thin air" reads as fake.
- `up_hit(x, y, z)` finds what is straight above, for hanging things from real structure.
- `spot_fixture(lens, aim, mount_z, layer, name)` models a can, bezel, rear cap, yoke, knobs, drop rod and ceiling plate,
  plus a lens disc. Then `move_spot(light_id, fx["source"], fx["direction"])` puts the V-Ray spot 25 mm in front of the lens.
- `bottom_rails(layer_text, toward)` + `led_trim(rail, layer)` add a channel on a balustrade's bottom rail and a diffuser.
  Choose `toward` as THE CAMERA (or the side people see): "toward the stage" put 23 of 29 trims on the hidden face (2026-10-18).
- Lenses and diffusers become lights with `rig_adopt(stamp, group, ids)` -> `_-vrayLightMesh _Enter` -> `rig_params`.
- Materials: reuse the user’s V-Ray materials (`obj.RenderMaterial = rm; CommitChanges()`). `doc.Materials.Add` makes a legacy
  material with no render material, so `_-vrayMtlFromRhino` has nothing to convert.
