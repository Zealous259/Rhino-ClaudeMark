# ClaudeMark 4

Capture the active Rhino view, mark it up, send it to Claude Code. No screenshot, no Downloads, no drag-and-drop.

Version 4 (5 Nov 2026) is a new window in the style of Apple's Liquid Glass: the capture fills the window, and the
tools float over it on glass that blurs and bends what is behind it and darkens or lightens itself so it stays readable.
When it opens, the capture comes into focus and the bars condense out of it as glass: each one grows into place with a
little give while the bend at its edges overshoots and settles, like a drop of liquid (about a second; Reduce motion in
Settings, or Windows' own "Animation effects" switch, skips it). The data it sends is the version 3 data plus a few fields (below), so the hook, `tools/` and ClaudeReply carry on as
before. The old window is still there as the Rhino command `ClaudeMarkClassic` (and `ClaudeReplyClassic`), in `classic/`.

## Use

1. In Rhino type `ClaudeMark`. Select first if the selection matters; it is recorded.
2. Mark the capture. The tools are in the bar at the top; hover any button for its name and key.
   - **Box** (B), **Ellipse** (O), **Revision cloud** (C): drag over the area. Shift makes a square or a circle.
     These, and **Pin** (N), are **numbered** 1, 2, 3... so the request can say "fix 2 and 4".
   - **Pen** (P): freehand; hold Shift for a straight segment. **Highlighter** (M): a wide translucent marker.
   - **Arrow** (A): drag tail to tip; Shift snaps to 15 degrees.
   - **Text** (T): click and type on the spot it applies to. Enter finishes, Shift+Enter is a new line, Esc cancels.
   - **Measure** (D): drag between two points on the model. Rhino picks the model under both ends and the label shows
     the real distance in model units (633 mm, 4.21 m). In a parallel view (plan, elevation) it measures on the view
     plane when an end misses the model.
   - **Eraser** (E): rubs out pen and highlighter lines where it passes; other marks go whole; references stay.
   - **Select** (V): click a mark to move it, resize it from its handles, recolour it (click a colour) or change its
     width. A small bar over it offers Comment, Duplicate, Bring to front and Delete. Arrow keys nudge it.
   - **Comment on any mark**: double-click it, or use the Marks list. The comment is drawn next to the mark's number
     and sent with it.
   - **Photo match** (Shift+M): for montages. Double-click a dropped photo to open it, click points on it, Tab back,
     click the same points on the model in the same order (6 or more, spread out, not all on one plane).
   - Colours 1 to 7, widths with [ and ]. Undo Ctrl+Z, redo Ctrl+Y. Right-click a mark (or the capture) for a menu.
3. Look around: scroll or Ctrl+scroll zooms at the cursor, Space+drag or middle-drag pans, Ctrl+0 fits, Ctrl+1 is
   100%. The **crosshair** button (top right) shows Rhino's pick under the cursor: the object, its name and layer.
4. **References**: drag image files from Explorer or images from a browser onto the spot they apply to, or Ctrl+V a
   copied image. Each lands as a card (R1, R2...): drag to move, scroll over it to resize. The + button adds one from a
   file, and Find examples (Ctrl+F) docks Chrome beside the window on Google Images. Say what each one is for in the
   sidebar, under Images on this capture (it starts as the likeliest for the task: a material to match for Materials).
5. **Marks list** (top right): every mark with its number, what Rhino found under it, and a comment box.
6. **Task** (the sidebar, top left; redone in 4.1): pick the kind of job from the eight tiles (General, Model, Fix,
   Materials, Lighting, Render, Analyse, Critique). Each says what it **runs in** (for Render: V-Ray 7 in Rhino 8, over
   the Rhino MCP) and, under **How it works**, the steps Claude takes. Below that:
   - **Setups**: saved sets of options. Click one to set them; it stays lit while they match. **Save these** keeps the
     options as they are under a name; right-click one of yours to update, rename or delete it (a built-in can be
     hidden). Render comes with Quick preview, Final 4K, A3 board, Clay study and Instant look; Lighting with Golden
     hour, Midday, Overcast, Blue hour and Studio white.
   - **Options**: the controls for that job, remembered per task. **Reset** puts a task back to its defaults.
     - Render: what to make (a render, a photo montage or a style board), the engine, size (presets, A3 and A2 at 300
       dpi, or your own), camera (this view, two-point at eye level, axonometric or a named view), light, clay, people,
       background, format, where it saves and whether it opens. The engine is V-Ray, Rhino's Raytraced, or a **Python
       script** (rhino3dm reads the saved .3dm; moderngl draws linework, a white model and shadows on the GPU in about
       half a second, or Blender renders it with Cycles; Rhino stays free). Each engine's own settings **pop up** beside
       the sidebar when you pick it, and from its settings row: V-Ray has quality, a time limit, the sampler, global
       illumination, the denoiser, render elements, lens effects and your VFB corrections; Raytraced has passes,
       bounces, the denoiser and caustics; Python has the renderer, what to draw, edges, line weight, where shadows
       come from and supersampling (or Blender's samples).
     - Model: geometry type, layers, backup, real product sizes, save first, and **Detail**: the level of detail as BIM
       uses it (LOD 100 massing to LOD 400 fabrication), the parts modelled in full down to their real components
       (build-ups, windows and doors, stairs and rails, structure, roof edges, facade and cladding, interior finishes,
       fixtures and services, landscape), the smallest detail (1 to 100 mm), what the detail applies to (what you
       marked, the view, the whole model), every copy as a block (like a Revit family), Revit-style organisation
       (layers by category, every part named with its type, material and thickness), and NCC sizes for stairs,
       balustrades and doors. Setups: Massing, Design model, Construction detail, Revit-grade.
     - Fix: for modelling errors, where a one-word note ("gap") is enough. What to look for (gaps and holes, missing
       glass, overlaps and clashes, odd ones out, stray objects, missing detail), where (your marks, around them, the
       view, or a sweep of the whole model that lists everything first), how (matching what is around it, the simplest
       clean surface, or asking first), the widest gap to close, backup, save, a check that it is closed, and a before
       and after. Setups: Quick fix, Careful fix, Sweep the model.
     - Critique: the critic, the Architectural council (3.1: seven architects, Whitmore chairs), the Engineering
       council (2.1.1: ten engineers in five fields against the NCC, Chen chairs), both councils in one reply, or the
       project brief; Quick or Full length, whether the council sees the image, and checking the brief with you first.
       The token cost shows under Runs in.
     - Lighting: sun and sky (a time of day and a season, or an exact date and time), an HDRI, a studio or your own
       lights, the sky, rooms lit inside, exposure, a test render. Materials: what they go on, what happens with none
       picked, scale, grain, clay for the rest. Analyse and General have theirs.
   - **Materials to use** (Materials): your library is the Cosmos materials you have downloaded, with their previews,
     plus your own .vrmat files and photos. Pick several; each goes on a numbered mark, the selection, everything else,
     or wherever your note says. Add to it from a file or by dropping onto the open library. Your own photos show as an
     icon, never the photo. Lighting has the same for HDRIs.
   - **Images on this capture**: R1, R2 and so on, each with what it is for (a material, a look or a light to match,
     the site photo for a montage, a precedent, or context). Images are for this request only: dropping one anywhere,
     the sidebar included, adds it to the capture and never turns it into a preset.
   - **Your instructions**: standing instructions for that kind of job, sent every time.
   - **What Claude gets**: the exact brief the options make. It goes into meta.json and, through the hook, to Claude as
     the first preset.
   Each capture starts on General unless "Keep for the next capture" is on; options and setups are remembered either
   way. The small **?** beside a section says what it does. The + in the request bar adds an image to this request, or
   saves or copies the marked image.
7. Type the request and press **Enter** (Shift+Enter for a new line, Up in an empty box for earlier requests).
   The window closes, the capture is written, and the request and the marked image are pasted into the Claude Code
   window named on the **terminal button** in the request bar. That button lists every Windows Terminal window by its
   title, which is the Claude Code session name, and remembers the one you pick (version 3 pasted into whichever window
   Windows called the terminal's main one, which with several sessions open is effectively arbitrary). "Don't paste"
   leaves the capture to be attached to your next prompt in any session.
8. **Batch** (Ctrl+Shift+Enter, or the stack button): keep this capture and mark another view. Batched captures wait
   in `queue/`, not the inbox, so nothing goes out early. The next Send pastes them all, oldest first, with your request.
9. **Esc** steps back (cancels a stroke, deselects, leaves photo mode), then asks before throwing marks away. Every
   change is saved as a draft while you work, so a crash or a closed window loses nothing: the next ClaudeMark offers
   "Unsent markup from 14:32" with Restore. Drafts are kept for 7 days.
10. **Ctrl+S** saves the marked image as a PNG (the folder is remembered); **Ctrl+C** copies it. The marked image is also
    left on the clipboard after a send.
11. **Settings** (sliders, top right): appearance (Auto, Dark, Light), accent colour, paste on/off, the hover pick,
    numbering, comments on the image, keep the task, glass refraction, reduce transparency, reduce motion. **?** lists
    every shortcut.

## ClaudeReply: answering Claude's crit

When Claude posts a crit (`tools/cm_reply.py`), the next ClaudeMark shows a banner, "Claude replied to ...", with Open
and Dismiss; or type `ClaudeReply`. Claude's findings (C1, C2...) are drawn in orange on the capture and listed under
"From Claude" in the Marks list. H, or Hide in the banner, hides them. Mark and type your answer on top and send; it
arrives tagged as a reply, with each of your marks related to the nearest finding. Close leaves the reply waiting;
answered and dismissed replies move to `outbox/answered/`.

**Options boards** and **render style boards** open the same way, with A / B / C at the top: the arrow keys or the
letters flip between them, and the one showing when you send is the one you pick. See TOOLS.md for how Claude makes them.

The note is cleaned before it is typed: one line, `@` becomes "at", and a leading `/`, `!` or `#` gets "Note: " in
front, so it can't trigger a Claude Code command.

## Files

| Path | What |
|---|---|
| `claude_mark.py` | The Rhino side (IronPython 2.7, run by the `ClaudeMark` alias): capture, camera, picks, presets, send, paste, and the window host |
| `ui/index.html`, `ui/app.js`, `ui/app.css` | The window itself, a WebView2 page mapped to `https://cm.app/` (this folder) |
| `ui/fonts/` | Inter (SIL Open Font License, `Inter-LICENSE.txt`), the closest free match to Apple's SF Pro |
| `ui/mock.js` | A stand-in for `claude_mark.py`, for working on the window in a browser: `ui/index.html?mock=1` |
| `claude_reply.py` | `ClaudeReply`: opens the newest reply in `outbox/` (runs claude_mark.py in reply mode) |
| `classic/` | The version 3 window, unchanged, with its own `claude_reply.py`: `ClaudeMarkClassic`, `ClaudeReplyClassic` |
| `inbox/<stamp>/` | Sent, waiting for the next prompt. `.<stamp>` = being marked now, or a draft (holds `state.json`) |
| `queue/<stamp>/` | Batched, released into the inbox by the next Send |
| `sent/<stamp>/`, `stale/<stamp>/` | Attached to a prompt; or older than 6 h when the next prompt came |
| `outbox/<stamp>-reply/`, `-options/`, `-styles/` | Claude's crits and boards; `answered/` holds the ones done |
| `tools/` | Claude's scripts for the Analyse, Critique and montage jobs; `tools/TOOLS.md` says how to run them |
| `presets/<task>/` | Your setups (`kind: setup`, their `options`), library items (`kind: library`) and older presets of your own. The 4.0 built-ins still there are skipped: their jobs are options in the sidebar now (`ui/app.js`, `TASKS`) |
| `.cache/claudemark.log` | Start-up times, page errors and failures. Read this first if the window misbehaves |
| `.cache/thumbs/` | 96 px copies of preset previews |
| `claudemark_settings.json` | Tool, colour, width, task, presets, paste target, window size and the settings above |
| `hook/rhino_markup_inbox.py` | UserPromptSubmit hook that attaches pending captures (it finds this folder from its own location) |

Alias macro: `! _-RunPythonScript "<this folder>\claude_mark.py"`. Every script finds this folder from its own location (or `CLAUDEMARK_ROOT`), so the folder can live anywhere.

If WebView2 cannot start, or the page does not answer within 12 s, the classic window opens instead and the reason
goes to Rhino's command line and the log.

## meta.json

Everything is recorded at the moment of capture, before the window opens. The window is modal, so the camera cannot
move until it closes.

| Key | Content |
|---|---|
| `captured`, `captured_unix` | Local ISO time, epoch seconds |
| `version` | 4 |
| `rhino_pid` | The Rhino process. Compare with the Rhino the MCP is driving |
| `doc` | `path`, `name`, `modified`, `units`, `object_count` (live objects; version 3 counted deleted table slots too), `current_layer` |
| `view` | `name`, `viewport_id`, `display_mode`, `size` [w, h], `maximized`, `screen_rect` |
| `image` | `clean.png` is the capture; `marked.png` has the marks burned in, rendered by the window itself, so it is exactly what the user saw. Both are exactly viewport size |
| `camera` | `projection`, `location`, `target`, `direction`, `up`, `lens_35mm`, `two_point`, `frustum` {left, right, bottom, top, near, far}, `world_to_screen` (4x4, row-major) |
| `cplane` | `origin`, `x`, `y` |
| `selection` | Selected objects: `id`, `type`, `name`, `layer` (full path) |
| `subobject_selection` | Objects with a face or an edge sub-selected, with `components` like `BrepFace#3` |
| `clipping_planes` | Clip planes active in this viewport: `origin`, `normal_kept_side`, `hidden` |
| `history_tail` | The last 15 command-history lines |
| `note` | The request typed in the window |
| `task`, `presets` | The task ("general" when none) and the chosen presets in full (`name`, `instructions`, `files`...). Since 4.1 the first is the sidebar's brief (`kind: options`, named like "Render options") |
| `task_options` | The task's options as set in the sidebar, plus `picks`: the materials or HDRI picked (`name`, `file`, and `on`: `note`, `sel`, `rest`, or `#3` for mark 3) |
| `task_brief` | The brief those options make, word for word what Claude is told |
| `strokes` | Every mark, in image px. Common: `kind`, `colour`, `rgb`, `width` (px), `points` [[x, y], ...], optional `comment` (the user’s instruction for that mark) |
| `picks` | Hits under each stroke: `stroke`, `kind`, `label`, `role`, `px`, `hit` {`id`, `type`, `name`, `layer`, `point`} or null |
| `reply_to`, `reply_id`, `reply_title`, `claude_strokes` | Only on an answer to a ClaudeReply: the capture Claude's crit was on, the outbox folder, its title, and Claude's marks (`by: claude`; findings are pins labelled C1, C2...) |
| `chosen_option`, `options`, `options_mode`, `styles_file`, `styles_previews` | Only on a pick from a board |

Stroke kinds:

| `kind` | Points and extra fields |
|---|---|
| `box`, `ellipse`, `cloud` | Two opposite corners of the box they fill; `label` (number) when numbering is on; `closed: true`. Picks: `centre` plus `corner` (box, cloud) or `edge` (ellipse, the four extreme points) |
| `pin` | [[centre]]; `label` (number). Pick role `pin` |
| `arrow` | [tail, tip]. Picks `tip`, `tail` |
| `pen`, `highlight` | The line, densified where erased; `closed: true` for a loop. A highlight's `width` is the marker width (16, 26 or 44 px). Picks `start`, `end`, `along`, and `centre` for a loop |
| `measure` | [a, b]; `measure` {`value`, `units`, `text` like "633 mm", `method` "surface" (both ends on the model), "plane" (parallel view, on the plane through the camera target) or "none", `a`, `b` (world points), `a_hit`, `b_hit`}. Picks `start`, `end` |
| `text` | [[top-left]]; `text` (lines split by `\n`), `font_px`, `size` [w, h] of its tag. Picks `centre`, `anchor` |
| `ref` | [[centre]]; `label` (R1...), `role` (what it is for: `context`, `like`, `precedent`, `material`, `light`, `look`, `site` or `style`), `file` (refN.png, at most 2000 px), `source` (the file's path, the image's URL, `dropped image <name>` for a file a browser or app handed over with no path on disk, or `pasted image`), `size` [w, h] of the card; a dropped file adds `original` (refN_original.*, full resolution with EXIF); a photo opened for matching adds `matches` [[u, v], ...] (fractions of the photo, in number order) |
| `match` | [[x, y]] on the capture; `label` (its number), `ref` (the photo it pairs with) |

Numbers are shared: boxes, ellipses, clouds and pins count 1, 2, 3... together, in the order drawn, and a new mark takes
the next number after the highest (a deleted number is not reused).

### Pixel to world

Screen point from a world point: `[x, y, _, w] = world_to_screen · [X, Y, Z, 1]`, then `x/w, y/w`.
The camera target maps to the image centre. To go the other way, rebuild the camera in a fresh `ViewportInfo` (see
`notes/RHINO-VRAY-NOTES.md`, section rhino-viewport-camera-traps) or invert the matrix on the near and far planes.

The picks come from Rhino's own point pick, plus a ray test against the selected objects, which that pick skips. The
nearest render-mesh hit along the view ray wins. Curves, points and blocks fall back to Rhino's selection point. In
Wireframe only edges can be hit. Treat the picks as hints: verify a pick by projecting the candidate's edges onto
`clean.png` before acting on it. A reply or a restored draft picks through its saved camera unless the live view has
not moved.

## Working on it

- The window can be developed in a browser: serve this folder (for example `python -m http.server`) and open
  `ui/index.html?mock=1`. `mock.js` answers like Rhino would and `&scenario=marks` draws one of everything.
- `claude_mark.py` honours `CLAUDEMARK_TEST_ROOT` (a scratch copy of this folder, with a real `ui/` folder: WebView2
  will not serve files through a directory junction) and `CLAUDEMARK_TEST` = `{"no_paste": True, "tests": [(seconds
  after ready, "js" | "shot" | "send" | "close", argument)]}`, set as globals before running it, for scripted runs that
  never touch the real inbox or type into a terminal. `window.CMTest` in the page drives the real handlers.
- The hook honours `CLAUDEMARK_ROOT` the same way. Never test against the live inbox: any session's next prompt would
  attach the test.
