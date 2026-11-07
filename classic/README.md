# ClaudeMark

Capture the active Rhino view, mark it up, send it to Claude Code. No screenshot, no Downloads, no drag-and-drop.

## Use

1. In Rhino type `ClaudeMark`. Select first if the selection matters; it is recorded.
2. The markup window opens on a capture of the active view at full viewport resolution.
   - **Pen** (P): freehand. **Hold Shift** for a straight segment from where Shift went down;
     release and freehand carries on. A loop is read as "this thing".
   - **Box** (B): drag a rectangle. Shift makes it square.
   - **Arrow** (A): drag from tail to tip. Shift snaps to 15 degrees.
   - **Pin** (N): click to drop numbered pins (1, 2, 3...).
   - **Text** (T): click where the note applies and type. Enter finishes it, Shift+Enter starts a new
     line, Backspace deletes, Ctrl+V pastes text, Esc cancels the note (not the capture). The colour and
     the line width (small / medium / large text) apply to the note being typed. Drag a note to move it,
     scroll over it to resize, double-click (or click with the Text tool) to edit, right-click or Delete
     to remove.
   - **Eraser** (E): drag over marks. Pen lines are rubbed out only where the circle passes (a line can
     split into pieces); boxes, arrows, pins and text notes go whole when touched. Reference cards are
     never erased, so lines drawn across them can be cleaned up safely; remove a card with right-click or
     Delete. The line width sets the eraser size (shown as a circle). One drag is one undo step. Pins keep
     their numbers; a new pin takes the next number after the highest.
   - **Photo match** (M): for montages. Drop a site photo as a reference, then click it with this tool
     (or double-click it with any tool): it opens full-size. Click points on the photo, press Tab to go
     back to the model, click the same points there, in the same order (they pair by number). Tab flips
     between the two; Esc or the pill at the top returns to the model. Aim for 6 or more points, spread
     out and not all on one plane; corners are best (Claude snaps model points to corners). Right-click a
     photo point to remove it. A dropped photo FILE keeps its full-resolution original (and its EXIF date
     and lens) as `refN_original.*`.
   - **References**: drag image files from Explorer or images from a browser onto the view, or
     Ctrl+V a copied image. Each lands where it is dropped as a card (R1, R2...). Drag to move,
     scroll to resize, right-click or Delete to remove. Drop it on the place it applies to.
   - **Find examples** (top right, Ctrl+F): docks ClaudeMark on the left 58% of the screen and
     opens a new Chrome window on Google Images in the rest. Drag images straight across onto the
     view. Pressing it again re-docks the same Chrome window. From a browser, the image is taken
     from the pixels, then the page's `<img src>` (Google thumbnails are data: URIs), then the real
     image address inside a Google result link, then any URL. Downloads send browser headers.
   - **Analyse tab:** the mark is the question. A line for a section, a loop or box for sun hours, a pin
     for an isovist or an eye-level view; plus an hourly shadow study from this exact view.
   - **Critique tab:** say what you're claiming in the request box; the critic agent, the architectural council, the product council (tools and end-effectors),
     or the brief attacks it, and the findings come back drawn on your view (see ClaudeReply).
   - **Task sidebar (left):** pick the kind of job (Model, Materials, Lighting, Render, Analyse, Critique), then click
     presets to use them. Choices in several tabs combine, and the tab badges count them. The choice is
     remembered for next time. Your request is typed into Claude Code with a tag such as
     `[Lighting: Golden hour | Render: Final 4K, 16:9]`. Each preset's full instructions and files go in
     meta.json. Double-click a preset to edit its file. Right-click twice to remove it (built-ins are
     hidden, not deleted).
   - **Your own presets:** drop onto the sidebar a `.vrmat` (becomes a material), an `.hdr`/`.exr`
     (becomes a dome light), a `.vrscene`/`.vropt` (render settings), a preset `.json`, or a photo or
     browser image ("match this" for the open tab). They're saved in `presets/<task>/`, and Cosmos files
     show their Cosmos preview.
   - Colours 1-4, three widths. Undo Ctrl+Z or right-click, redo Ctrl+Y. Clear can be undone.
3. Type the request in the **Request** box. Enter sends, Shift+Enter starts a new line, Esc cancels.
   **Save...** (Ctrl+S) writes the marked image to a PNG wherever you choose; the folder is remembered.
4. With **Paste into Claude Code** ticked, Windows Terminal comes to the front and the prompt already
   holds the request followed by the marked image (`[Image #1]`). Check it, add to it, press Enter.
   Keys are sent only once the terminal is confirmed in front; Claude Code's image paste on Windows
   is Alt+V. If the terminal can't be brought forward, Rhino's command line says so and nothing is typed.
5. On Enter, the hook adds the camera, the strokes and the picks from `meta.json`. A line saying
   "ClaudeMark: attached ..." confirms it. That happens on the next prompt in ANY session, so paste
   into the right tab.

## ClaudeReply: answering Claude's crit

When Claude posts a crit, Rhino's command line says so, and ClaudeMark shows a reminder when it opens.
Type `ClaudeReply`: ClaudeMark opens on the capture with Claude's findings (C1, C2...) as a locked orange
layer. H hides and shows them; the eraser and moves never touch them. Mark and type your answer on top
and send it like any capture; it arrives tagged as a reply, with each of your marks related to the nearest
finding. Cancel leaves the reply waiting; answered replies move to `outbox/answered/`.

If the model changed after the capture, the reply is drawn on a fresh capture from the same camera, so
findings placed on real geometry land on it.

**Options boards** come through ClaudeReply too. Pick Model → "Options board (sketched)", mark where the
options go, and say what for. Claude sketches three different options over your view without modelling
them: light massing drawn in perspective, hidden correctly behind your existing model. ClaudeReply then
shows them with A / B / C chips at the top; the arrow keys or the chips flip between them. Mark your
changes on the one you want and send: the one showing is the one you pick, and only that one gets modelled.

**Render style boards** work the same way. Pick Render → "Style board (3 looks)" and drop the render whose look you
want as a reference. Claude reads its light and grade and renders three real V-Ray previews of your view, about a minute
each. A matches the reference as closely as V-Ray physically can. B and C each change one big thing about the light:
the time, the sky or the haze. Your own V-Ray settings and sun are saved first and put back afterwards. ClaudeReply
shows the three with the reference in the corner. Mark tweaks on the one you want ("warmer", "B's sky") and send.
Claude then sets it up for real:
- the sun (at a real date and time at your site when that's possible, and it says when it isn't), the sky, haze,
  exposure and white balance as V-Ray settings;
- V-Ray's Light Mix, so you can rebalance the lights in the VFB and Transfer to Scene;
- a named view "CM style <name>", a saved `.vropt`, a render, and the colour grade as a `.cube` LUT (VFB LUT layer,
  Photoshop Color Lookup, Premiere).

The finished render matches the preview you picked; on the demo the difference was about the smallest a person can
see (mean dE 2.3).

The marked image is also left on the clipboard (as a plain Windows bitmap) for pasting anywhere else.
The note is cleaned before it is typed: one line, `@` becomes "at", and a leading `/`, `!` or `#` gets
"Note: " in front, so it can't trigger a Claude Code command.

## Files

| Path | What |
|---|---|
| `claude_mark.py` | The Rhino command (IronPython 2.7, run by the `ClaudeMark` alias) |
| `claude_reply.py` | `ClaudeReply`: opens the newest reply in `outbox/` (runs claude_mark.py in reply mode) |
| `outbox/<stamp>-styles/` | A render style board: `option_A.png`..., `reply.json` with `kind: options`, `mode: style` and the paths of the styles and their grades |
| `outbox/<stamp>-reply/` | Claude's crits: `clean.png`, `reply.json` (title, findings as strokes with `by: claude`, the capture's camera), `marked.png`. `answered/` holds the ones the user answered |
| `tools/` | Claude's scripts for the Analyse, Critique and montage jobs; `tools/TOOLS.md` says how to run them |
| `../hook/rhino_markup_inbox.py` | UserPromptSubmit hook that attaches pending captures |
| `inbox/<stamp>/` | Sent from Rhino, not yet attached. `.<stamp>` = still being written |
| `sent/<stamp>/` | Attached to a prompt |
| `stale/<stamp>/` | Older than 6 h when the next prompt came, so not attached |
| `claudemark_settings.json` | The last tool, colour, width and "jump to terminal" choices |

Alias macro: `! _-RunPythonScript "<this folder>\claude_mark.py"`

## meta.json

Everything is recorded at the moment of capture, before the markup window opens. The window is
modal, so the camera cannot move until it closes.

| Key | Content |
|---|---|
| `captured`, `captured_unix` | Local ISO time, epoch seconds |
| `rhino_pid` | The Rhino process. Compare with the Rhino the MCP is driving |
| `doc` | `path`, `name`, `modified`, `units`, `object_count`, `current_layer` |
| `view` | `name`, `viewport_id`, `display_mode`, `size` [w, h], `maximized`, `screen_rect` |
| `image` | `clean.png` is the capture; `marked.png` has the strokes burned in. Both are exactly viewport size |
| `camera` | `projection`, `location`, `target`, `direction`, `up`, `lens_35mm`, `frustum` {left, right, bottom, top, near, far}, `world_to_screen` (4x4, row-major) |
| `cplane` | `origin`, `x`, `y` |
| `selection` | Selected objects: `id`, `type`, `name`, `layer` (full path) |
| `subobject_selection` | Objects with a face or an edge sub-selected, with `components` like `BrepFace#3` |
| `clipping_planes` | Clip planes active in this viewport: `origin`, `normal_kept_side`, `hidden` |
| `history_tail` | The last 15 command-history lines |
| `note` | The note typed in the markup window |
| `strokes` | `kind` (pen / box / arrow / pin / ref / text), `colour`, `rgb`, `width` (px), `points` [[x, y], ...] in image px. Arrows have [tail, tip]; boxes have two opposite corners; pins have `label`; closed pen loops and boxes have `closed: true`. A ref has `label` (R1...), `file` (refN.png in the capture folder, max 2000 px), `source` (original path or URL), `points` [[centre]] and `size` [w, h] of the card in image px. A text note has `text` (lines split by `\n`), `font_px` (text height in image px), `points` [[top-left corner]] and `size` [w, h] of its tag in image px. A photo-match point is `kind: "match"` with `label` (its number), `ref` (the photo it pairs with) and `points` [[x, y]] on the capture; the photo's own points are on its ref as `matches` [[u, v], ...] (fractions of the photo's width and height, in number order), and a dropped photo file adds `original` (refN_original.*, full resolution with EXIF) |
| `picks` | Hits under each stroke: `stroke`, `kind`, `label`, `role` (pin / ref / match / tip / tail / start / end / along / centre / corner / anchor), `px`, `hit` {`id`, `type`, `name`, `layer`, `point`} or null. A text note is picked at its `centre` and its `anchor` (top-left, where the user clicked) |
| `reply_to`, `reply_id`, `reply_title`, `claude_strokes` | Only on an answer to a ClaudeReply: the capture Claude's crit was on, the outbox folder, its title, and Claude's marks (`by: claude`; findings are pins labelled C1, C2...). The camera, view and image size are the original capture's, and picks go through that saved camera |
| `chosen_option`, `options`, `options_mode`, `styles_file`, `styles_previews` | Only on a pick from a board: the option showing when the user sent, all the options, and for a render style board `options_mode: "style"` with the styles file and the folder of previews and grades |

### Pixel to world

Screen point from a world point: `[x, y, _, w] = world_to_screen · [X, Y, Z, 1]`, then `x/w, y/w`.
The camera target maps to the image centre. To go the other way, rebuild the camera in a fresh
`ViewportInfo` (see `notes/RHINO-VRAY-NOTES.md`, section rhino-viewport-camera-traps) or invert the matrix on the near
and far planes.

The picks come from Rhino's own point pick, plus a ray test against the selected objects, which
that pick skips. The nearest render-mesh hit along the view ray wins. Curves, points and blocks
fall back to Rhino's selection point. In Wireframe only edges can be hit. Treat the picks as
hints: verify a pick by projecting the candidate's edges onto `clean.png` before acting on it.
