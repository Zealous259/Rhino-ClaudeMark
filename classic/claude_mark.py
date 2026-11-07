#! python 2
# -*- coding: utf-8 -*-
"""
ClaudeMark - capture the active Rhino view, mark it up, send it to Claude Code.

Run from the ClaudeMark alias:
    ! _-RunPythonScript "<kit folder>\\classic\\claude_mark.py"

Send writes inbox/<stamp>/ with clean.png, marked.png, ref*.png and meta.json. The
UserPromptSubmit hook (hook/rhino_markup_inbox.py) attaches it to the next
prompt typed in Claude Code and moves it to sent/. The schema of meta.json is in README.md.

IronPython 2.7 (the "#! python 2" line): no f-strings; Eto dialogs are subclassed.
"""
import os
import re
import copy
import json
import json.encoder
import math
import time
import shutil
import datetime

# IronPython's str is already unicode, yet its json .decode('utf-8')s any string holding \x80-\xff, so a layer
# name with "·" (\xb7, e.g. "Platform · deck") in the selection or picks killed the send mid meta.json.
# Never take that branch; the escaper then writes · and the file stays ASCII.
json.encoder.HAS_UTF8 = re.compile(r"(?!)")

import clr
import System
import Rhino
import scriptcontext as sc
import Eto.Forms as forms
import Eto.Drawing as drawing

HOME = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile)

def _kit_root():
    """The ClaudeMark folder: where this script is, else the usual places (Desktop\\Rhino-ClaudeMark, then the old
    ~/.claude/rhino-markup)."""
    f = globals().get("__file__")
    if f:
        d = os.path.dirname(os.path.abspath(f))
        if os.path.basename(d).lower() == "classic":
            d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, "claude_mark.py")):
            return d
    desk = System.Environment.GetFolderPath(System.Environment.SpecialFolder.DesktopDirectory)
    for d in (os.path.join(desk, "Rhino-ClaudeMark"), os.path.join(HOME, ".claude", "rhino-markup")):
        if os.path.isfile(os.path.join(d, "claude_mark.py")):
            return d
    return os.path.join(desk, "Rhino-ClaudeMark")


KIT = _kit_root()
ROOT = os.environ.get("CLAUDEMARK_ROOT") or KIT
INBOX = os.path.join(ROOT, "inbox")
SETTINGS_PATH = os.path.join(ROOT, "claudemark_settings.json")

COLOURS = [("red", (240, 68, 56)), ("yellow", (253, 176, 34)),
           ("blue", (46, 144, 250)), ("green", (18, 183, 106))]
WIDTHS = [("Thin", 3), ("Medium", 6), ("Thick", 11)]
TOOLS = [("pen", "Pen  (P)  -  hold Shift for a straight line"),
         ("box", "Box  (B)  -  hold Shift for a square"),
         ("arrow", "Arrow  (A)  -  hold Shift to snap to 15 degrees"),
         ("pin", "Numbered pin  (N)"),
         ("text", "Text  (T)  -  click and type; Enter to finish, Shift+Enter for a new line"),
         ("match", "Photo match  (M)  -  click a reference photo to open it, click a point there, Tab back, "
                   "click the same point on the model"),
         ("eraser", "Eraser  (E)  -  rub out pen lines where you drag; other marks go whole; "
                    "reference cards stay (right-click them)")]
TEXT_PX = {3: 22.0, 6: 30.0, 11: 42.0}   # note text height in image px, by the chosen line width
ERASER_R = {3: 14.0, 6: 28.0, 11: 56.0}  # eraser radius in image px, by the chosen line width
MATCH_RGB = (56, 215, 255)               # photo-match points, the same on the photo and the model
OUTBOX = os.path.join(ROOT, "outbox")    # Claude's replies (crits drawn on the user’s view); ClaudeReply opens them
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")

EXAMPLES_URL = "https://www.google.com/imghp"
EXAMPLES_SPLIT = 0.58   # share of the screen ClaudeMark keeps when Chrome docks beside it

# ---- task sidebar: what kind of job this is, and the presets that say how the user likes it done
PRESETS_DIR = os.path.join(ROOT, "presets")
# Chaos Cosmos downloads: Documents, or OneDrive\Documents when Documents is redirected to OneDrive
COSMOS = next((p for p in (os.path.join(HOME, "Documents", "Chaos Cosmos", "Packages"),
                           os.path.join(HOME, "OneDrive", "Documents", "Chaos Cosmos", "Packages"))
               if os.path.isdir(p)), os.path.join(HOME, "Documents", "Chaos Cosmos", "Packages"))
SIDEBAR_W = 292
CATEGORIES = [("model", "Model", "cube"), ("materials", "Materials", "swatch"),
              ("lighting", "Lighting", "sun"), ("render", "Render", "camera"),
              ("analyse", "Analyse", "chart"), ("critique", "Critique", "crit")]
TOOLKIT = os.path.join(ROOT, "tools")   # Claude's scripts for these jobs; tools/TOOLS.md says how to run them
TASK_TEXT = {
    "model": ("What should Claude do here?  Refer to pins (1, 2) and references (R1).",
              "e.g.  add a window like R1 between pins 1-4, sill at 900"),
    "materials": ("Which surfaces get what?  Mark them, or drop material photos onto them.",
                  "e.g.  timber on the box at pin 1, concrete below, glass in the openings"),
    "lighting": ("Time of day, mood, and where the light comes from.",
                 "e.g.  late afternoon from the west, warm glow from the gallery inside"),
    "render": ("Which view, and what is it for?",
               "e.g.  this view for the A3 board, eye level, people in the yard"),
    "analyse": ("A line for a section, a loop for sun, a pin for what can be seen.",
                "e.g.  section along the line looking north; sun hours on the loop in June"),
    "critique": ("What are you claiming? The critic needs something asserted to attack.",
                 "e.g.  the canopy reads as one gesture from the street"),
}
REPLY_TEXT = ("Answer Claude: agree, push back, or mark what it missed.",
              "e.g.  C2 is wrong, the column sits behind the screen; C4 yes, fix it")
OPTIONS_TEXT = ("Pick an option: flip with the arrow keys or the letters, mark changes on it, send.",
                "e.g.  B, but drop the north end to 4 m and keep A's roof line")
STYLE_TEXT = ("Pick a look: flip with the arrow keys or the letters, mark tweaks on it, send. It gets set up for real.",
              "e.g.  A, but warmer here and with B's sky; less haze")
RENDER_NOTE = ("Set everything up over the MCP, then render it with V-Ray yourself: set /SettingsOutput "
               "img_dir + img_file, call rhVRay.Render, wait for the file, then clear the path (notes/RHINO-VRAY-NOTES.md section "
               "vray-render-over-mcp). It renders the active viewport, so restore the named view first. Save "
               "where this preset says, otherwise in a Renders folder beside the .3dm. If the user’s VFB colour "
               "filters are on, the .denoiser.png is the clean image.")
SITE = "the site (the model's Rhino sun location; ask for it if that isn't set)"

BUILTIN_PRESETS = [
    # model
    {"slug": "nurbs-keep-originals", "category": "model", "name": "NURBS, keep originals",
     "summary": "Breps and extrusions, never meshes; hidden backup of anything edited",
     "instructions": "Build new geometry as NURBS breps or extrusions, never meshes. Before changing any of the user’s "
                     "objects, copy them to a hidden backup layer. Name and layer everything new.",
     "swatch": [214, 216, 220]},
    {"slug": "real-product-sizes", "category": "model", "name": "Real product sizes",
     "summary": "Read the product or precedent first; model at true size",
     "instructions": "If a product or precedent is named or linked, read its spec and photos first and model at "
                     "true size. Say what scale you assumed if the document units disagree.",
     "swatch": [120, 160, 200]},
    # materials
    {"slug": "match-the-references", "category": "materials", "name": "Match the references",
     "summary": "Closest Cosmos material to each R1, R2... on the marked surfaces",
     "instructions": "For each reference image on the capture, pick the closest Chaos Cosmos material already "
                     "downloaded (" + os.path.join(COSMOS, "Materials") + "), import it with "
                     "-_vrayImportMaterial and assign it with obj.RenderMaterial to the objects under or inside "
                     "the matching marks, or the selection. If nothing is close, build a V-Ray material from the "
                     "photo's colour and roughness. List what went where.",
     "icon": "image"},
    {"slug": "clay-model", "category": "materials", "name": "Clay model",
     "summary": "Matte white everything, light grey glass; the user’s materials untouched",
     "instructions": "Clay render: prefer V-Ray's material override (matte white, albedo about 0.8, no "
                     "reflection) with glass excluded and given a light grey translucent material, so the user’s own "
                     "materials stay assigned. If the override can't be scripted, tell the user the one toggle.",
     "swatch": [236, 236, 232]},
    {"slug": "timber-oak", "category": "materials", "name": "Timber: oak, semigloss",
     "summary": "Cosmos Oak Northland semigloss, grain along each member",
     "instructions": "Import the Cosmos oak below and apply it to the marked or selected objects. Run the grain "
                     "along each member's long axis (box mapping, Z up for vertical members).",
     "cosmos": "Materials/Wood/Oak_Northland_Semigloss_300cm"},
    {"slug": "maquette-wood", "category": "materials", "name": "Maquette (model wood)",
     "summary": "Cosmos model-making wood, for physical-model style shots",
     "instructions": "Apply the Cosmos maquette wood below to the marked or selected objects (or everything but "
                     "glass if nothing is marked), for a physical-model look.",
     "cosmos": "Materials/Wood/Maquette_Wood_A01_12cm"},
    {"slug": "glass-clear", "category": "materials", "name": "Glass: clear",
     "summary": "Cosmos clear glass for all glazing",
     "instructions": "Apply the Cosmos clear glass below to all glazing (objects named or layered as glass, or "
                     "the marked panes).",
     "cosmos": "Materials/Glass/Glass_Clear_001"},
    {"slug": "concrete-smooth", "category": "materials", "name": "Concrete: smooth",
     "summary": "Cosmos plain concrete, 2 m tile",
     "instructions": "Apply the Cosmos concrete below to the marked or selected objects, real-world scale.",
     "cosmos": "Materials/Concrete/Concrete_Simple_A01_200cm"},
    {"slug": "metal-brushed", "category": "materials", "name": "Metal: brushed",
     "summary": "Cosmos brushed metal for frames and trims",
     "instructions": "Apply the Cosmos brushed metal below to the marked or selected frames, trims and fixings.",
     "cosmos": "Materials/Metal/Metal_Brushed_26-94_100cm"},
    # lighting
    {"slug": "golden-hour", "category": "lighting", "name": "Golden hour",
     "summary": "Low warm sun on a summer evening, long shadows",
     "instructions": "V-Ray Sun + Sky at " + SITE + ". Sun about 8-10 degrees up on a real summer evening there "
                     "(find the date and time that give it, and say which). Warm, long "
                     "shadows; expose so the sky keeps its colour. Soft warm fill inside if there are interiors.",
     "gradient": [[255, 170, 90], [236, 104, 120]]},
    {"slug": "midday-summer", "category": "lighting", "name": "Midday, summer",
     "summary": "High crisp sun, short shadows, clear sky",
     "instructions": "V-Ray Sun + Sky at " + SITE + " on the summer solstice at about 1:00 pm local time "
                     "(21 December south of the equator, 21 June north). Crisp, short shadows, clear blue sky.",
     "gradient": [[120, 180, 245], [235, 244, 255]]},
    {"slug": "overcast", "category": "lighting", "name": "Overcast",
     "summary": "Soft dome light, no hard shadows",
     "instructions": "Soft overcast light: a dome light with an overcast HDRI from " +
                     os.path.join(COSMOS, "HDRIs", "Day") + " (check their Previews to pick one), sun off, low "
                     "contrast, no hard shadows.",
     "gradient": [[150, 158, 168], [220, 224, 228]]},
    {"slug": "sunset-hdri", "category": "lighting", "name": "Sunset HDRI",
     "summary": "Cosmos Sunset 016 dome light",
     "instructions": "Dome light with the Cosmos sunset HDRI below (its _light.vrmat imports as a ready dome "
                     "light). Rotate it so the sun sits behind and to the left of the camera; expose for the "
                     "facade.",
     "cosmos": "HDRIs/Evening/Sunset_016"},
    {"slug": "blue-hour-interiors", "category": "lighting", "name": "Blue hour, lit inside",
     "summary": "Twilight sky, warm glowing interiors",
     "instructions": "Sun just below the horizon for a blue twilight sky. Light the occupied rooms warm "
                     "(3000 K): build Rhino rectangular lights, then _-vrayLight _Convert (notes/RHINO-VRAY-NOTES.md section "
                     "vray-lights-by-script). Glazing should glow; exterior slightly underexposed.",
     "gradient": [[32, 52, 110], [250, 170, 90]]},
    {"slug": "studio-white", "category": "lighting", "name": "Studio white",
     "summary": "No sun; soft white dome for clay and model shots",
     "instructions": "No sun. A soft white dome light (or a studio HDRI) and a ground plane that catches soft "
                     "contact shadows. Pairs with Clay model.",
     "gradient": [[255, 255, 255], [200, 202, 206]]},
    # render
    {"slug": "quick-preview", "category": "render", "name": "Quick preview",
     "summary": "1280 x 720, about a minute, denoised",
     "instructions": "V-Ray output 1280 x 720, progressive with about a 60 s limit, denoiser on. " + RENDER_NOTE,
     "icon": "camera"},
    {"slug": "final-4k", "category": "render", "name": "Final 4K, 16:9",
     "summary": "3840 x 2160, clean, saved to this week's folder",
     "instructions": "V-Ray output 3840 x 2160, noise threshold 0.01, denoiser on; save to the current week's "
                     "folder with a dated name. " + RENDER_NOTE,
     "icon": "camera"},
    {"slug": "a3-board", "category": "render", "name": "A3 board, 300 dpi",
     "summary": "4961 x 3508 landscape for print",
     "instructions": "V-Ray output 4961 x 3508 (A3 landscape at 300 dpi), noise threshold 0.01. " + RENDER_NOTE,
     "icon": "camera"},
    {"slug": "two-point-eye-level", "category": "render", "name": "Two-point, eye level",
     "summary": "1.6 m eye height, verticals straight, lens about 24 mm",
     "instructions": "Camera: two-point perspective at 1.6 m eye height, lens about 24 mm (the user’s usual), "
                     "verticals vertical, saved as a named view. Keep the user’s framing from the capture unless "
                     "told otherwise.",
     "icon": "camera"},
    {"slug": "axonometric", "category": "render", "name": "Axonometric",
     "summary": "Parallel projection from above, saved as a named view",
     "instructions": "Camera: parallel projection looking down from the corner that best shows the marked "
                     "area, saved as a named view, white background.",
     "icon": "cube"},
    {"slug": "cosmos-people", "category": "render", "name": "Add Cosmos people",
     "summary": "The user’s downloaded Cosmos people; pins = where, arrows = heading",
     "instructions": "Populate the view with people from the user’s downloaded Chaos Cosmos library (" +
                     os.path.join(COSMOS, "3D_Models", "People") + ": walking, standing and sitting, singles "
                     "and pairs; check their Previews to pick). Each pin is one person or pair, an arrow is a "
                     "walker heading from tail to tip, and a loop or box is an area to populate. With no marks, "
                     "put a few where the camera sees use: walkers on routes, standers at edges and entries, "
                     "sitters on seating. Place with -_vrayCosmos 1 <package uuid> and w-prefixed points so they "
                     "arrive at true size, then Esc out of the placing loop (notes/RHINO-VRAY-NOTES.md section "
                     "vray-cosmos-proxies-scripting). Stand each on the surface below it, check heights read "
                     "1.5-1.9 m, keep them clear of geometry, turn them to their heading or towards each other, "
                     "and don't repeat a person close by. Layer \"People (Cosmos)\". Use downloaded assets only; "
                     "ask before downloading more (name and size first).",
     "cosmos_thumb": "3D_Models/People/Johnny_and_Elizabeth_Walking_001",
     "icon": "image"},
    {"slug": "photo-montage", "category": "render", "name": "Photo montage",
     "summary": "Match a site photo; render the design into it",
     "instructions": "Montage the design into the site photo. The user drops the photo as a reference, double-clicks it, "
                     "and places Photo match points (M) on the photo and then the same points on the model (6 or "
                     "more, spread out, not all on one plane). The objects selected at capture are the proposal; "
                     "if none, ask which. Snap the model points and solve the photo's camera "
                     "(tools/cm_photomatch.py), then show the user the model edges overlaid on the photo before "
                     "rendering. Set the sun from the photo's EXIF date and time at the site, render the proposal "
                     "alone in V-Ray from the solved camera (notes/RHINO-VRAY-NOTES.md section vray-render-over-mcp) and composite it "
                     "over the photo (tools/cm_post.py montage). Report the reprojection error per point.",
     "icon": "image"},
    {"slug": "style-board", "category": "render", "name": "Style board (3 looks)",
     "summary": "Three V-Ray looks from a reference render; pick one and it's set up for real",
     "instructions": "Make a style board from the reference the user dropped (R1; if none, ask for one): follow 'Render "
                     "styles' in tools/TOOLS.md. Read the reference (cm_style.py read) and look at it yourself, then "
                     "write three styles that differ in principle: A matches the reference as closely as V-Ray "
                     "physically can, B and C each change one big thing about the light (time, sky, haze), not "
                     "just the grade. Snapshot the user’s settings (style_begin), render a real V-Ray preview of each from "
                     "this capture's camera (about a minute each), fit A's grade to the reference, build and post "
                     "the board, restore the user’s settings, and tell the user to type ClaudeReply to pick. Say plainly what "
                     "the reference does that V-Ray can't (a sky colour, an impossible sun at the user’s site).",
     "icon": "image"},
    # analyse - the mark is the question; tools/cm_analyse.py does the work, tools/cm_post.py draws it
    {"slug": "section-line", "category": "analyse", "name": "Section along the line",
     "summary": "Draw a line across the view; get a cut section",
     "instructions": "Cut a vertical section along the marked line (the first straight pen line or arrow). It "
                     "looks away from the camera unless the user’s note says otherwise. Run section() in "
                     "tools/cm_analyse.py, then tools/cm_post.py section: poche cut, linework beyond graded by "
                     "depth (notes/RHINO-VRAY-NOTES.md section section-perspective-pipeline), as SVG and PNG, plus the cut line drawn "
                     "on the user’s view.",
     "icon": "chart"},
    {"slug": "sun-hours", "category": "analyse", "name": "Sun hours on the marked area",
     "summary": "Loop or box surfaces; direct sun on 21 Jun and 21 Dec",
     "instructions": "Hours of direct sun on the surfaces inside each loop or box, from the user’s camera's view of "
                     "them, on 21 June and 21 December at the site (the model's Rhino sun location; ask if it isn't set). "
                     "Run sun_hours() in tools/cm_analyse.py (chunked, it can be slow), then tools/cm_post.py "
                     "sun: a heatmap over the user’s view with a legend, one panel per date, and the area-weighted mean.",
     "icon": "sun"},
    {"slug": "shadow-study", "category": "analyse", "name": "Shadow study, hourly",
     "summary": "The user’s view with the sun stepped hourly, three dates",
     "instructions": "Shadow study from the user’s exact capture camera: Rendered captures with the sun stepped hourly "
                     "from 8:00 to 18:00 on 21 June, 21 March and 21 December at the site. Run shadow_study() in "
                     "tools/cm_analyse.py, then tools/cm_post.py shadows for the labelled contact sheet. Restore "
                     "the user’s sun and view afterwards.",
     "icon": "sun"},
    {"slug": "isovist", "category": "analyse", "name": "Isovist from the pin",
     "summary": "What can be seen from each pin at 1.6 m, in plan",
     "instructions": "For each pin: the isovist at 1.6 m eye height from where the pin lands, by 720 horizontal "
                     "rays against the visible model. Run isovist() in tools/cm_analyse.py, then "
                     "tools/cm_post.py isovist: a plan with the obstacles cut at eye height, the visible field "
                     "and its area, plus the field drawn on the user’s view.",
     "icon": "chart"},
    {"slug": "eye-level", "category": "analyse", "name": "Eye-level view from the pin",
     "summary": "A 1.6 m named view from each pin",
     "instructions": "For each pin: a two-point named view at 1.6 m eye height, lens 24 mm, looking along the "
                     "arrow if there is one, else toward the next pin, else the capture's own direction. Run "
                     "eye_level() in tools/cm_analyse.py; it saves the named views and a Rendered capture of each, "
                     "and puts the user’s view back.",
     "icon": "camera"},
    # critique - drawn back onto the user’s view as a ClaudeMark reply (tools/cm_reply.py, ClaudeReply in Rhino)
    {"slug": "critic", "category": "critique", "name": "Critic: what's wrong with this",
     "summary": "The critic agent attacks the claim; marks come back on the view",
     "instructions": "Run the critic agent on this capture: marked.png, meta.json and the user’s note as the claim. If "
                     "the note asserts nothing, ask the user what the user is claiming first. Post its findings as a "
                     "ClaudeMark reply with post_reply() in tools/cm_reply.py: one numbered mark (C1, C2...) on "
                     "the element each finding is about, with a short label, and the full argument in chat under "
                     "the same numbers. Then tell the user to type ClaudeReply in Rhino.",
     "icon": "crit"},
    {"slug": "council", "category": "critique", "name": "Architectural council",
     "summary": "Several disciplines review it; marks come back on the view",
     "instructions": "Run the architectural-council skill (Skill tool: architectural-council:architectural-council) "
                     "on this capture (marked.png, meta.json, the user’s note), then "
                     "post the points it lands on as a ClaudeMark reply with post_reply() in tools/cm_reply.py, "
                     "numbered C1, C2... on the elements, the argument in chat under the same numbers. Tell the user to "
                     "type ClaudeReply in Rhino.",
     "icon": "crit"},
    {"slug": "brief", "category": "critique", "name": "Against the brief",
     "summary": "The project brief, tested on the marked design",
     "instructions": "Test the marked design against the project brief (from CLAUDE.md or memory; if you don't "
                     "have it, ask for it first): what does the brief ask for, where does the design deliver it, "
                     "and where do they part? Post it as a ClaudeMark reply with "
                     "post_reply() in tools/cm_reply.py, numbered on the elements, the argument in chat. Tell the user "
                     "to type ClaudeReply in Rhino.",
     "icon": "crit"},
    {"slug": "product-council", "category": "critique", "name": "Product council",
     "summary": "Seven tool and product reviewers; FMEA and kill tests",
     "instructions": "Run the product-council skill (Skill tool: product-council:product-council) on the tool, "
                     "product or end-effector in this capture. Build its brief from marked.png, meta.json, the user’s note "
                     "and any memory or project notes about the tool, listing what isn't known "
                     "as unknown, and confirm the brief with the user before the council sits. If the user’s note says quick, run "
                     "quick mode. Give the full report in chat and save it as the skill says. Then post the critical "
                     "and major issues as a ClaudeMark reply with post_reply() in tools/cm_reply.py, numbered C1, "
                     "C2... on the parts they concern, and tell the user to type ClaudeReply in Rhino.",
     "icon": "crit"},
    {"slug": "options-board", "category": "model", "name": "Options board (sketched)",
     "summary": "Three different options sketched on this view, not modelled",
     "instructions": "Propose three genuinely different options for what the user marked: different principles, not "
                     "variants of one, each with a one-line idea. Do NOT model them in Rhino. Write each as a few "
                     "massing primitives in world coordinates (schema in tools/cm_sketch.py), anchored on the user’s marks "
                     "with pick()/snap() from tools/cm_core.py; keep each option to what conveys its idea. Take one "
                     "depth snapshot (depth_from_meta in tools/cm_core.py), render with python tools/cm_sketch.py "
                     "render <capture> <options.json> <out> --depth <depth.bin>, look at the board yourself, then "
                     "post it (cm_sketch.py post) and tell the user to type ClaudeReply in Rhino to flip through and pick "
                     "one. Model only the option the user picks, with the user’s marked changes, and only after the user picks.",
     "icon": "cube"},
    {"slug": "engineering-council", "category": "critique", "name": "Engineering council",
     "summary": "Ten engineers across five fields; knock-on effects and diagrams",
     "instructions": "Run the engineering-council skill (Skill tool: engineering-council:engineering-council) on "
                     "this capture: build its brief from marked.png, meta.json, the user’s note and any memory or project "
                     "notes about the project, describe what the image shows, list what isn't known as unknown, and "
                     "confirm the brief with the user before the council sits. pandoc and wkhtmltopdf aren't installed, so "
                     "keep the council's Markdown report; if the user wants a PDF, print it with headless Edge instead "
                     "(notes/RHINO-VRAY-NOTES.md section section-perspective-pipeline). Give the report in chat, then post the critical "
                     "and major issues as a ClaudeMark reply with post_reply() in tools/cm_reply.py, numbered C1, "
                     "C2... on the elements they concern, and tell the user to type ClaudeReply in Rhino.",
     "icon": "crit"},
]

MAX_PICKS = 60          # Rhino picks per send, across all strokes
PICK_BUDGET_S = 3.0     # stop picking after this long; the camera is saved anyway
SELECTION_LIMIT = 200


# ---------------------------------------------------------------- theme

def rgb(hex_, a=255):
    hex_ = hex_.lstrip("#")
    return drawing.Color.FromArgb(int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16), a)


BG = rgb("#151618")
PANEL = rgb("#1E1F22")
PANEL_HI = rgb("#2A2C30")
HOVER = rgb("#33363B")
LINE = rgb("#3A3D42")
TEXT = rgb("#ECEDEF")
MUTED = rgb("#8E9197")
ACCENT = rgb("#D97757")
ACCENT_HI = rgb("#E88A6B")
ACCENT_SOFT = rgb("#D97757", 46)
WHITE = drawing.Colors.White

FONT_UI = "Segoe UI Variable Text"
FONT_HEAD = "Segoe UI Variable Display"


def font(size, bold=False, family=FONT_UI):
    style = drawing.FontStyle.Bold if bold else drawing.FontStyle.None
    try:
        return drawing.Font(family, size, style)
    except Exception:
        return drawing.Font(drawing.SystemFont.Bold if bold else drawing.SystemFont.Default, size)


def round_rect(x, y, w, h, r):
    return drawing.GraphicsPath.GetRoundRect(drawing.RectangleF(x, y, w, h), r)


def pen(col, w, cap=True):
    p = drawing.Pen(col, w)
    if cap:
        p.LineCap = drawing.PenLineCap.Round
        p.LineJoin = drawing.PenLineJoin.Round
    return p


def text_centered(g, f, col, cx, cy, s):
    size = g.MeasureString(f, s)
    g.DrawText(f, drawing.SolidBrush(col), cx - size.Width / 2.0, cy - size.Height / 2.0, s)


# ---------------------------------------------------------------- small helpers

def _r(v, nd=4):
    return round(float(v), nd)


def _pt(p):
    return [_r(p.X), _r(p.Y), _r(p.Z)]


def load_settings():
    s = {"tool": "pen", "colour": 0, "width": 1, "jump": True, "save_dir": ""}
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except Exception:
        pass
    if s["tool"] not in [t for t, _ in TOOLS]:
        s["tool"] = "pen"
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


def layer_path(doc, obj):
    try:
        return doc.Layers[obj.Attributes.LayerIndex].FullPath
    except Exception:
        return ""


def obj_record(doc, obj):
    return {"id": str(obj.Id), "type": str(obj.ObjectType),
            "name": obj.Attributes.Name or "", "layer": layer_path(doc, obj)}


# ---------------------------------------------------------------- capture-time record

def camera_record(vp):
    cam = {
        "projection": "parallel" if vp.IsParallelProjection else "perspective",
        "location": _pt(vp.CameraLocation),
        "target": _pt(vp.CameraTarget),
        "direction": _pt(vp.CameraDirection),
        "up": _pt(vp.CameraUp),
        "lens_35mm": _r(vp.Camera35mmLensLength, 3),
        "two_point": bool(vp.IsTwoPointPerspectiveProjection),   # a shifted frustum V-Ray only honours on 2-pt views
    }
    res = vp.GetFrustum()
    if res[0]:
        cam["frustum"] = dict(zip(["left", "right", "bottom", "top", "near", "far"],
                                  [_r(v, 6) for v in res[1:7]]))
    xf = vp.GetTransform(Rhino.DocObjects.CoordinateSystem.World,
                         Rhino.DocObjects.CoordinateSystem.Screen)
    cam["world_to_screen"] = [[float(xf[i, j]) for j in range(4)] for i in range(4)]
    return cam


def selection_record(doc):
    sel = []
    for obj in doc.Objects.GetSelectedObjects(False, False):
        if len(sel) >= SELECTION_LIMIT:
            break
        sel.append(obj_record(doc, obj))
    # Sub-object selections (a face or an edge) don't show up in GetSelectedObjects.
    sub = []
    t0 = time.time()
    for obj in doc.Objects:
        if time.time() - t0 > 0.5:
            break
        if obj.IsSelected(True) == 3:
            rec = obj_record(doc, obj)
            rec["components"] = ["%s#%d" % (ci.ComponentIndexType, ci.Index)
                                 for ci in obj.GetSelectedSubObjects()]
            sub.append(rec)
    return sel, sub


def clipping_planes_record(doc, vp):
    out = []
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.ObjectTypeFilter = Rhino.DocObjects.ObjectType.ClipPlane
    es.HiddenObjects = True
    for obj in doc.Objects.GetObjectList(es):
        try:
            geo = obj.ClippingPlaneGeometry
            if vp.Id not in list(geo.ViewportIds()):
                continue
            pl = geo.Plane
            out.append({"id": str(obj.Id), "hidden": obj.IsHidden,
                        "origin": _pt(pl.Origin), "normal_kept_side": _pt(pl.ZAxis)})
        except Exception:
            pass
    return out


def history_tail(n=15):
    try:
        lines = [l for l in Rhino.RhinoApp.CommandHistoryWindowText.splitlines() if l.strip()]
        return lines[-n:]
    except Exception:
        return []


def gather_meta(doc, view, w, h):
    vp = view.ActiveViewport
    sel, sub = selection_record(doc)
    rect = view.ScreenRectangle
    return {
        "tool": "ClaudeMark",
        "version": 3,
        "captured": datetime.datetime.now().isoformat(),
        "captured_unix": time.time(),
        "rhino_pid": System.Diagnostics.Process.GetCurrentProcess().Id,
        "doc": {
            "path": doc.Path or "",
            "name": doc.Name or "Untitled",
            "modified": doc.Modified,
            "units": str(doc.ModelUnitSystem),
            "object_count": doc.Objects.Count,
            "current_layer": doc.Layers.CurrentLayer.FullPath,
        },
        "view": {
            "name": vp.Name,
            "viewport_id": str(vp.Id),
            "display_mode": vp.DisplayMode.EnglishName,
            "size": [w, h],
            "maximized": view.Maximized,
            "screen_rect": [rect.X, rect.Y, rect.Width, rect.Height],
        },
        "image": {"clean": "clean.png", "marked": "marked.png",
                  "pixels": "image px = viewport px, origin top-left, y down"},
        "camera": camera_record(vp),
        "cplane": {"origin": _pt(vp.ConstructionPlane().Origin),
                   "x": _pt(vp.ConstructionPlane().XAxis),
                   "y": _pt(vp.ConstructionPlane().YAxis)},
        "selection": sel,
        "subobject_selection": sub,
        "clipping_planes": clipping_planes_record(doc, vp),
        "history_tail": history_tail(),
    }


# ---------------------------------------------------------------- what is under a stroke

def pick_at(doc, view, x, y, selected=()):
    """The nearest visible object under viewport pixel (x, y), or None.

    Rhino's own pick finds candidates, but it skips objects that are already selected, so
    those are passed in and ray-tested too. A ray from the near plane decides which one is
    in front. GetFrustumLine runs far -> near, so the ray goes To -> From.
    """
    vp = view.ActiveViewport
    xi, yi = int(round(x)), int(round(y))
    pc = Rhino.Input.Custom.PickContext()
    pc.View = view
    pc.PickStyle = Rhino.Input.Custom.PickStyle.PointPick
    pc.PickMode = Rhino.Input.Custom.PickMode.Shaded
    pc.SetPickTransform(vp.GetPickTransform(xi, yi))
    pc.UpdateClippingPlanes()
    refs = doc.Objects.PickObjects(pc) or []
    candidates = [(ref.Object(), ref) for ref in refs] + [(obj, None) for obj in selected]
    if not candidates:
        return None
    ok, line = vp.GetFrustumLine(float(xi), float(yi))
    if not ok:
        return None
    origin = line.To
    d = line.From - line.To
    d.Unitize()
    ray = Rhino.Geometry.Ray3d(origin, d)
    best = None
    for obj, ref in candidates:
        if obj is None:
            continue
        t = None
        meshes = obj.GetMeshes(Rhino.Geometry.MeshType.Render) or []
        for m in meshes:
            tm = Rhino.Geometry.Intersect.Intersection.MeshRay(m, ray)
            if tm >= 0 and (t is None or tm < t):
                t = tm
        if t is None:
            # Curves, points, blocks: fall back to Rhino's own selection point.
            if ref is None:
                continue
            sp = ref.SelectionPoint()
            if not sp.IsValid:
                continue
            t = (sp - origin) * d
        if best is None or t < best[0]:
            best = (t, obj)
    if best is None:
        return None
    t, obj = best
    hit = obj_record(doc, obj)
    hit["point"] = _pt(origin + d * t)
    return hit


def stroke_samples(st):
    """Pixels worth picking for a stroke: its meaningful points, plus a few along it."""
    pts = st["points"]
    kind = st["kind"]
    if kind == "pin":
        return [("pin", pts[0])]
    if kind == "ref":
        return [("ref", pts[0])]
    if kind == "match":
        return [("match", pts[0])]
    if kind == "text":
        (x, y), (tw, th) = pts[0], st.get("size") or [0, 0]
        return [("centre", [x + tw / 2.0, y + th / 2.0]), ("anchor", pts[0])]
    if kind == "arrow":
        return [("tip", pts[-1]), ("tail", pts[0])]
    if kind == "box":
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        st["closed"] = True
        return [("centre", [(x0 + x1) / 2.0, (y0 + y1) / 2.0]), ("corner", [x0, y0]), ("corner", [x1, y0]),
                ("corner", [x1, y1]), ("corner", [x0, y1])]
    out = [("start", pts[0])]
    if len(pts) > 1:
        out.append(("end", pts[-1]))
        n = min(6, len(pts) - 2)
        for k in range(1, n + 1):
            out.append(("along", pts[int(k * (len(pts) - 1) / float(n + 1))]))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        gap = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if diag > 20 and gap < 0.25 * diag:
            st["closed"] = True
            out.append(("centre", [sum(xs) / len(xs), sum(ys) / len(ys)]))
    return out


def pick_with_camera(doc, camera, x, y):
    """Like pick_at, but through a saved camera (its world_to_screen matrix) instead of the live view,
    so a reply opened later still picks what was under the mark when the capture was taken."""
    M = Rhino.Geometry.Transform(1.0)
    for i in range(4):
        for j in range(4):
            M[i, j] = camera["world_to_screen"][i][j]
    ok, Mi = M.TryGetInverse()
    if not ok:
        return None

    def unproject(z):
        v = [sum(Mi[i, j] * c for j, c in enumerate((x, y, z, 1.0))) for i in range(4)]
        return Rhino.Geometry.Point3d(v[0] / v[3], v[1] / v[3], v[2] / v[3])
    a, b = unproject(0.0), unproject(1.0)
    d = b - a
    d.Unitize()
    loc = Rhino.Geometry.Point3d(*camera["location"])
    origin = a + d * ((loc - a) * d) if camera.get("projection") == "parallel" else loc
    ray = Rhino.Geometry.Ray3d(origin, d)
    line = Rhino.Geometry.Line(origin, origin + d * 1e7)
    best = None
    es = Rhino.DocObjects.ObjectEnumeratorSettings()
    es.VisibleFilter = True
    es.HiddenObjects = False
    for obj in doc.Objects.GetObjectList(es):
        ok, _ = Rhino.Geometry.Intersect.Intersection.LineBox(line, obj.Geometry.GetBoundingBox(True), 0.0)
        if not ok:
            continue
        for m in obj.GetMeshes(Rhino.Geometry.MeshType.Render) or []:
            t = Rhino.Geometry.Intersect.Intersection.MeshRay(m, ray)
            if t >= 0 and (best is None or t < best[0]):
                best = (t, obj)
    if best is None:
        return None
    hit = obj_record(doc, best[1])
    hit["point"] = _pt(ray.PointAt(best[0]))
    return hit


def pick_hints(doc, view, strokes, camera=None):
    hints = []
    t0 = time.time()
    count = 0
    selected = list(doc.Objects.GetSelectedObjects(False, False))[:SELECTION_LIMIT]
    for i, st in enumerate(strokes):
        for role, p in stroke_samples(st):
            if count >= MAX_PICKS or time.time() - t0 > PICK_BUDGET_S:
                return hints
            count += 1
            try:
                hit = pick_with_camera(doc, camera, p[0], p[1]) if camera else pick_at(doc, view, p[0], p[1], selected)
            except Exception as ex:
                hit = {"error": str(ex)}
            hints.append({"stroke": i, "kind": st["kind"], "label": st.get("label"),
                          "role": role, "px": [_r(p[0], 1), _r(p[1], 1)], "hit": hit})
    return hints


# ---------------------------------------------------------------- reference images

def load_reference(source, work, n):
    """Copy a dropped or pasted image into the capture folder as refN.png. Returns (path, bitmap).

    `source` is a local path, an http(s) URL, a data: URI or an Eto image. Big images are scaled
    to 2000 px. A URL that turns out to be a web page raises, so the caller can try the next one.
    """
    return load_image(source, os.path.join(work, "ref%d.png" % n))


def load_image(source, out):
    """Decode an image source and save it as a PNG at `out`. Returns (out, bitmap)."""
    # Downloads and data: URIs are decoded from memory, so a failed decode leaves no file behind.
    if isinstance(source, basestring) and source.lower().startswith("data:image"):
        img = drawing.Bitmap(System.IO.MemoryStream(System.Convert.FromBase64String(source.split(",", 1)[1])))
    elif isinstance(source, basestring) and source.lower().startswith(("http://", "https://")):
        clr.AddReference("System.Net.Http")
        from System.Net.Http import HttpClient
        client = HttpClient()
        client.Timeout = System.TimeSpan.FromSeconds(20)
        # Image hosts often refuse requests that don't look like the browser the image came from.
        client.DefaultRequestHeaders.TryAddWithoutValidation(
            "User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0.0.0 Safari/537.36")
        client.DefaultRequestHeaders.TryAddWithoutValidation(
            "Accept", "image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
        img = drawing.Bitmap(System.IO.MemoryStream(client.GetByteArrayAsync(source).Result))
    elif isinstance(source, basestring):
        img = drawing.Bitmap(source)
    else:
        img = source
    w, h = img.Size.Width, img.Size.Height
    k = min(1.0, 2000.0 / max(w, h))
    bmp = drawing.Bitmap(int(w * k), int(h * k), drawing.PixelFormat.Format32bppRgba)
    g = drawing.Graphics(bmp)
    try:
        g.ImageInterpolation = drawing.ImageInterpolation.High
        g.DrawImage(img, 0.0, 0.0, float(bmp.Size.Width), float(bmp.Size.Height))
    finally:
        g.Dispose()
    bmp.Save(out, drawing.ImageFormat.Png)
    return out, bmp


def _imgurl(url):
    """Google Images result links carry the real image as ?imgurl=...; return it, or None."""
    import urllib
    if "imgurl=" not in url:
        return None
    return urllib.unquote(url.split("imgurl=", 1)[1].split("&", 1)[0])


def dropped_sources(data):
    """Images in an Eto DataObject, as (files, alternatives).

    Each file is its own reference. The alternatives are different encodings of ONE dragged
    browser image, best first: the pixels themselves, the <img src> (often a data: URI on Google
    Images), the image URL inside a Google result link, then any other URL. Try them in order.
    """
    import re
    files, alts = [], []
    try:
        if data.ContainsImage and data.Image is not None:
            alts.append(data.Image)
    except Exception:
        pass
    try:
        html = data.Html if data.ContainsHtml else ""
        for src in re.findall(r'<img[^>]+src="((?:https?://|data:image)[^"]+)"', html or ""):
            alts.append(src.replace("&amp;", "&"))
    except Exception:
        pass
    try:
        if data.ContainsUris:
            for u in data.Uris:
                if u.IsFile:
                    if u.LocalPath.lower().endswith(IMAGE_EXTS):
                        files.append(u.LocalPath)
                elif u.Scheme in ("http", "https"):
                    real = _imgurl(u.AbsoluteUri)
                    if real:
                        alts.append(real)
                    alts.append(u.AbsoluteUri)
    except Exception:
        pass
    try:
        text = (data.Text or "").strip() if data.ContainsText else ""
        if text.lower().startswith(("http://", "https://")):
            alts.append(text.split()[0])
    except Exception:
        pass
    seen, unique = set(), []
    for a in alts:
        key = a if isinstance(a, basestring) else id(a)
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return files, unique


# ---------------------------------------------------------------- docking Chrome beside the window

def _win32():
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    u = ctypes.windll.user32
    u.FindWindowExW.restype = ctypes.c_void_p
    u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    return ctypes, u, RECT


def find_chrome():
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        p = os.path.join(os.environ.get(key, ""), "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(p):
            return p
    return None


def chrome_windows():
    """Visible, titled top-level windows belonging to chrome.exe (Spotify and Edge share the class)."""
    ctypes, u, RECT = _win32()
    from ctypes import wintypes
    out = []
    h = u.FindWindowExW(None, None, u"Chrome_WidgetWin_1", None)
    while h:
        hv = ctypes.c_void_p(h)
        if u.IsWindowVisible(hv) and u.GetWindowTextLengthW(hv) > 0:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hv, ctypes.byref(pid))
            try:
                if System.Diagnostics.Process.GetProcessById(pid.value).ProcessName.lower() == "chrome":
                    out.append(h)
            except Exception:
                pass
        h = u.FindWindowExW(None, hv, u"Chrome_WidgetWin_1", None)
    return out


def place_window(hwnd, x, y, w, h):
    """Move a top-level window so its VISIBLE frame fills (x, y, w, h).

    Windows 10/11 pad every window with an invisible resize border; DWM's extended frame bounds
    give the visible edge, so the difference is added back on each side.
    """
    ctypes, u, RECT = _win32()
    hv = ctypes.c_void_p(hwnd)
    u.ShowWindow(hv, 9)  # SW_RESTORE: a maximised window ignores SetWindowPos
    outer, frame = RECT(), RECT()
    u.GetWindowRect(hv, ctypes.byref(outer))
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hv, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
        frame = outer
    l, t = frame.left - outer.left, frame.top - outer.top
    r, b = outer.right - frame.right, outer.bottom - frame.bottom
    u.SetWindowPos(hv, None, int(x - l), int(y - t), int(w + l + r), int(h + t + b), 0x0004 | 0x0040)


def window_alive(hwnd):
    if not hwnd:
        return False
    ctypes, u, RECT = _win32()
    return bool(u.IsWindow(ctypes.c_void_p(hwnd))) and bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))


# ---------------------------------------------------------------- drawing (screen and file)

HALO = drawing.Color.FromArgb(0, 0, 0, 80)


def _colour(st):
    r, g, b = st["rgb"]
    return drawing.Color.FromArgb(r, g, b)


def _arrow_shape(a, b, w, s):
    dx, dy = b.X - a.X, b.Y - a.Y
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    head = min(max(4.0 * w, 14.0 * s), 0.6 * length)
    bx, by = b.X - ux * head, b.Y - uy * head
    half = 0.55 * head
    tri = [b, drawing.PointF(bx - uy * half, by + ux * half), drawing.PointF(bx + uy * half, by - ux * half)]
    return drawing.PointF(bx, by), System.Array[drawing.PointF](tri)


def text_layout(g, st, s):
    """Font, lines, line height, text width and height, and padding of a note at scale s."""
    px = st.get("font_px", 30.0) * s
    f = font(max(4.0, px * 0.75), True)   # Eto sizes fonts in points
    lines = (st.get("text") or "").split("\n")
    sizes = [g.MeasureString(f, ln or " ") for ln in lines]
    lh = max(sz.Height for sz in sizes)
    return f, lines, lh, max(sz.Width for sz in sizes), lh * len(lines), 0.4 * px, 0.18 * px


_MEASURE = []


def measure_text(st):
    """Store the note's box size in image px (st["size"]), for hit tests and picks."""
    if not _MEASURE:
        _MEASURE.append(drawing.Bitmap(4, 4, drawing.PixelFormat.Format32bppRgba))
    g = drawing.Graphics(_MEASURE[0])
    try:
        _, _, _, tw, th, padx, pady = text_layout(g, st, 1.0)
    finally:
        g.Dispose()
    st["size"] = [round(tw + 2 * padx, 1), round(th + 2 * pady, 1)]


def draw_match(g, x, y, label, s):
    """A photo-match point: a ring with a gapped crosshair, so the exact point stays visible."""
    col = drawing.Color.FromArgb(*MATCH_RGB)
    rad = max(6.0, 11.0 * s)
    for c, wdt in ((drawing.Color.FromArgb(0, 0, 0, 150), 3.5), (col, 1.6)):
        g.DrawEllipse(pen(c, wdt), x - rad, y - rad, 2 * rad, 2 * rad)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            g.DrawLine(pen(c, wdt), x + dx * rad * 0.35, y + dy * rad * 0.35, x + dx * rad * 1.5, y + dy * rad * 1.5)
    f = font(max(6.0, 10.0 * s), True)
    ts = g.MeasureString(f, str(label))
    bx, by = x + rad * 1.1, y - rad * 1.1 - ts.Height
    g.FillPath(drawing.Color.FromArgb(10, 40, 50, 220), round_rect(bx, by, ts.Width + 8 * s, ts.Height, ts.Height / 2.0))
    g.DrawText(f, drawing.SolidBrush(col), bx + 4 * s, by, str(label))


def draw_stroke(g, st, s, ox, oy, bitmaps=None):
    """Draw one mark stored in image px, scaled by s and offset by (ox, oy)."""
    if st.get("by") == "claude":
        st = dict(st, rgb=[ACCENT.Rb, ACCENT.Gb, ACCENT.Bb])   # Claude's own marks, in Claude orange
    col = _colour(st) if "rgb" in st else ACCENT
    w = max(1.0, st.get("width", 6) * s)
    halo_w = w + max(2.0, 3.0 * s)
    pts = [drawing.PointF(ox + p[0] * s, oy + p[1] * s) for p in st["points"]]
    kind = st["kind"]
    if kind == "pen":
        if len(pts) == 1:
            g.FillEllipse(HALO, pts[0].X - halo_w / 2, pts[0].Y - halo_w / 2, halo_w, halo_w)
            g.FillEllipse(col, pts[0].X - w / 2, pts[0].Y - w / 2, w, w)
            return
        arr = System.Array[drawing.PointF](pts)
        g.DrawLines(pen(HALO, halo_w), arr)
        g.DrawLines(pen(col, w), arr)
    elif kind == "box":
        a, b = pts[0], pts[-1]
        x, y = min(a.X, b.X), min(a.Y, b.Y)
        bw, bh = abs(b.X - a.X), abs(b.Y - a.Y)
        g.DrawRectangle(pen(HALO, halo_w), x, y, bw, bh)
        g.DrawRectangle(pen(col, w), x, y, bw, bh)
    elif kind == "arrow":
        a, b = pts[0], pts[-1]
        if math.hypot(b.X - a.X, b.Y - a.Y) < 1:
            return
        base, tri = _arrow_shape(a, b, w, s)
        g.DrawLine(pen(HALO, halo_w), a, base)
        g.DrawPolygon(pen(HALO, max(2.0, 3.0 * s)), tri)
        g.DrawLine(pen(col, w), a, base)
        g.FillPolygon(col, tri)
    elif kind == "pin":
        c = pts[0]
        rad = (12.0 + st.get("width", 6)) * s
        g.FillEllipse(drawing.Color.FromArgb(0, 0, 0, 70), c.X - rad + 1.5 * s, c.Y - rad + 2.5 * s, 2 * rad, 2 * rad)
        g.FillEllipse(col, c.X - rad, c.Y - rad, 2 * rad, 2 * rad)
        g.DrawEllipse(pen(WHITE, max(1.0, 2.0 * s)), c.X - rad, c.Y - rad, 2 * rad, 2 * rad)
        ink = drawing.Colors.Black if st.get("colour") == "yellow" else WHITE
        text_centered(g, font(max(5.0, rad * 0.78), True), ink, c.X, c.Y, str(st.get("label", "")))
    elif kind == "ref":
        c = pts[0]
        rw, rh = st["size"][0] * s, st["size"][1] * s
        x, y = c.X - rw / 2.0, c.Y - rh / 2.0
        pad = max(2.0, 4.0 * s)
        g.FillRectangle(drawing.Color.FromArgb(0, 0, 0, 90), x - pad + 3 * s, y - pad + 5 * s, rw + 2 * pad, rh + 2 * pad)
        g.FillRectangle(WHITE, x - pad, y - pad, rw + 2 * pad, rh + 2 * pad)
        bmp = (bitmaps or {}).get(st["label"])
        if bmp is not None:
            g.ImageInterpolation = drawing.ImageInterpolation.High
            g.DrawImage(bmp, x, y, rw, rh)
        else:
            g.FillRectangle(PANEL_HI, x, y, rw, rh)
        f = font(max(8.0, 15.0 * s), True)
        chip = st["label"] + ("   %d match points" % len(st["matches"]) if st.get("matches") else "")
        tsize = g.MeasureString(f, chip)
        cw, ch = tsize.Width + 16 * s, tsize.Height + 6 * s
        cx0, cy0 = x - pad, y - pad - ch - 4 * s
        g.FillPath(drawing.Color.FromArgb(0, 0, 0, 90), round_rect(cx0 + 1.5 * s, cy0 + 2.5 * s, cw, ch, ch / 2.0))
        g.FillPath(ACCENT, round_rect(cx0, cy0, cw, ch, ch / 2.0))
        g.DrawText(f, drawing.SolidBrush(WHITE), cx0 + (cw - tsize.Width) / 2.0, cy0 + (ch - tsize.Height) / 2.0, chip)
    elif kind == "match":
        draw_match(g, pts[0].X, pts[0].Y, st.get("label", ""), s)
    elif kind == "text":
        # The user’s colour on a dark tag, so a note reads over any part of the capture.
        x, y = pts[0].X, pts[0].Y
        f, lines, lh, tw, th, padx, pady = text_layout(g, st, s)
        bw, bh = tw + 2 * padx, th + 2 * pady
        rad = min(bh / 2.0, max(3.0, 0.3 * st.get("font_px", 30.0) * s))
        g.FillPath(drawing.Color.FromArgb(0, 0, 0, 70), round_rect(x + 1.5 * s, y + 2.5 * s, bw, bh, rad))
        g.FillPath(drawing.Color.FromArgb(20, 21, 24, 205), round_rect(x, y, bw, bh, rad))
        g.DrawPath(pen(drawing.Color.FromArgb(col.Rb, col.Gb, col.Bb, 150), max(1.0, 1.5 * s)), round_rect(x, y, bw, bh, rad))
        for i, ln in enumerate(lines):
            g.DrawText(f, drawing.SolidBrush(col), x + padx, y + pady + i * lh, ln)


def _seg_dist(p, a, b):
    """Distance from point p to the segment a-b (image px)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _densify(pts, step=1.5):
    """The polyline with extra points so no gap exceeds step, so the eraser can cut long straight runs."""
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / step)
        for k in range(1, n + 1):
            t = k / float(n + 1)
            out.append([round(a[0] + t * (b[0] - a[0]), 1), round(a[1] + t * (b[1] - a[1]), 1)])
        out.append(b)
    return out


def erase_stroke(st, centres, r):
    """What an eraser of radius r at centres leaves of one mark: None if untouched, else a list of
    surviving pieces ([] when the mark goes). Pen lines are cut; other marks go whole; refs stay."""
    kind, pts = st["kind"], st["points"]
    reach = r + st.get("width", 6) / 2.0
    if kind == "ref":
        return None
    if kind == "pen":
        if len(pts) == 1:
            return [] if any(math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]) <= reach for c in centres) else None
        dense = _densify(pts)
        keep = [all(math.hypot(p[0] - c[0], p[1] - c[1]) > reach for c in centres) for p in dense]
        if all(keep):
            return None
        pieces, run = [], []
        for p, k in zip(dense + [None], keep + [False]):
            if k:
                run.append(p)
            elif run:
                length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(run, run[1:]))
                if length >= 3.0:   # crumbs shorter than 3 px go with the rubbed part
                    piece = dict((key, v) for key, v in st.items() if key != "closed")
                    piece["points"] = run
                    pieces.append(piece)
                run = []
        return pieces
    if kind == "box":
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        sides = [([x0, y0], [x1, y0]), ([x1, y0], [x1, y1]), ([x1, y1], [x0, y1]), ([x0, y1], [x0, y0])]
        hit = any(_seg_dist(c, a, b) <= reach for c in centres for a, b in sides)
    elif kind == "arrow":
        hit = any(_seg_dist(c, pts[0], pts[-1]) <= reach + st.get("width", 6) for c in centres)
    elif kind == "pin":
        hit = any(math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]) <= r + 12 + st.get("width", 6) for c in centres)
    elif kind == "match":
        hit = any(math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]) <= r + 10 for c in centres)
    elif kind == "text":
        (x, y), (tw, th) = pts[0], st.get("size") or [0, 0]
        hit = any(math.hypot(c[0] - min(max(c[0], x), x + tw), c[1] - min(max(c[1], y), y + th)) <= r
                  for c in centres)
    else:
        return None
    return [] if hit else None


def compose(clean_path, strokes, out_path, w, h, bitmaps=None):
    """Burn the marks into a copy of the capture at full resolution."""
    src = drawing.Bitmap(clean_path)
    out = drawing.Bitmap(w, h, drawing.PixelFormat.Format32bppRgb)
    g = drawing.Graphics(out)
    try:
        g.ImageInterpolation = drawing.ImageInterpolation.None
        g.DrawImage(src, 0.0, 0.0, float(w), float(h))
        g.AntiAlias = True
        for st in strokes:
            draw_stroke(g, st, 1.0, 0.0, 0.0, bitmaps)
    finally:
        g.Dispose()
    out.Save(out_path, drawing.ImageFormat.Png)
    return out


# ---------------------------------------------------------------- icons (drawn, 20 px box)

def draw_icon(g, name, cx, cy, col):
    p = pen(col, 1.8)
    if name == "pen":
        pts = [drawing.PointF(cx - 8 + i * 0.8, cy + 3 - 5 * math.sin(i / 3.2)) for i in range(21)]
        g.DrawLines(p, System.Array[drawing.PointF](pts))
    elif name == "box":
        g.DrawPath(p, round_rect(cx - 8, cy - 6, 16, 12, 2.5))
    elif name == "arrow":
        a, b = drawing.PointF(cx - 7, cy + 7), drawing.PointF(cx + 7, cy - 7)
        base, tri = _arrow_shape(a, b, 1.8, 0.45)
        g.DrawLine(p, a, base)
        g.FillPolygon(col, tri)
    elif name == "pin":
        g.FillEllipse(col, cx - 7, cy - 7, 14, 14)
        text_centered(g, font(7.0, True), PANEL, cx, cy, "1")
    elif name == "text":
        g.DrawLine(p, cx - 6.5, cy - 7, cx + 6.5, cy - 7)
        g.DrawLine(p, cx, cy - 7, cx, cy + 7)
        g.DrawLine(p, cx - 3, cy + 7, cx + 3, cy + 7)
    elif name == "chart":
        for k, hgt in enumerate((6, 11, 8)):
            g.FillPath(col, round_rect(cx - 7.5 + k * 5.5, cy + 7 - hgt, 4, hgt, 1))
        g.DrawLine(p, cx - 9, cy + 8, cx + 9, cy + 8)
    elif name == "crit":
        g.DrawPath(p, round_rect(cx - 8.5, cy - 7.5, 17, 12, 3))
        g.DrawLines(p, System.Array[drawing.PointF]([drawing.PointF(cx - 4, cy + 4.5), drawing.PointF(cx - 6, cy + 8.5),
                                                     drawing.PointF(cx, cy + 4.5)]))
        g.DrawLine(pen(col, 2.0), cx, cy - 4.5, cx, cy - 0.5)
        g.FillEllipse(col, cx - 1.1, cy + 1.2, 2.2, 2.2)
    elif name == "match":
        g.DrawEllipse(p, cx - 6, cy - 6, 12, 12)
        g.DrawLine(p, cx - 9, cy, cx - 3, cy)
        g.DrawLine(p, cx + 3, cy, cx + 9, cy)
        g.DrawLine(p, cx, cy - 9, cx, cy - 3)
        g.DrawLine(p, cx, cy + 3, cx, cy + 9)
    elif name == "eraser":
        # a block tilted 45 degrees, the rubbing end filled
        def rot(dx, dy):
            return drawing.PointF(cx + 0.7071 * (dx + dy), cy + 0.7071 * (dy - dx))
        g.FillPolygon(col, System.Array[drawing.PointF]([rot(-8, -4), rot(-2, -4), rot(-2, 4), rot(-8, 4)]))
        g.DrawPolygon(p, System.Array[drawing.PointF]([rot(-8, -4), rot(8, -4), rot(8, 4), rot(-8, 4)]))
    elif name in ("undo", "redo"):
        sgn = 1 if name == "undo" else -1
        g.DrawArc(p, drawing.RectangleF(cx - 6, cy - 5, 12, 11), 200 if sgn > 0 else -20, 230 * sgn)
        tip = drawing.PointF(cx - 6.5 * sgn, cy - 2)
        g.FillPolygon(col, System.Array[drawing.PointF]([drawing.PointF(tip.X - 3.5 * sgn, tip.Y - 4.5),
                                                          drawing.PointF(tip.X + 3.5 * sgn, tip.Y - 2.5),
                                                          drawing.PointF(tip.X - 1 * sgn, tip.Y + 3)]))
    elif name == "clear":
        g.DrawLine(p, cx - 7, cy - 5, cx + 7, cy - 5)
        g.DrawLine(p, cx - 2.5, cy - 7.5, cx + 2.5, cy - 7.5)
        g.DrawPath(p, round_rect(cx - 5, cy - 4, 10, 11, 2))
    elif name == "save":
        g.DrawLine(p, cx, cy - 7, cx, cy + 2)
        g.DrawLines(p, System.Array[drawing.PointF]([drawing.PointF(cx - 4, cy - 2), drawing.PointF(cx, cy + 2),
                                                     drawing.PointF(cx + 4, cy - 2)]))
        g.DrawLines(p, System.Array[drawing.PointF]([drawing.PointF(cx - 7, cy + 2), drawing.PointF(cx - 7, cy + 7),
                                                     drawing.PointF(cx + 7, cy + 7), drawing.PointF(cx + 7, cy + 2)]))
    elif name == "send":
        g.DrawLine(p, cx - 6, cy, cx + 5, cy)
        g.DrawLines(p, System.Array[drawing.PointF]([drawing.PointF(cx + 1, cy - 4.5), drawing.PointF(cx + 5.5, cy),
                                                     drawing.PointF(cx + 1, cy + 4.5)]))
    elif name == "cube":
        top = [drawing.PointF(cx, cy - 8), drawing.PointF(cx + 7, cy - 4.5), drawing.PointF(cx, cy - 1),
               drawing.PointF(cx - 7, cy - 4.5), drawing.PointF(cx, cy - 8)]
        g.DrawLines(p, System.Array[drawing.PointF](top))
        g.DrawLine(p, cx - 7, cy - 4.5, cx - 7, cy + 4.5)
        g.DrawLine(p, cx + 7, cy - 4.5, cx + 7, cy + 4.5)
        g.DrawLine(p, cx, cy - 1, cx, cy + 8)
        g.DrawLine(p, cx - 7, cy + 4.5, cx, cy + 8)
        g.DrawLine(p, cx + 7, cy + 4.5, cx, cy + 8)
    elif name == "swatch":
        g.DrawEllipse(p, cx - 7.5, cy - 7.5, 15, 15)
        g.FillPie(col, drawing.RectangleF(cx - 7.5, cy - 7.5, 15, 15), 135, 180)
    elif name == "sun":
        g.DrawEllipse(p, cx - 4, cy - 4, 8, 8)
        for k in range(8):
            a = k * math.pi / 4
            g.DrawLine(p, cx + 6.5 * math.cos(a), cy + 6.5 * math.sin(a), cx + 9 * math.cos(a), cy + 9 * math.sin(a))
    elif name == "camera":
        g.DrawPath(p, round_rect(cx - 8.5, cy - 5, 17, 12, 2.5))
        g.DrawEllipse(p, cx - 3.5, cy - 2.5, 7, 7)
        g.DrawLine(p, cx - 4, cy - 5, cx - 2, cy - 7.5)
        g.DrawLine(p, cx - 2, cy - 7.5, cx + 2, cy - 7.5)
        g.DrawLine(p, cx + 2, cy - 7.5, cx + 4, cy - 5)
    elif name == "plus":
        g.DrawLine(p, cx - 6, cy, cx + 6, cy)
        g.DrawLine(p, cx, cy - 6, cx, cy + 6)
    elif name == "check":
        g.DrawLines(pen(col, 2.0), System.Array[drawing.PointF]([drawing.PointF(cx - 4, cy), drawing.PointF(cx - 1, cy + 3),
                                                                 drawing.PointF(cx + 4.5, cy - 3.5)]))
    elif name == "search":
        g.DrawEllipse(p, cx - 7, cy - 7, 11, 11)
        g.DrawLine(pen(col, 2.2), cx + 2.5, cy + 2.5, cx + 7, cy + 7)
    elif name == "image":
        g.DrawPath(p, round_rect(cx - 8, cy - 6, 16, 12, 2))
        g.DrawLines(p, System.Array[drawing.PointF]([drawing.PointF(cx - 6, cy + 4), drawing.PointF(cx - 1.5, cy - 1),
                                                     drawing.PointF(cx + 1.5, cy + 2), drawing.PointF(cx + 3.5, cy),
                                                     drawing.PointF(cx + 6, cy + 4)]))
        g.FillEllipse(col, cx + 2, cy - 4.5, 3, 3)


# ---------------------------------------------------------------- event guard

_GUARD_REPORTED = set()


def guarded(fn):
    """Wrap an Eto event handler so an error is reported instead of killing the dialog.

    An exception escaping an IronPython handler closes the modal window (ShowModal returns as if cancelled),
    which threw away the user’s marks when the Analyse tab's drop zone raised a KeyError (2026-10-18). Each failing
    handler prints its traceback once to Rhino's command line; the window stays open."""
    def wrap(self, sender, e):
        try:
            return fn(self, sender, e)
        except Exception:
            import traceback
            if fn.__name__ not in _GUARD_REPORTED:
                _GUARD_REPORTED.add(fn.__name__)
                print("ClaudeMark: %s.%s failed; the window stays open, your marks are kept.\n%s" % (
                    type(self).__name__, fn.__name__, traceback.format_exc()))
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


# ---------------------------------------------------------------- a drawn button bar

class Bar(object):
    """A row of drawn controls on one Drawable: tool buttons, swatches, toggles, labels."""

    def __init__(self, height, background):
        self.d = forms.Drawable()
        self.d.Height = height
        self.background = background
        self.items = []
        self.rects = {}
        self.hover = None
        self.pressed = None
        self.d.Paint += self.on_paint
        self.d.MouseMove += self.on_move
        self.d.MouseLeave += self.on_leave
        self.d.MouseDown += self.on_down
        self.d.MouseUp += self.on_up

    def add(self, key, width, draw, click=None, tip=None, side="left"):
        self.items.append({"key": key, "w": width, "draw": draw, "click": click, "tip": tip, "side": side})

    def layout(self):
        h = float(self.d.Height)
        x = 14.0
        for it in self.items:
            if it["side"] == "left":
                self.rects[it["key"]] = drawing.RectangleF(x, 0.0, it["w"], h)
                x += it["w"] + 6
        x = float(self.d.Width) - 14.0
        for it in reversed(self.items):
            if it["side"] == "right":
                x -= it["w"]
                self.rects[it["key"]] = drawing.RectangleF(x, 0.0, it["w"], h)
                x -= 8

    def item_at(self, loc):
        for it in self.items:
            r = self.rects.get(it["key"])
            if r is not None and it["click"] and r.Contains(loc):
                return it
        return None

    @guarded
    def on_paint(self, sender, e):
        g = e.Graphics
        g.AntiAlias = True
        g.FillRectangle(self.background, 0.0, 0.0, float(self.d.Width), float(self.d.Height))
        self.layout()
        for it in self.items:
            it["draw"](g, self.rects[it["key"]], self.hover == it["key"], self.pressed == it["key"])

    @guarded
    def on_move(self, sender, e):
        it = self.item_at(e.Location)
        key = it["key"] if it else None
        if key != self.hover:
            self.hover = key
            self.d.ToolTip = it["tip"] if it and it["tip"] else None
            self.d.Cursor = forms.Cursors.Pointer if it else forms.Cursors.Default
            self.d.Invalidate()

    @guarded
    def on_leave(self, sender, e):
        if self.hover is not None or self.pressed is not None:
            self.hover = self.pressed = None
            self.d.Invalidate()

    @guarded
    def on_down(self, sender, e):
        it = self.item_at(e.Location)
        if it and e.Buttons == forms.MouseButtons.Primary:
            self.pressed = it["key"]
            self.d.Invalidate()

    @guarded
    def on_up(self, sender, e):
        it = self.item_at(e.Location)
        was, self.pressed = self.pressed, None
        self.d.Invalidate()
        if it and was == it["key"]:
            it["click"]()


def draw_chip(g, r, hover, active, fill=None):
    """The rounded background of a toolbar button."""
    size = min(r.Height - 14, r.Width)
    x = r.X + (r.Width - size) / 2.0
    y = r.Y + (r.Height - size) / 2.0
    if active:
        g.FillPath(ACCENT_SOFT, round_rect(x, y, size, size, 8))
        g.DrawPath(pen(ACCENT, 1.0), round_rect(x + 0.5, y + 0.5, size - 1, size - 1, 8))
    elif hover:
        g.FillPath(fill or HOVER, round_rect(x, y, size, size, 8))


def draw_separator(g, r, hover, pressed):
    g.DrawLine(drawing.Pen(LINE, 1.0), r.X + r.Width / 2.0, r.Y + 14, r.X + r.Width / 2.0, r.Bottom - 14)


def draw_button(g, r, label, hover, pressed, primary=False, icon=None, height=36):
    y = r.Y + (r.Height - height) / 2.0
    if primary:
        fill = ACCENT_HI if hover else ACCENT
        if pressed:
            fill = ACCENT
        g.FillPath(fill, round_rect(r.X, y, r.Width, height, 9))
        ink = WHITE
    else:
        g.FillPath(HOVER if hover else PANEL_HI, round_rect(r.X, y, r.Width, height, 9))
        g.DrawPath(pen(LINE, 1.0), round_rect(r.X + 0.5, y + 0.5, r.Width - 1, height - 1, 9))
        ink = TEXT
    f = font(10.0, primary)
    size = g.MeasureString(f, label)
    gap = 22 if icon else 0
    x = r.X + (r.Width - size.Width - gap) / 2.0
    if icon:
        draw_icon(g, icon, x + 8, y + height / 2.0, ink)
    g.DrawText(f, drawing.SolidBrush(ink), x + gap, y + (height - size.Height) / 2.0, label)


# ---------------------------------------------------------------- presets

def clean_name(stem):
    """'fd845e0_3dh_Larch_Smoked_Matte_002_15_94cm' -> 'Larch Smoked Matte 002'."""
    import re
    s = stem.split("_3dh_")[-1]
    s = re.sub(r"_\d+k_lin_srgb$|_lin_srgb$", "", s)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"(\s\d+(\s\d+)?\s?cm|\s\d+(\.\d+)?cm)$", "", s)
    return " ".join(s.split())[:40] or "Preset"


def _slug(text):
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "preset")[:48]


def _cosmos_package(rel):
    """Resolve 'Materials/Wood/Oak_..' to its Cosmos package: (files, preview png) or ([], None)."""
    import glob
    hits = glob.glob(os.path.join(COSMOS, rel.replace("/", os.sep) + "_*"))
    if not hits:
        return [], None
    pkg = hits[0]
    files = sorted(glob.glob(os.path.join(pkg, "*.vrmat")) + glob.glob(os.path.join(pkg, "*.exr")) +
                   glob.glob(os.path.join(pkg, "*.hdr")))
    previews = sorted(glob.glob(os.path.join(pkg, "Previews", "*.png")) + glob.glob(os.path.join(pkg, "Previews", "*.jpg")))
    return files, (previews[0] if previews else None)


def install_presets(settings):
    """Write the built-in presets that aren't on disk yet. The user’s edits to existing files are kept."""
    hidden = set(settings.get("hidden_presets", []))
    for p in BUILTIN_PRESETS:
        folder = os.path.join(PRESETS_DIR, p["category"])
        path = os.path.join(folder, p["slug"] + ".json")
        pid = p["category"] + "/" + p["slug"] + ".json"
        if os.path.exists(path) or pid in hidden:
            continue
        if not os.path.isdir(folder):
            os.makedirs(folder)
        rec = dict((k, v) for k, v in p.items() if k not in ("slug", "cosmos", "cosmos_thumb"))
        rec["builtin"] = True
        rec["order"] = BUILTIN_PRESETS.index(p)
        if "cosmos" in p:
            files, preview = _cosmos_package(p["cosmos"])
            rec["files"] = files
            if preview:
                rec["thumb"] = preview
            elif "swatch" not in rec:
                rec["icon"] = "swatch" if p["category"] == "materials" else "sun"
        if "cosmos_thumb" in p:
            preview = _cosmos_package(p["cosmos_thumb"])[1]
            if preview:
                rec["thumb"] = preview
        with open(path, "w") as f:
            json.dump(rec, f, indent=1)


def load_presets(settings):
    """All presets, by category: built-ins in their order, then the user’s own oldest first."""
    hidden = set(settings.get("hidden_presets", []))
    out = dict((c, []) for c, _, _ in CATEGORIES)
    for cat, _, _ in CATEGORIES:
        folder = os.path.join(PRESETS_DIR, cat)
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            if not fn.lower().endswith(".json"):
                continue
            pid = cat + "/" + fn
            if pid in hidden:
                continue
            try:
                with open(os.path.join(folder, fn)) as f:
                    rec = json.load(f)
            except Exception:
                continue
            rec["id"] = pid
            rec["category"] = cat
            rec["_mtime"] = os.path.getmtime(os.path.join(folder, fn))
            out[cat].append(rec)
        out[cat].sort(key=lambda r: (0, r.get("order", 0)) if r.get("builtin") else (1, r["_mtime"]))
    return out


def preset_from_source(source, category):
    """Turn a dropped file or browser image into a saved preset. Returns (preset id, category)."""
    is_path = isinstance(source, basestring) and not source.lower().startswith(("http://", "https://", "data:"))
    ext = os.path.splitext(source)[1].lower() if is_path else ""
    if ext == ".json":
        with open(source) as f:
            rec = json.load(f)
        category = rec.get("category", category)
        name = rec.get("name") or os.path.splitext(os.path.basename(source))[0]
        rec["builtin"] = False
    elif ext == ".vrmat":
        category = "materials"
        name = clean_name(os.path.splitext(os.path.basename(source))[0])
        rec = {"name": name, "summary": "Your V-Ray material (.vrmat), on the marked surfaces",
               "instructions": "Import this V-Ray material with -_vrayImportMaterial and assign it with "
                               "obj.RenderMaterial to the marked or selected objects.",
               "files": [source], "icon": "swatch"}
    elif ext in (".hdr", ".exr"):
        category = "lighting"
        stem = os.path.splitext(os.path.basename(source))[0]
        name = clean_name(stem.split("_", 1)[-1] if "_" in stem and len(stem.split("_", 1)[0]) == 8 else stem)
        rec = {"name": name, "summary": "Your HDRI, as a V-Ray dome light",
               "instructions": "Light the scene with this HDRI on a V-Ray dome light; rotate it so the sun suits "
                               "the marked view and expose for the building.",
               "files": [source], "icon": "sun"}
    elif ext in (".vrscene", ".vropt"):
        category = "render"
        name = clean_name(os.path.splitext(os.path.basename(source))[0])
        rec = {"name": name, "summary": "Your V-Ray render settings",
               "instructions": "Load the render settings from this file. " + RENDER_NOTE,
               "files": [source], "icon": "camera"}
    else:
        if category == "model":
            category = "materials"
        if is_path:
            name = clean_name(os.path.splitext(os.path.basename(source))[0])
        elif isinstance(source, basestring) and source.startswith("http"):
            host = source.split("/")[2].lower()
            for pre in ("www.", "images.", "img.", "cdn."):
                host = host[len(pre):] if host.startswith(pre) else host
            name = "Web image, " + host
        else:
            name = "Pasted image"
        name = name[:40]
        what = {"materials": "Make a V-Ray material that matches this photo (colour, pattern scale, roughness) "
                             "and apply it to the marked or selected objects.",
                "lighting": "Light the scene to match the mood of this image: time of day, sun direction, colour "
                            "temperature and contrast.",
                "render": "Match the look of this image: framing, lens, exposure and post look. " + RENDER_NOTE,
                "analyse": "Draw the analysis in the graphic style of this image: line weights, tones, colour "
                           "and how it is labelled.",
                "critique": "Use this image as the precedent to test the marked design against: where the design "
                            "does what the precedent does, and where it doesn't."}
        summary = {"materials": "Match this photo's material", "lighting": "Match this image's light",
                   "render": "Match this image's look", "analyse": "Draw it like this image",
                   "critique": "Test it against this precedent"}
        rec = {"name": name, "summary": summary.get(category, summary["render"]),
               "instructions": what.get(category, what["render"])}
        if isinstance(source, basestring) and source.startswith("http"):
            rec["source"] = source[:500]
        folder = os.path.join(PRESETS_DIR, category, "files")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        img_path = os.path.join(folder, "%s-%d.png" % (_slug(name), int(time.time())))
        load_image(source, img_path)
        rec["files"] = [img_path]
        rec["thumb"] = img_path
    if ext in (".vrmat", ".hdr", ".exr"):
        _, preview = _cosmos_package_of_file(source)
        if preview:
            rec["thumb"] = preview
    rec["category"] = category
    rec["builtin"] = False
    folder = os.path.join(PRESETS_DIR, category)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    fn = "%s.json" % _slug(rec.get("name", "preset"))
    while os.path.exists(os.path.join(folder, fn)):
        fn = "%s-%d.json" % (_slug(rec.get("name", "preset")), int(time.time() * 1000) % 100000)
    with open(os.path.join(folder, fn), "w") as f:
        json.dump(rec, f, indent=1)
    return category + "/" + fn, category


def _cosmos_package_of_file(path):
    import glob
    previews = sorted(glob.glob(os.path.join(os.path.dirname(path), "Previews", "*.png")))
    return None, (previews[0] if previews else None)


_THUMBS = {}


def thumb(path):
    """A small cached bitmap of a preview image (Cosmos previews are 1-2 MB each)."""
    if path in _THUMBS:
        return _THUMBS[path]
    bmp = None
    try:
        src = drawing.Bitmap(path)
        k = 96.0 / min(src.Size.Width, src.Size.Height)
        w, h = max(1, int(src.Size.Width * k)), max(1, int(src.Size.Height * k))
        bmp = drawing.Bitmap(w, h, drawing.PixelFormat.Format32bppRgba)
        g = drawing.Graphics(bmp)
        try:
            g.ImageInterpolation = drawing.ImageInterpolation.High
            g.DrawImage(src, 0.0, 0.0, float(w), float(h))
        finally:
            g.Dispose()
        src.Dispose()
    except Exception:
        bmp = None
    _THUMBS[path] = bmp
    return bmp


def wrap_text(g, f, text, width, max_lines=2):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if g.MeasureString(f, trial).Width <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(" ".join(lines)) < len(" ".join(words)):
        while lines[-1] and g.MeasureString(f, lines[-1] + "...").Width > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    return lines


class Sidebar(object):
    """The left panel: the kind of task, then the presets for it. Drop files here to add your own."""

    TAB_H = 42
    CARD_H = 66

    def __init__(self, dlg):
        self.dlg = dlg
        self.d = forms.Drawable()
        self.d.Width = SIDEBAR_W
        self.d.CanFocus = False
        self.d.AllowDrop = True
        self.scroll = 0.0
        self.hover = None
        self.drop_hover = False
        self.pending_delete = (None, 0.0)
        self.items = []
        self.d.Paint += self.on_paint
        self.d.MouseMove += self.on_move
        self.d.MouseLeave += self.on_leave
        self.d.MouseDown += self.on_down
        self.d.MouseDoubleClick += self.on_double
        self.d.MouseWheel += self.on_wheel
        self.d.DragEnter += self.on_drag_over
        self.d.DragOver += self.on_drag_over
        self.d.DragLeave += self.on_drag_leave
        self.d.DragDrop += self.on_drop

    # -- layout: a list of (kind, key, rect) in content coordinates

    def layout(self):
        w = float(self.d.Width)
        items, y = [], 16.0
        items.append(("label", "TASK", drawing.RectangleF(16, y, w - 32, 18)))
        y += 24
        for key, label, icon in CATEGORIES:
            items.append(("tab", key, drawing.RectangleF(10, y, w - 20, self.TAB_H)))
            y += self.TAB_H + 2
        y += 16
        items.append(("label", "PRESETS", drawing.RectangleF(16, y, w - 32, 18)))
        y += 26
        for rec in self.dlg.presets.get(self.dlg.task, []):
            items.append(("card", rec["id"], drawing.RectangleF(10, y, w - 20, self.CARD_H)))
            y += self.CARD_H + 6
        items.append(("drop", None, drawing.RectangleF(10, y, w - 20, 74)))
        y += 74 + 16
        self.items = items
        self.content_h = y
        self.scroll = max(0.0, min(self.scroll, self.content_h - float(self.d.Height)))

    def item_at(self, loc):
        y = loc.Y + self.scroll
        for kind, key, r in self.items:
            if kind in ("tab", "card", "drop") and r.X <= loc.X <= r.Right and r.Y <= y <= r.Bottom:
                return kind, key
        return None

    def preset(self, pid):
        for rec in self.dlg.presets.get(self.dlg.task, []):
            if rec["id"] == pid:
                return rec
        return None

    # -- drawing

    @guarded
    def on_paint(self, sender, e):
        g = e.Graphics
        g.AntiAlias = True
        w, h = float(self.d.Width), float(self.d.Height)
        g.FillRectangle(PANEL, 0.0, 0.0, w, h)
        g.DrawLine(drawing.Pen(LINE, 1.0), w - 0.5, 0.0, w - 0.5, h)
        self.layout()
        g.TranslateTransform(0.0, -self.scroll)
        for kind, key, r in self.items:
            if kind == "label":
                f = font(8.5, True)
                txt = key if key == "TASK" else "PRESETS  -  %s" % dict((c, l) for c, l, _ in CATEGORIES)[self.dlg.task].upper()
                g.DrawText(f, drawing.SolidBrush(MUTED), r.X, r.Y, txt)
                if key == "PRESETS":
                    hint = "click to use"
                    hs = g.MeasureString(font(8.5), hint)
                    g.DrawText(font(8.5), drawing.SolidBrush(MUTED), r.Right - hs.Width, r.Y, hint)
            elif kind == "tab":
                self.draw_tab(g, key, r)
            elif kind == "card":
                self.draw_card(g, self.preset(key), r)
            elif kind == "drop":
                self.draw_drop(g, r)
        g.TranslateTransform(0.0, self.scroll)
        if self.content_h > h:  # a thin scroll indicator
            bar_h = max(30.0, h * h / self.content_h)
            bar_y = (h - bar_h) * self.scroll / (self.content_h - h)
            g.FillPath(LINE, round_rect(w - 6, bar_y + 2, 3, bar_h - 4, 1.5))

    def draw_tab(self, g, key, r):
        active = self.dlg.task == key
        hover = self.hover == ("tab", key)
        if active:
            g.FillPath(ACCENT_SOFT, round_rect(r.X, r.Y, r.Width, r.Height, 9))
            g.FillPath(ACCENT, round_rect(r.X, r.Y + 9, 3, r.Height - 18, 1.5))
        elif hover:
            g.FillPath(HOVER, round_rect(r.X, r.Y, r.Width, r.Height, 9))
        label, icon = [(l, i) for c, l, i in CATEGORIES if c == key][0]
        col = ACCENT if active else TEXT
        draw_icon(g, icon, r.X + 24, r.Y + r.Height / 2.0, col)
        f = font(10.5, active)
        size = g.MeasureString(f, label)
        g.DrawText(f, drawing.SolidBrush(TEXT), r.X + 46, r.Y + (r.Height - size.Height) / 2.0, label)
        n = sum(1 for pid in self.dlg.selected if pid.startswith(key + "/"))
        if n:
            txt = str(n)
            bf = font(8.5, True)
            bs = g.MeasureString(bf, txt)
            bw = max(20.0, bs.Width + 12)
            g.FillPath(ACCENT, round_rect(r.Right - bw - 10, r.Y + r.Height / 2.0 - 10, bw, 20, 10))
            g.DrawText(bf, drawing.SolidBrush(WHITE), r.Right - bw - 10 + (bw - bs.Width) / 2.0,
                       r.Y + r.Height / 2.0 - bs.Height / 2.0, txt)

    def draw_card(self, g, rec, r):
        if rec is None:
            return
        pid = rec["id"]
        selected = pid in self.dlg.selected
        hover = self.hover == ("card", pid)
        deleting = self.pending_delete[0] == pid and time.time() - self.pending_delete[1] < 3.0
        base = rgb("#24262A")
        g.FillPath(ACCENT_SOFT if selected else (HOVER if hover else base), round_rect(r.X, r.Y, r.Width, r.Height, 10))
        if selected or deleting:
            g.DrawPath(pen(rgb("#F04438") if deleting else ACCENT, 1.5),
                       round_rect(r.X + 0.75, r.Y + 0.75, r.Width - 1.5, r.Height - 1.5, 10))
        # thumbnail
        tx, ty, ts = r.X + 10, r.Y + 11, 44.0
        path = round_rect(tx, ty, ts, ts, 8)
        bmp = thumb(rec["thumb"]) if rec.get("thumb") and os.path.exists(rec["thumb"]) else None
        if bmp is not None:
            g.SaveTransform()
            g.SetClip(path)
            bw, bh = float(bmp.Size.Width), float(bmp.Size.Height)
            k = ts / min(bw, bh)
            g.DrawImage(bmp, tx + (ts - bw * k) / 2.0, ty + (ts - bh * k) / 2.0, bw * k, bh * k)
            g.ResetClip()
            g.RestoreTransform()
        elif rec.get("gradient"):
            (a, b) = rec["gradient"]
            brush = drawing.LinearGradientBrush(drawing.Color.FromArgb(*a), drawing.Color.FromArgb(*b),
                                                drawing.PointF(tx, ty), drawing.PointF(tx, ty + ts))
            g.FillPath(brush, path)
        elif rec.get("swatch"):
            g.FillPath(drawing.Color.FromArgb(*rec["swatch"]), path)
        else:
            g.FillPath(PANEL_HI, path)
            draw_icon(g, rec.get("icon", "image"), tx + ts / 2.0, ty + ts / 2.0, MUTED)
        g.DrawPath(pen(drawing.Color.FromArgb(255, 255, 255, 28), 1.0), path)
        # text
        x = tx + ts + 12
        width = r.Right - x - (30 if selected else 12)
        tf = font(10.0, True)
        title = wrap_text(g, tf, rec.get("name", ""), width, 1)[0] if rec.get("name") else ""
        g.DrawText(tf, drawing.SolidBrush(TEXT), x, r.Y + 10, title)
        sf = font(8.5)
        summary = "Right-click again to remove" if deleting else rec.get("summary", "")
        for k, line in enumerate(wrap_text(g, sf, summary, width + (30 if selected else 0) - 4, 2)):
            g.DrawText(sf, drawing.SolidBrush(rgb("#F04438") if deleting else MUTED), x, r.Y + 30 + 14 * k, line)
        if selected:
            cx, cy = r.Right - 18, r.Y + 18
            g.FillEllipse(ACCENT, cx - 9, cy - 9, 18, 18)
            draw_icon(g, "check", cx, cy, WHITE)

    def draw_dropzone_text(self, g, r, lines):
        f1, f2 = font(9.5, True), font(8.5)
        s1 = g.MeasureString(f1, lines[0])
        g.DrawText(f1, drawing.SolidBrush(TEXT), r.X + 46, r.Y + 17, lines[0])
        g.DrawText(f2, drawing.SolidBrush(MUTED), r.X + 46, r.Y + 17 + s1.Height + 2, lines[1])

    def draw_drop(self, g, r):
        active = self.drop_hover or self.hover == ("drop", None)
        if self.drop_hover:
            g.FillPath(ACCENT_SOFT, round_rect(r.X, r.Y, r.Width, r.Height, 10))
        dash = drawing.Pen(ACCENT if active else LINE, 1.5)
        dash.DashStyle = drawing.DashStyles.Dash
        g.DrawPath(dash, round_rect(r.X + 0.75, r.Y + 0.75, r.Width - 1.5, r.Height - 1.5, 10))
        draw_icon(g, "plus", r.X + 24, r.Y + r.Height / 2.0, ACCENT if active else MUTED)
        kinds = {"model": "images, preset .json", "materials": "photos, .vrmat, preset .json",
                 "lighting": "photos, .hdr / .exr, preset .json", "render": "renders you like, .vrscene, .json",
                 "analyse": "a drawing style to copy, preset .json", "critique": "a precedent to test against, .json"}
        self.draw_dropzone_text(g, r, ["Drop your own preset", kinds.get(self.dlg.task, "images, preset .json")])

    # -- events

    @guarded
    def on_move(self, sender, e):
        it = self.item_at(e.Location)
        if it != self.hover:
            self.hover = it
            self.d.Cursor = forms.Cursors.Pointer if it and it[0] in ("tab", "card") else forms.Cursors.Default
            tip = None
            if it and it[0] == "card":
                rec = self.preset(it[1])
                if rec:
                    tip = rec.get("instructions", "")
                    if rec.get("files"):
                        tip += "\n\n" + "\n".join(rec["files"])
                    tip += "\n\nDouble-click to edit the preset file."
            elif it and it[0] == "drop":
                tip = "Drag files from Explorer or images from Chrome here. They're saved for next time."
            self.d.ToolTip = tip
            self.d.Invalidate()

    @guarded
    def on_leave(self, sender, e):
        if self.hover is not None:
            self.hover = None
            self.d.Invalidate()

    @guarded
    def on_down(self, sender, e):
        it = self.item_at(e.Location)
        if not it:
            return
        kind, key = it
        if kind == "tab" and e.Buttons == forms.MouseButtons.Primary:
            self.dlg.set_task(key)
        elif kind == "card" and e.Buttons == forms.MouseButtons.Primary:
            if key in self.dlg.selected:
                self.dlg.selected.remove(key)
            else:
                self.dlg.selected.append(key)
            self.d.Invalidate()
            self.dlg.actions.d.Invalidate()
        elif kind == "card" and e.Buttons == forms.MouseButtons.Alternate:
            pid, t = self.pending_delete
            if pid == key and time.time() - t < 3.0:
                self.dlg.remove_preset(key)
                self.pending_delete = (None, 0.0)
            else:
                self.pending_delete = (key, time.time())
            self.d.Invalidate()

    @guarded
    def on_double(self, sender, e):
        it = self.item_at(e.Location)
        if it and it[0] == "card":
            path = os.path.join(PRESETS_DIR, it[1].replace("/", os.sep))
            try:
                System.Diagnostics.Process.Start("notepad.exe", '"%s"' % path)
            except Exception:
                pass

    @guarded
    def on_wheel(self, sender, e):
        self.scroll -= e.Delta.Height * 48
        self.layout()
        self.d.Invalidate()
        e.Handled = True

    @guarded
    def on_drag_over(self, sender, e):
        files, alts = dropped_sources(e.Data)
        any_file = []
        try:
            if e.Data.ContainsUris:
                any_file = [u.LocalPath for u in e.Data.Uris if u.IsFile]
        except Exception:
            pass
        if files or alts or any_file:
            e.Effects = forms.DragEffects.Copy
            if not self.drop_hover:
                self.drop_hover = True
                self.d.Invalidate()
        else:
            e.Effects = forms.DragEffects.None

    @guarded
    def on_drag_leave(self, sender, e):
        self.drop_hover = False
        self.d.Invalidate()

    @guarded
    def on_drop(self, sender, e):
        self.drop_hover = False
        sources = []
        try:
            if e.Data.ContainsUris:
                sources = [u.LocalPath for u in e.Data.Uris if u.IsFile]
        except Exception:
            pass
        if not sources:
            files, alts = dropped_sources(e.Data)
            sources = files
            if not sources and alts:
                self.dlg.add_preset_from(alts)
                return
        for s in sources[:12]:
            self.dlg.add_preset_from([s])
        self.d.Invalidate()


# ---------------------------------------------------------------- the markup window

class MarkupDialog(forms.Dialog[bool]):

    def __init__(self, work, w, h, info, settings, reply=None):
        self.work = work
        # A reply: Claude's crit drawn on the user’s capture, shown as a locked layer the user answers on top of.
        self.reply = reply
        self.claude_strokes = list((reply or {}).get("strokes") or [])
        self.show_claude = True
        self.photo = None        # label of the reference photo open full-size for matching, or None
        self.photo_last = None   # the photo the model's match points pair with
        self.pending_reply = None if reply else pending_reply()
        # An options board: sketched options (not modelled) to flip between; sending picks the one showing.
        self.options = list((reply or {}).get("options") or []) if (reply or {}).get("kind") == "options" else []
        self.option_index = 0
        self.option_bitmaps = {}
        for opt in self.options:
            try:
                self.option_bitmaps[opt["label"]] = drawing.Bitmap(os.path.join(reply["_folder"], opt["image"]))
            except Exception:
                pass
        self.option_chips = []
        self.iw, self.ih = float(w), float(h)
        self.clean_path = os.path.join(work, "clean.png")
        self.image = drawing.Bitmap(self.clean_path)
        self.settings = settings
        self.tool = settings["tool"]
        self.colour_index = settings["colour"]
        self.width_index = settings["width"]
        self.paste = bool(settings.get("jump", True))
        self.strokes = []
        self.history = []
        self.future = []
        self.bitmaps = {}
        self.ref_count = 0
        self.live = None
        self.shift_base = None
        self.moving = None
        self.editing = None   # the note being typed: {"st", "new", "before", "orig"}
        self.erasing = None   # an eraser drag: {"last": image point, "changed": bool}
        self.mouse_in = False
        self.last_wheel = (None, 0.0)
        self.drop_hover = False
        self.toast = None
        self.mouse = drawing.PointF(0, 0)
        self.note_text = ""
        self.info = info
        try:
            install_presets(settings)
        except Exception:
            pass
        self.presets = load_presets(settings)
        known = set(r["id"] for recs in self.presets.values() for r in recs)
        self.task = settings.get("task") if settings.get("task") in self.presets else "model"
        self.selected = [pid for pid in settings.get("presets", []) if pid in known]

        self.Title = "ClaudeMark"
        self.Resizable = True
        self.BackgroundColor = BG
        self.Padding = drawing.Padding(0)

        # -- canvas
        self.canvas = forms.Drawable()
        self.canvas.CanFocus = True
        self.canvas.AllowDrop = True
        self.canvas.BackgroundColor = BG
        self.canvas.Cursor = forms.Cursors.Crosshair
        self.canvas.Paint += self.on_paint
        self.canvas.MouseDown += self.on_down
        self.canvas.MouseMove += self.on_move
        self.canvas.MouseUp += self.on_up
        self.canvas.MouseWheel += self.on_wheel
        self.canvas.KeyDown += self.on_key
        # Typed characters come through TextInput (a key press arrives first as KeyDown, then as text).
        self.canvas.TextInput += self.on_text_input
        self.canvas.MouseDoubleClick += self.on_double
        self.canvas.MouseLeave += self.on_leave
        self.canvas.LostFocus += self.on_canvas_blur
        self.canvas.DragEnter += self.on_drag_over
        self.canvas.DragOver += self.on_drag_over
        self.canvas.DragLeave += self.on_drag_leave
        self.canvas.DragDrop += self.on_drop

        self.timer = forms.UITimer()
        self.timer.Interval = 0.25
        self.timer.Elapsed += self.on_timer
        self.chrome_hwnd = None
        self.chrome_before = None
        self.chrome_wait = None
        self.chrome_timer = forms.UITimer()
        self.chrome_timer.Interval = 0.15
        self.chrome_timer.Elapsed += self.on_chrome_timer

        self.toolbar = self.build_toolbar()
        request = self.build_request()

        holder = forms.Panel()
        holder.Padding = drawing.Padding(12, 12, 12, 12)
        holder.BackgroundColor = BG
        holder.Content = self.canvas

        self.sidebar = Sidebar(self)
        right = forms.TableLayout()
        right.Spacing = drawing.Size(0, 0)
        view_row = forms.TableRow(forms.TableCell(holder, True))
        view_row.ScaleHeight = True
        right.Rows.Add(view_row)
        right.Rows.Add(forms.TableRow(forms.TableCell(request, True)))

        # Sidebar and view get their own table: column scaling in an Eto table is per column across
        # ALL rows, so sharing the toolbar's table made the sidebar column stretch too.
        body = forms.TableLayout()
        body.Spacing = drawing.Size(0, 0)
        body.Rows.Add(forms.TableRow(forms.TableCell(self.sidebar.d, False), forms.TableCell(right, True)))

        table = forms.TableLayout()
        table.Spacing = drawing.Size(0, 0)
        table.Rows.Add(forms.TableRow(forms.TableCell(self.toolbar.d, True)))
        middle = forms.TableRow(forms.TableCell(body, True))
        middle.ScaleHeight = True
        table.Rows.Add(middle)
        self.Content = table
        self.set_task(self.task)
        # Size the window, not the canvas: a sized canvas won't shrink when Chrome docks beside it.
        cs = self.initial_canvas_size()
        self.ClientSize = drawing.Size(SIDEBAR_W + cs.Width + 24, cs.Height + 24 + 58 + 196)
        self.MinimumSize = drawing.Size(980, 640)
        self.KeyDown += self.on_dialog_key
        self.Shown += self.on_shown

    # -- construction

    def initial_canvas_size(self):
        try:
            area = forms.Screen.FromPoint(forms.Mouse.Position).WorkingArea
        except Exception:
            area = forms.Screen.PrimaryScreen.WorkingArea
        s = min(1.0, (area.Width * 0.94 - 40 - SIDEBAR_W) / self.iw, (area.Height * 0.93 - 300) / self.ih)
        return drawing.Size(int(self.iw * s), int(self.ih * s))

    def build_toolbar(self):
        bar = Bar(58, PANEL)

        def brand(g, r, hover, pressed):
            g.FillEllipse(ACCENT, r.X, r.Y + r.Height / 2.0 - 5, 10, 10)
            f = font(12.0, True, FONT_HEAD)
            size = g.MeasureString(f, "ClaudeMark")
            g.DrawText(f, drawing.SolidBrush(TEXT), r.X + 18, r.Y + (r.Height - size.Height) / 2.0, "ClaudeMark")
        bar.add("brand", 118, brand)
        bar.add("sep1", 10, draw_separator)

        for key, tip in TOOLS:
            def tool_draw(g, r, hover, pressed, key=key):
                active = self.tool == key
                draw_chip(g, r, hover, active)
                draw_icon(g, key, r.X + r.Width / 2.0, r.Y + r.Height / 2.0, ACCENT if active else TEXT)

            def tool_click(key=key):
                self.set_tool(key)
            bar.add("tool_" + key, 40, tool_draw, tool_click, tip)
        bar.add("sep2", 10, draw_separator)

        for k, (name, c) in enumerate(COLOURS):
            def sw_draw(g, r, hover, pressed, k=k, c=c):
                cx, cy = r.X + r.Width / 2.0, r.Y + r.Height / 2.0
                col = drawing.Color.FromArgb(c[0], c[1], c[2])
                if self.colour_index == k:
                    g.DrawEllipse(pen(TEXT, 2.0), cx - 12, cy - 12, 24, 24)
                elif hover:
                    g.DrawEllipse(pen(LINE, 2.0), cx - 12, cy - 12, 24, 24)
                g.FillEllipse(col, cx - 8, cy - 8, 16, 16)

            def sw_click(k=k):
                self.colour_index = k
                if self.editing is not None:
                    self.editing["st"]["colour"], self.editing["st"]["rgb"] = COLOURS[k][0], list(COLOURS[k][1])
                    self.canvas.Invalidate()
                self.toolbar.d.Invalidate()
                self.canvas.Focus()
            bar.add("col_%d" % k, 30, sw_draw, sw_click, "%s  (%d)" % (name.capitalize(), k + 1))
        bar.add("sep3", 10, draw_separator)

        for k, (name, px) in enumerate(WIDTHS):
            def w_draw(g, r, hover, pressed, k=k):
                active = self.width_index == k
                draw_chip(g, r, hover, active)
                cx, cy = r.X + r.Width / 2.0, r.Y + r.Height / 2.0
                g.DrawLine(pen(ACCENT if active else TEXT, [1.5, 3.5, 6.0][k]), cx - 7, cy, cx + 7, cy)

            def w_click(k=k):
                self.width_index = k
                if self.editing is not None:
                    st = self.editing["st"]
                    st["width"], st["font_px"] = WIDTHS[k][1], TEXT_PX[WIDTHS[k][1]]
                    measure_text(st)
                    self.canvas.Invalidate()
                self.toolbar.d.Invalidate()
                self.canvas.Focus()
            bar.add("w_%d" % k, 36, w_draw, w_click, "%s line, text or eraser" % name)
        bar.add("sep4", 10, draw_separator)

        for key, tip, fn in [("undo", "Undo  (Ctrl+Z, right-click)", self.undo),
                             ("redo", "Redo  (Ctrl+Y)", self.redo),
                             ("clear", "Clear all marks", self.clear)]:
            def act_draw(g, r, hover, pressed, key=key):
                draw_chip(g, r, hover, False)
                draw_icon(g, key, r.X + r.Width / 2.0, r.Y + r.Height / 2.0, TEXT if hover else MUTED)
            bar.add(key, 38, act_draw, fn, tip)

        def info_draw(g, r, hover, pressed):
            f = font(9.5)
            size = g.MeasureString(f, self.info)
            g.DrawText(f, drawing.SolidBrush(MUTED), r.Right - size.Width, r.Y + (r.Height - size.Height) / 2.0, self.info)
        bar.add("info", 400, info_draw, side="right")

        def find_draw(g, r, hover, pressed):
            draw_button(g, r, "Find examples", hover, pressed, False, "search", 34)
        bar.add("find", 156, find_draw, self.open_examples,
                "Open Chrome beside this window to find reference images; drag them onto the view  (Ctrl+F)",
                side="right")
        return bar

    def build_request(self):
        clr.AddReference("PresentationFramework")
        clr.AddReference("PresentationCore")
        clr.AddReference("WindowsBase")
        from System.Windows import Thickness, HorizontalAlignment, VerticalAlignment
        from System.Windows.Controls import TextBlock, Border
        from System.Windows.Media import SolidColorBrush, VisualBrush, AlignmentX, AlignmentY, Stretch
        from System.Windows.Media import Color as WColor

        def brush(c):
            return SolidColorBrush(WColor.FromArgb(c.Ab, c.Rb, c.Gb, c.Bb))

        head = forms.Label()
        head.Text = "Request"
        head.Font = font(12.0, True, FONT_HEAD)
        head.TextColor = TEXT
        sub = forms.Label()
        sub.Text = TASK_TEXT["model"][0]
        sub.Font = font(9.5)
        sub.TextColor = MUTED
        self.sub_label = sub
        heading = forms.StackLayout()
        heading.Orientation = forms.Orientation.Horizontal
        heading.Spacing = 12
        heading.VerticalContentAlignment = forms.VerticalAlignment.Bottom
        heading.Items.Add(forms.StackLayoutItem(head, False))
        heading.Items.Add(forms.StackLayoutItem(sub, False))

        self.note = forms.TextArea()
        self.note.AcceptsReturn = True
        self.note.Wrap = True
        self.note.Font = font(12.5)
        self.note.Height = 86
        tb = self.note.ControlObject
        tb.BorderThickness = Thickness(0)
        tb.Foreground = brush(TEXT)
        tb.CaretBrush = brush(ACCENT)
        tb.SelectionBrush = brush(ACCENT)
        tb.Padding = Thickness(12, 10, 12, 10)
        solid = brush(PANEL_HI)
        hint = TextBlock()
        hint.Text = TASK_TEXT["model"][1]
        self.hint_block = hint
        hint.Foreground = brush(MUTED)
        hint.FontSize = tb.FontSize
        hint.FontFamily = tb.FontFamily
        hint.Margin = Thickness(14, 10, 0, 0)
        card = Border()
        card.Background = solid
        card.Width = 4000
        card.Height = 400
        card.Child = hint
        placeholder = VisualBrush(card)
        placeholder.Stretch = Stretch.None
        placeholder.AlignmentX = AlignmentX.Left
        placeholder.AlignmentY = AlignmentY.Top

        def refresh(*args):
            tb.Background = placeholder if not self.note.Text else solid
        refresh()
        self.note.TextChanged += refresh
        self.note.KeyDown += self.on_note_key

        frame = forms.Panel()
        frame.Padding = drawing.Padding(1)
        frame.BackgroundColor = LINE
        frame.Content = self.note
        self.note.GotFocus += lambda s, e: setattr(frame, "BackgroundColor", ACCENT)
        self.note.LostFocus += lambda s, e: setattr(frame, "BackgroundColor", LINE)

        actions = Bar(56, PANEL)

        def hint_draw(g, r, hover, pressed):
            f = font(9.0)
            g.DrawText(f, drawing.SolidBrush(MUTED), r.X, r.Y + 20,
                       "Shift: straight  -  drop or paste images: references (drag to move, scroll to resize)  -  "
                       "right-click: undo")
        actions.add("hint", 700, hint_draw)

        def send_draw(g, r, hover, pressed):
            draw_button(g, r, "Send to Claude", hover, pressed, True, "send")

        def cancel_draw(g, r, hover, pressed):
            draw_button(g, r, "Cancel", hover, pressed)

        def save_draw(g, r, hover, pressed):
            draw_button(g, r, "Save...", hover, pressed, False, "save")

        def toggle_draw(g, r, hover, pressed):
            f = font(9.5)
            label = "Paste into Claude Code"
            size = g.MeasureString(f, label)
            cy = r.Y + r.Height / 2.0
            tx = r.Right - 36
            g.FillPath(ACCENT if self.paste else LINE, round_rect(tx, cy - 9, 36, 18, 9))
            kx = tx + (20 if self.paste else 2)
            g.FillEllipse(WHITE, kx, cy - 7, 14, 14)
            g.DrawText(f, drawing.SolidBrush(TEXT if hover else MUTED), tx - size.Width - 10, cy - size.Height / 2.0, label)

        def toggle_click():
            self.paste = not self.paste
            actions.d.Invalidate()
        # right-hand items are laid out from the right edge in reverse, so Send ends up last
        actions.add("paste", 200, toggle_draw, toggle_click,
                    "Bring the terminal forward and put the request and image in Claude Code's prompt", side="right")
        actions.add("save", 104, save_draw, self.on_save, "Save the marked image to a file  (Ctrl+S)", side="right")
        actions.add("cancel", 92, cancel_draw, lambda: self.Close(False), "Esc", side="right")
        actions.add("send", 168, send_draw, self.on_send, "Enter", side="right")
        self.actions = actions

        col = forms.TableLayout()
        col.Spacing = drawing.Size(0, 8)
        col.Padding = drawing.Padding(14, 12, 14, 0)
        col.BackgroundColor = PANEL
        col.Rows.Add(forms.TableRow(forms.TableCell(heading, True)))
        col.Rows.Add(forms.TableRow(forms.TableCell(frame, True)))
        col.Rows.Add(forms.TableRow(forms.TableCell(actions.d, True)))
        return col

    # -- state

    def set_tool(self, key):
        self.tool = key
        self.toolbar.d.Invalidate()
        self.canvas.Focus()

    def set_task(self, key):
        self.task = key
        if self.options:
            text = STYLE_TEXT if (self.reply or {}).get("mode") == "style" else OPTIONS_TEXT
        else:
            text = REPLY_TEXT if self.reply else TASK_TEXT[key]
        self.sub_label.Text = text[0]
        self.hint_block.Text = text[1]
        self.sidebar.scroll = 0.0
        self.sidebar.d.Invalidate()

    def reload_presets(self):
        self.presets = load_presets(self.settings)
        known = set(r["id"] for recs in self.presets.values() for r in recs)
        self.selected = [pid for pid in self.selected if pid in known]
        self.sidebar.d.Invalidate()

    def add_preset_from(self, sources):
        """Save a dropped file or browser image as a preset in the open task (or the one its type implies)."""
        err = None
        for src in sources:
            try:
                pid, cat = preset_from_source(src, self.task)
            except Exception as ex:
                err = ex
                continue
            self.reload_presets()
            self.set_task(cat)
            if pid not in self.selected:
                self.selected.append(pid)
            name = [r["name"] for r in self.presets[cat] if r["id"] == pid]
            self.show_toast("Added preset \"%s\" to %s, and selected it" % (
                (name or ["?"])[0], dict((c, l) for c, l, _ in CATEGORIES)[cat]), 4)
            return
        self.show_toast("Couldn't make a preset from that (%s)" % str(err)[:60] if err else "Nothing to add", 4)

    def remove_preset(self, pid):
        rec = None
        for recs in self.presets.values():
            for r in recs:
                if r["id"] == pid:
                    rec = r
        if rec is None:
            return
        if rec.get("builtin"):
            hidden = self.settings.setdefault("hidden_presets", [])
            if pid not in hidden:
                hidden.append(pid)
            save_settings(self.settings)
        else:
            try:
                os.remove(os.path.join(PRESETS_DIR, pid.replace("/", os.sep)))
            except Exception:
                pass
        if pid in self.selected:
            self.selected.remove(pid)
        self.reload_presets()
        self.show_toast("Removed \"%s\"" % rec.get("name", ""), 3)

    def chosen_presets(self):
        out = []
        for pid in self.selected:
            for recs in self.presets.values():
                for r in recs:
                    if r["id"] == pid:
                        out.append(dict((k, v) for k, v in r.items() if not k.startswith("_")))
        return out

    def show_toast(self, text, seconds=2.5):
        self.toast = (text, time.time() + seconds)
        self.timer.Start()
        self.canvas.Invalidate()

    @guarded
    def on_timer(self, sender, e):
        if self.toast and time.time() > self.toast[1]:
            self.toast = None
            self.timer.Stop()
            self.canvas.Invalidate()

    @guarded
    def on_shown(self, sender, e):
        self.canvas.Focus()
        if self.pending_reply:
            self.show_toast("Claude replied to %s. Type ClaudeReply in Rhino to open it" % self.pending_reply[1], 7)
        try:  # dark title bar (Windows 10 2004+ / 11)
            import ctypes
            from System.Windows.Interop import WindowInteropHelper
            hwnd = WindowInteropHelper(self.ControlObject).Handle.ToInt64()
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(val), 4)
        except Exception:
            pass

    # -- geometry

    def dims(self):
        """Pixel size of what the canvas shows: the capture, or the reference photo open for matching."""
        if self.photo and self.photo in self.bitmaps:
            sz = self.bitmaps[self.photo].Size
            return float(sz.Width), float(sz.Height)
        return self.iw, self.ih

    def fit(self):
        iw, ih = self.dims()
        cw, ch = float(self.canvas.Width), float(self.canvas.Height)
        s = min(cw / iw, ch / ih, 1.0)
        return s, (cw - iw * s) / 2.0, (ch - ih * s) / 2.0

    def to_image(self, loc):
        iw, ih = self.dims()
        s, ox, oy = self.fit()
        x = min(max((loc.X - ox) / s, 0.0), iw - 1)
        y = min(max((loc.Y - oy) / s, 0.0), ih - 1)
        return [round(x, 1), round(y, 1)]

    # -- photo match

    def ref_by_label(self, label):
        for st in self.strokes:
            if st["kind"] == "ref" and st["label"] == label:
                return st
        return None

    def open_photo(self, label):
        self.commit_edit()
        if self.ref_by_label(label) is None:
            return
        self.photo = self.photo_last = label
        self.set_tool("match")
        self.canvas.Invalidate()

    def close_photo(self):
        self.photo = None
        self.canvas.Invalidate()

    def photo_points(self):
        ref = self.ref_by_label(self.photo or self.photo_last)
        return (ref or {}).get("matches") or []

    def model_matches(self):
        return [st for st in self.strokes if st["kind"] == "match"]

    def add_match(self, p):
        """A match point: on the photo if it's open, else on the model, paired by number."""
        if self.photo:
            iw, ih = self.dims()
            self.snapshot()
            ref = self.ref_by_label(self.photo)
            ref.setdefault("matches", []).append([round(p[0] / iw, 5), round(p[1] / ih, 5)])
            n = len(ref["matches"])
            have = len(self.model_matches())
            self.show_toast("Photo point %d. %s" % (n, "Tab: back to the model to click it there" if have < n
                                                    else "%d pairs so far" % n), 3)
        else:
            st = self.new_stroke("match", p)
            st["colour"], st["rgb"] = "cyan", list(MATCH_RGB)
            st["label"] = 1 + max([m.get("label", 0) for m in self.model_matches()] or [0])
            st["ref"] = self.photo_last
            self.commit(st)
            n, k = len(self.photo_points()), st["label"]
            if self.photo_last is None:
                self.show_toast("Model point %d. Open a reference photo (click it with this tool) to pair it" % k, 4)
            elif k < n:
                self.show_toast("Model point %d. %d photo points still to pair" % (k, n - k), 3)
            elif k == n:
                self.show_toast("%d pairs. Aim for 6 or more, spread out, not all on one plane. Tab: the photo" % k, 4)
            else:
                self.show_toast("Model point %d has no photo point yet. Tab: open the photo" % k, 4)
        self.canvas.Invalidate()

    def remove_photo_point(self, p, radius):
        iw, ih = self.dims()
        pts = self.photo_points()
        near = [k for k, (u, v) in enumerate(pts) if math.hypot(u * iw - p[0], v * ih - p[1]) <= radius]
        if not near:
            return False
        self.snapshot()
        ref = self.ref_by_label(self.photo)
        ref["matches"] = [uv for k, uv in enumerate(ref["matches"]) if k not in near]
        self.canvas.Invalidate()
        return True

    def photo_pill_rect(self):
        cw = float(self.canvas.Width)
        return drawing.RectangleF(cw / 2.0 - 300, 10, 600, 40)

    def new_stroke(self, kind, p):
        name, c = COLOURS[self.colour_index]
        return {"kind": kind, "colour": name, "rgb": list(c),
                "width": WIDTHS[self.width_index][1], "points": [p]}

    def item_at(self, p):
        """The reference card or note under an image point: the marks that can be moved and resized."""
        if self.photo:
            return None
        for st in reversed(self.strokes):
            if st["kind"] == "ref":
                (cx, cy), (rw, rh) = st["points"][0], st["size"]
                if abs(p[0] - cx) <= rw / 2.0 and abs(p[1] - cy) <= rh / 2.0:
                    return st
            elif st["kind"] == "text":
                (x, y), (tw, th) = st["points"][0], st["size"]
                if x <= p[0] <= x + tw and y <= p[1] <= y + th:
                    return st
        return None

    # -- edits

    def snapshot(self):
        self.history.append(copy.deepcopy(self.strokes))
        self.future = []

    def commit(self, st):
        self.snapshot()
        self.strokes.append(st)
        self.canvas.Invalidate()

    def undo(self):
        if self.editing is not None:   # undo while typing drops the note being typed
            self.cancel_edit()
            return
        if self.history:
            self.future.append(copy.deepcopy(self.strokes))
            self.strokes = self.history.pop()
            self.canvas.Invalidate()

    def redo(self):
        if self.editing is not None:
            self.commit_edit()
        if self.future:
            self.history.append(copy.deepcopy(self.strokes))
            self.strokes = self.future.pop()
            self.canvas.Invalidate()

    def clear(self):
        self.cancel_edit()
        if self.strokes:
            self.snapshot()
            self.strokes = []
            self.canvas.Invalidate()

    # -- notes (the text tool)

    def begin_edit(self, st=None, p=None):
        """Start typing a new note at p, or edit the note st in place."""
        self.commit_edit()
        if st is None:
            st = self.new_stroke("text", p)
            st["text"] = ""
            st["font_px"] = TEXT_PX[st["width"]]
            measure_text(st)
            self.editing = {"st": st, "new": True}
        else:
            self.editing = {"st": st, "new": False, "before": copy.deepcopy(self.strokes),
                            "orig": copy.deepcopy(st)}
        self.canvas.Focus()
        self.canvas.Invalidate()

    def commit_edit(self):
        ed, self.editing = self.editing, None
        if ed is None:
            return
        st = ed["st"]
        st["text"] = (st.get("text") or "").strip("\n ")
        measure_text(st)
        if ed["new"]:
            if st["text"]:
                self.commit(st)
        elif st != ed["orig"]:
            self.history.append(ed["before"])   # one undo step for the whole edit
            self.future = []
            if not st["text"]:
                self.strokes.remove(st)
        self.canvas.Invalidate()

    def cancel_edit(self):
        ed, self.editing = self.editing, None
        if ed is None:
            return
        if not ed["new"]:
            ed["st"].clear()
            ed["st"].update(ed["orig"])
        self.canvas.Invalidate()

    def keep_inside(self, st):
        """Slide a note left or up so its tag stays inside the capture."""
        (x, y), (tw, th) = st["points"][0], st.get("size") or [0, 0]
        st["points"] = [[round(max(0.0, min(x, self.iw - tw)), 1), round(max(0.0, min(y, self.ih - th)), 1)]]

    def type_text(self, s):
        st = self.editing["st"]
        st["text"] = (st.get("text") or "") + s
        measure_text(st)
        self.keep_inside(st)
        self.canvas.Invalidate()

    # -- the eraser

    def eraser_radius(self):
        return ERASER_R[WIDTHS[self.width_index][1]]

    def erase_along(self, a, b):
        """Rub the eraser from image point a to b: cut pen lines, remove whatever else it touches."""
        r = self.eraser_radius()
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (0.4 * r)))
        centres = [[a[0] + (b[0] - a[0]) * k / float(n), a[1] + (b[1] - a[1]) * k / float(n)] for k in range(n + 1)]
        out, changed = [], False
        for st in self.strokes:
            left = erase_stroke(st, centres, r)
            if left is None:
                out.append(st)
            else:
                out.extend(left)
                changed = True
        if changed:
            if not self.erasing["changed"]:
                self.snapshot()   # one undo step per drag, taken before the first cut
                self.erasing["changed"] = True
            self.strokes = out

    def finish_live(self):
        st, self.live, self.shift_base = self.live, None, None
        if st is None:
            return
        if st["kind"] in ("arrow", "box"):
            a, b = st["points"][0], st["points"][-1]
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 4:
                self.canvas.Invalidate()
                return
        self.commit(st)

    def add_reference(self, source, p, quiet=False):
        try:
            self.ref_count += 1
            path, bmp = load_reference(source, self.work, self.ref_count)
        except Exception as ex:
            self.ref_count -= 1
            if not quiet:
                self.show_toast("Couldn't read that image (%s)" % str(ex)[:60], 4)
            return False
        label = "R%d" % self.ref_count
        self.bitmaps[label] = bmp
        bw, bh = float(bmp.Size.Width), float(bmp.Size.Height)
        k = (0.2 * self.iw) / max(bw, bh)
        size = [round(bw * k, 1), round(bh * k, 1)]
        x = min(max(p[0], size[0] / 2.0), self.iw - size[0] / 2.0)
        y = min(max(p[1], size[1] / 2.0), self.ih - size[1] / 2.0)
        source_name = source if isinstance(source, basestring) else "pasted image"
        rec = {"kind": "ref", "label": label, "file": os.path.basename(path), "source": source_name[:500],
               "points": [[round(x, 1), round(y, 1)]], "size": size}
        if isinstance(source, basestring) and os.path.isfile(source):
            # Keep the original too: full resolution and EXIF (date, lens) for photo matching and montage.
            try:
                orig = "ref%d_original%s" % (self.ref_count, os.path.splitext(source)[1].lower())
                shutil.copyfile(source, os.path.join(self.work, orig))
                rec["original"] = orig
            except Exception:
                pass
        self.commit(rec)
        self.show_toast("%s added  -  drag to move, scroll to resize, right-click to remove" % label)
        return True

    def add_dropped(self, data, p):
        """Add what was dropped or pasted: every image file, or the first readable browser image."""
        files, alts = dropped_sources(data)
        if files:
            for k, f in enumerate(files[:6]):
                self.add_reference(f, [p[0] + 40 * k, p[1] + 40 * k])
            return
        for k, src in enumerate(alts):
            if self.add_reference(src, p, quiet=k < len(alts) - 1):
                return
        if not alts:
            self.show_toast("No image found in that")

    # -- Chrome beside the window

    def open_examples(self):
        """Dock this window on the left of the screen and a Chrome window on the right."""
        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as wf
        from System.Windows.Interop import WindowInteropHelper
        me = WindowInteropHelper(self.ControlObject).Handle.ToInt64()
        area = wf.Screen.FromHandle(System.IntPtr(me)).WorkingArea
        split = int(area.Width * EXAMPLES_SPLIT)
        self.dock = (area.X + split, area.Y, area.Width - split, area.Height)
        place_window(me, area.X, area.Y, split, area.Height)
        if window_alive(self.chrome_hwnd):
            place_window(self.chrome_hwnd, *self.dock)
            ctypes, u, RECT = _win32()
            u.SetForegroundWindow(ctypes.c_void_p(self.chrome_hwnd))
            return
        chrome = find_chrome()
        if chrome is None:
            os.startfile(EXAMPLES_URL)
            self.show_toast("Chrome isn't installed, so your default browser opened instead", 4)
            return
        self.chrome_before = set(chrome_windows())
        System.Diagnostics.Process.Start(chrome, '--new-window "%s"' % EXAMPLES_URL)
        self.chrome_wait = time.time() + 10
        self.chrome_timer.Start()
        self.show_toast("Opening Chrome...", 3)

    @guarded
    def on_chrome_timer(self, sender, e):
        new = [h for h in chrome_windows() if h not in self.chrome_before]
        if new:
            self.chrome_timer.Stop()
            self.chrome_hwnd = new[0]
            place_window(self.chrome_hwnd, *self.dock)
            ctypes, u, RECT = _win32()
            u.SetForegroundWindow(ctypes.c_void_p(self.chrome_hwnd))
            self.show_toast("Find an image in Chrome, then drag it onto the spot it's for", 5)
        elif time.time() > self.chrome_wait:
            self.chrome_timer.Stop()
            self.show_toast("Chrome opened but couldn't be docked; drag images in from wherever it is", 5)

    # -- canvas events

    @guarded
    def on_paint(self, sender, e):
        g = e.Graphics
        cw, ch = float(self.canvas.Width), float(self.canvas.Height)
        g.FillRectangle(BG, 0.0, 0.0, cw, ch)
        s, ox, oy = self.fit()
        iw, ih = self.dims()
        img = self.bitmaps[self.photo] if self.photo else (self.current_option_bitmap() or self.image)
        g.FillRectangle(drawing.Color.FromArgb(0, 0, 0, 110), ox + 4, oy + 6, iw * s, ih * s)
        g.ImageInterpolation = drawing.ImageInterpolation.High if s < 1.0 else drawing.ImageInterpolation.None
        g.DrawImage(img, ox, oy, iw * s, ih * s)
        g.DrawRectangle(drawing.Pen(LINE, 1.0), ox - 0.5, oy - 0.5, iw * s + 1, ih * s + 1)
        g.AntiAlias = True
        if self.photo:
            # The reference photo, open for matching: only its match points are drawn here.
            for k, (u, v) in enumerate(self.photo_points()):
                draw_match(g, ox + u * iw * s, oy + v * ih * s, k + 1, s)
            r = self.photo_pill_rect()
            self.draw_pill(g, "Photo %s:  %d points here, %d on the model   -   Tab or Esc: back to the model" % (
                self.photo, len(self.photo_points()), len(self.model_matches())), r.X + r.Width / 2.0, r.Y + r.Height / 2.0, "match")
            self.draw_eraser(g, s)
            if self.toast:
                self.draw_pill(g, self.toast[0], cw / 2.0, oy + ih * s - 34)
            return
        if self.claude_strokes and self.show_claude:
            for st in self.claude_strokes:
                draw_stroke(g, st, s, ox, oy, self.bitmaps)
        for st in self.strokes:
            draw_stroke(g, st, s, ox, oy, self.bitmaps)
        if self.live is not None:
            draw_stroke(g, self.live, s, ox, oy, self.bitmaps)
        if self.editing is not None:
            self.draw_editing(g, s, ox, oy)
        self.draw_eraser(g, s)
        if self.options:
            self.draw_option_chips(g, cw)
        elif self.reply:
            n = sum(1 for st in self.claude_strokes if st.get("kind") == "pin") or len(self.claude_strokes)
            self.draw_pill(g, "Claude's crit of %s:  %d finding%s   -   H %s them" % (
                self.reply.get("reply_to", "?"), n, "" if n == 1 else "s", "hides" if self.show_claude else "shows"),
                cw / 2.0, 30, "crit")
        if self.drop_hover:
            g.FillRectangle(ACCENT_SOFT, ox, oy, self.iw * s, self.ih * s)
            dash = drawing.Pen(ACCENT, 2.0)
            dash.DashStyle = drawing.DashStyles.Dash
            g.DrawRectangle(dash, ox + 6, oy + 6, self.iw * s - 12, self.ih * s - 12)
            self.draw_pill(g, "Drop to add reference R%d here" % (self.ref_count + 1), cw / 2.0, oy + 40, "image")
        if self.toast:
            self.draw_pill(g, self.toast[0], cw / 2.0, oy + self.ih * s - 34)

    def current_option(self):
        return self.options[self.option_index] if self.options else None

    def current_option_bitmap(self):
        opt = self.current_option()
        return self.option_bitmaps.get(opt["label"]) if opt else None

    def flip_option(self, k):
        if self.options:
            self.option_index = k % len(self.options)
            self.canvas.Invalidate()

    def draw_option_chips(self, g, cw):
        """A row of letter chips at the top of the canvas (click to show), and the shown option's idea."""
        f = font(10.5, True)
        chip_w, chip_h, gap = 38.0, 30.0, 8.0
        total = len(self.options) * chip_w + (len(self.options) - 1) * gap
        x0, y0 = cw / 2.0 - total / 2.0, 12.0
        self.option_chips = []
        g.FillPath(drawing.Color.FromArgb(20, 21, 24, 215), round_rect(x0 - 10, y0 - 6, total + 20, chip_h + 12, 12))
        for i, opt in enumerate(self.options):
            r = drawing.RectangleF(x0 + i * (chip_w + gap), y0, chip_w, chip_h)
            on = i == self.option_index
            g.FillPath(ACCENT if on else PANEL_HI, round_rect(r.X, r.Y, r.Width, r.Height, 8))
            text_centered(g, f, WHITE if on else TEXT, r.X + r.Width / 2.0, r.Y + r.Height / 2.0, opt["label"])
            self.option_chips.append((r, i))
        if not self.toast:   # the caption sits at the bottom, clear of the sketch's own title block
            opt = self.current_option()
            caption = "%s  %s  -  arrow keys flip  -  Send picks the option showing" % (opt["label"], opt.get("title", ""))
            self.draw_pill(g, caption, cw / 2.0, float(self.canvas.Height) - 30)

    def draw_eraser(self, g, s):
        if self.tool == "eraser" and self.mouse_in:
            rad = self.eraser_radius() * s
            mx, my = self.mouse.X, self.mouse.Y
            g.DrawEllipse(pen(drawing.Color.FromArgb(0, 0, 0, 120), 3.0), mx - rad, my - rad, 2 * rad, 2 * rad)
            g.DrawEllipse(pen(WHITE, 1.5), mx - rad, my - rad, 2 * rad, 2 * rad)

    def draw_editing(self, g, s, ox, oy):
        """The note being typed: placeholder when empty, a caret after the last character, a dashed frame."""
        st = self.editing["st"]
        empty = not st.get("text")
        shown = dict(st, text="Type a note") if empty else st
        if empty:
            shown["rgb"] = [142, 145, 151]
        if self.editing["new"] or empty:
            draw_stroke(g, shown, s, ox, oy)
        f, lines, lh, tw, th, padx, pady = text_layout(g, shown, s)
        x, y = ox + st["points"][0][0] * s, oy + st["points"][0][1] * s
        last = "" if empty else lines[-1]
        cx = x + padx + (g.MeasureString(f, last).Width if last else 0.0) + 1.0
        cy = y + pady + (0 if empty else (len(lines) - 1) * lh)
        g.DrawLine(pen(_colour(st), max(1.5, 2.0 * s), False), cx, cy + 0.12 * lh, cx, cy + 0.88 * lh)
        dash = drawing.Pen(ACCENT, 1.0)
        dash.DashStyle = drawing.DashStyles.Dash
        g.DrawRectangle(dash, x - 3, y - 3, tw + 2 * padx + 6, th + 2 * pady + 6)

    def draw_pill(self, g, text, cx, cy, icon=None):
        f = font(10.0, True)
        size = g.MeasureString(f, text)
        gap = 24 if icon else 0
        w, h = size.Width + 32 + gap, size.Height + 16
        g.FillPath(drawing.Color.FromArgb(0, 0, 0, 90), round_rect(cx - w / 2.0 + 2, cy - h / 2.0 + 3, w, h, h / 2.0))
        g.FillPath(PANEL, round_rect(cx - w / 2.0, cy - h / 2.0, w, h, h / 2.0))
        g.DrawPath(pen(LINE, 1.0), round_rect(cx - w / 2.0, cy - h / 2.0, w, h, h / 2.0))
        if icon:
            draw_icon(g, icon, cx - w / 2.0 + 26, cy, ACCENT)
        g.DrawText(f, drawing.SolidBrush(TEXT), cx - w / 2.0 + 16 + gap, cy - size.Height / 2.0, text)

    @guarded
    def on_down(self, sender, e):
        self.canvas.Focus()
        if self.live is not None:
            self.finish_live()
        p = self.to_image(e.Location)
        if self.options and e.Buttons == forms.MouseButtons.Primary:
            for r, i in self.option_chips:
                if r.Contains(e.Location):
                    self.flip_option(i)
                    return
        if self.photo:
            if self.photo_pill_rect().Contains(e.Location):
                self.close_photo()
            elif e.Buttons == forms.MouseButtons.Alternate:
                if not self.remove_photo_point(p, 25.0 / max(self.fit()[0], 0.05)):
                    self.undo()
            elif e.Buttons == forms.MouseButtons.Primary:
                if self.tool == "eraser":
                    self.remove_photo_point(p, self.eraser_radius())
                elif self.tool == "match":
                    self.add_match(p)
                else:
                    self.show_toast("On the photo, use Photo match (M). Tab or Esc: back to the model", 3)
            return
        if self.editing is not None:
            if self.item_at(p) is self.editing["st"] and e.Buttons == forms.MouseButtons.Primary:
                return   # clicking inside the note being typed keeps typing
            self.commit_edit()
        if self.tool == "eraser" and e.Buttons == forms.MouseButtons.Primary:
            self.erasing = {"last": p, "changed": False}
            self.erase_along(p, p)
            self.canvas.Invalidate()
            return
        item = self.item_at(p)
        if e.Buttons == forms.MouseButtons.Alternate:
            if item is not None:
                self.snapshot()
                self.strokes.remove(item)
                self.canvas.Invalidate()
            else:
                self.undo()
            return
        if e.Buttons != forms.MouseButtons.Primary:
            return
        if item is not None and item["kind"] == "ref" and self.tool == "match":
            self.open_photo(item["label"])   # with Photo match, clicking a reference opens it to match
            return
        if item is not None:
            # Snapshot on the first real movement, so a plain click leaves no empty undo step.
            c = item["points"][0]
            self.moving = {"item": item, "dx": c[0] - p[0], "dy": c[1] - p[1], "from": p, "moved": False}
            return
        if self.tool == "text":
            self.begin_edit(p=p)
            return
        if self.tool == "match":
            self.add_match(p)
            return
        if self.tool == "pin":
            st = self.new_stroke("pin", p)
            # Next number after the highest, not the count: after an erase, a count would repeat a label.
            st["label"] = 1 + max([x.get("label", 0) for x in self.strokes if x["kind"] == "pin"] or [0])
            self.commit(st)
            return
        self.live = self.new_stroke(self.tool, p)
        if self.tool in ("arrow", "box"):
            self.live["points"] = [p, p]
        self.canvas.Invalidate()

    @guarded
    def on_move(self, sender, e):
        p = self.to_image(e.Location)
        self.mouse = e.Location
        self.mouse_in = True
        shift = (e.Modifiers & forms.Keys.Shift) == forms.Keys.Shift
        if self.erasing is not None:
            if e.Buttons == forms.MouseButtons.Primary:
                self.erase_along(self.erasing["last"], p)
                self.erasing["last"] = p
            else:
                self.erasing = None
            self.canvas.Invalidate()
            return
        if self.moving is not None:
            m = self.moving
            if not m["moved"]:
                if math.hypot(p[0] - m["from"][0], p[1] - m["from"][1]) < 3:
                    return
                self.snapshot()
                m["moved"] = True
            m["item"]["points"] = [[round(p[0] + m["dx"], 1), round(p[1] + m["dy"], 1)]]
            self.canvas.Invalidate()
            return
        if self.live is None:
            if self.tool == "eraser":
                want = forms.Cursors.Default
                self.canvas.Invalidate()   # the eraser circle follows the mouse
            elif self.item_at(p) is not None:
                want = forms.Cursors.Move
            else:
                want = forms.Cursors.IBeam if self.tool == "text" else forms.Cursors.Crosshair
            if self.canvas.Cursor != want:
                self.canvas.Cursor = want
            return
        if e.Buttons != forms.MouseButtons.Primary:
            self.finish_live()
            return
        pts = self.live["points"]
        kind = self.live["kind"]
        if kind == "pen":
            if shift:
                if self.shift_base is None:
                    self.shift_base = list(pts)
                self.live["points"] = self.shift_base + [p]
            else:
                if self.shift_base is not None:
                    self.shift_base = None
                if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) >= 1.5:
                    pts.append(p)
        elif kind == "arrow":
            a = pts[0]
            if shift:
                ang = math.atan2(p[1] - a[1], p[0] - a[0])
                ang = round(ang / (math.pi / 12)) * (math.pi / 12)
                d = math.hypot(p[0] - a[0], p[1] - a[1])
                p = [round(a[0] + d * math.cos(ang), 1), round(a[1] + d * math.sin(ang), 1)]
            self.live["points"] = [a, p]
        elif kind == "box":
            a = pts[0]
            if shift:
                side = max(abs(p[0] - a[0]), abs(p[1] - a[1]))
                p = [a[0] + math.copysign(side, p[0] - a[0] or 1), a[1] + math.copysign(side, p[1] - a[1] or 1)]
            self.live["points"] = [a, p]
        self.canvas.Invalidate()

    @guarded
    def on_up(self, sender, e):
        if self.erasing is not None:
            self.erasing = None
            return
        if self.moving is not None:
            m, self.moving = self.moving, None
            if not m["moved"] and m["item"]["kind"] == "text" and self.tool == "text":
                self.begin_edit(m["item"])   # with the text tool, a click on a note edits it
            return
        if self.live is not None:
            self.finish_live()

    @guarded
    def on_leave(self, sender, e):
        self.mouse_in = False
        if self.tool == "eraser":
            self.canvas.Invalidate()

    @guarded
    def on_double(self, sender, e):
        item = self.item_at(self.to_image(e.Location))
        if item is not None and item["kind"] == "text":
            self.moving = None
            self.begin_edit(item)
        elif item is not None and item["kind"] == "ref":
            self.moving = None
            self.open_photo(item["label"])

    @guarded
    def on_wheel(self, sender, e):
        p = self.to_image(e.Location)
        item = self.item_at(p)
        if item is None:
            return
        if self.last_wheel[0] is not item or time.time() - self.last_wheel[1] > 0.8:
            if self.editing is None or item is not self.editing["st"]:
                self.snapshot()
        self.last_wheel = (item, time.time())
        k = 1.1 ** (1 if e.Delta.Height > 0 else -1)
        if item["kind"] == "text":
            if 12 <= item["font_px"] * k <= 0.25 * self.ih:
                item["font_px"] = round(item["font_px"] * k, 1)
                measure_text(item)
        else:
            w, h = item["size"]
            if 40 <= min(w, h) * k and max(w, h) * k <= 0.9 * max(self.iw, self.ih):
                item["size"] = [round(w * k, 1), round(h * k, 1)]
        self.canvas.Invalidate()
        e.Handled = True

    @guarded
    def on_drag_over(self, sender, e):
        files, alts = dropped_sources(e.Data)
        if files or alts:
            e.Effects = forms.DragEffects.Copy
            if not self.drop_hover:
                self.drop_hover = True
                self.canvas.Invalidate()
        else:
            e.Effects = forms.DragEffects.None

    @guarded
    def on_drag_leave(self, sender, e):
        self.drop_hover = False
        self.canvas.Invalidate()

    @guarded
    def on_drop(self, sender, e):
        self.drop_hover = False
        self.canvas.Invalidate()
        self.add_dropped(e.Data, self.to_image(e.Location))
        self.canvas.Focus()

    def paste_reference(self):
        p = self.to_image(self.mouse) if self.mouse.X > 0 else [self.iw / 2.0, self.ih / 2.0]
        self.add_dropped(forms.Clipboard(), p)

    @guarded
    def on_key(self, sender, e):
        k = e.Key
        if self.editing is not None:
            # Typing a note: characters arrive in on_text_input. Esc is left to the dialog, which cancels.
            if e.IsChar:
                return
            if k == forms.Keys.Enter and e.Shift:
                self.type_text("\n")
            elif k == forms.Keys.Enter:
                self.commit_edit()
            elif k == forms.Keys.Backspace:
                st = self.editing["st"]
                st["text"] = (st.get("text") or "")[:-1]
                measure_text(st)
                self.canvas.Invalidate()
            elif e.Control and k == forms.Keys.V:
                pasted = forms.Clipboard().Text or ""
                self.type_text(pasted.replace("\r\n", "\n").replace("\r", "\n"))
            else:
                return
            e.Handled = True
            return
        tools = {forms.Keys.P: "pen", forms.Keys.B: "box", forms.Keys.A: "arrow", forms.Keys.N: "pin",
                 forms.Keys.T: "text", forms.Keys.M: "match", forms.Keys.E: "eraser"}
        digits = [forms.Keys.D1, forms.Keys.D2, forms.Keys.D3, forms.Keys.D4]
        if self.options and k in (forms.Keys.Left, forms.Keys.Right):
            self.flip_option(self.option_index + (1 if k == forms.Keys.Right else -1))
        elif k == forms.Keys.Tab and (self.photo or self.photo_last):
            if self.photo:
                self.close_photo()
            else:
                self.open_photo(self.photo_last)
        elif k == forms.Keys.H and not e.Control and self.claude_strokes:
            self.show_claude = not self.show_claude
            self.canvas.Invalidate()
        elif e.Control and k == forms.Keys.V:
            self.paste_reference()
        elif e.Control and k == forms.Keys.Delete:
            self.clear()
        elif k == forms.Keys.Delete:
            item = self.item_at(self.to_image(self.mouse))
            if item is not None:
                self.snapshot()
                self.strokes.remove(item)
                self.canvas.Invalidate()
        elif k in tools and not e.Control:
            self.set_tool(tools[k])
        elif k in digits:
            self.colour_index = digits.index(k)
            self.toolbar.d.Invalidate()
        elif k == forms.Keys.Enter:
            self.on_send()
        else:
            return
        e.Handled = True

    @guarded
    def on_text_input(self, sender, e):
        if self.editing is None:
            return
        typed = "".join(ch for ch in (e.Text or "") if ch >= " " and ch != "\x7f")
        if typed:
            self.type_text(typed)
        e.Cancel = True

    @guarded
    def on_canvas_blur(self, sender, e):
        # Clicking into the request box (or anywhere off the canvas) finishes the note.
        self.commit_edit()

    @guarded
    def on_note_key(self, sender, e):
        if e.Key == forms.Keys.Enter and not e.Shift:
            e.Handled = True
            self.on_send()

    @guarded
    def on_dialog_key(self, sender, e):
        if e.Control and e.Key == forms.Keys.Z and not self.note.HasFocus:
            self.undo()
        elif e.Control and e.Key == forms.Keys.Y and not self.note.HasFocus:
            self.redo()
        elif e.Control and e.Key == forms.Keys.S:
            self.on_save()
        elif e.Control and e.Key == forms.Keys.F:
            self.open_examples()
        elif e.Key == forms.Keys.Escape:
            if self.editing is not None:
                self.cancel_edit()   # Esc while typing a note cancels the note, not the capture
            elif self.photo:
                self.close_photo()   # Esc on the open photo goes back to the model
            else:
                self.Close(False)
        else:
            return
        e.Handled = True

    # -- save and send

    def on_save(self):
        d = forms.SaveFileDialog()
        d.Title = "Save the marked image"
        d.Filters.Add(forms.FileFilter("PNG image", ".png"))
        folder = self.settings.get("save_dir") or os.path.join(
            System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyPictures), "ClaudeMark")
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except Exception:
                pass
        try:
            d.Directory = System.Uri(folder)
        except Exception:
            pass
        d.FileName = "ClaudeMark %s.png" % datetime.datetime.now().strftime("%Y-%m-%d %H%M%S")
        self.commit_edit()
        if d.ShowDialog(self) != forms.DialogResult.Ok:
            return
        path = d.FileName
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            compose(self.clean_path, self.strokes, path, int(self.iw), int(self.ih), self.bitmaps)
            self.settings["save_dir"] = os.path.dirname(path)
            save_settings(self.settings)
            self.show_toast("Saved  %s" % os.path.basename(path), 3)
        except Exception as ex:
            self.show_toast("Save failed (%s)" % str(ex)[:60], 4)

    def on_send(self):
        if self.live is not None:
            self.finish_live()
        self.commit_edit()
        self.note_text = self.note.Text or ""
        self.settings.update({"tool": self.tool, "colour": self.colour_index,
                              "width": self.width_index, "jump": self.paste,
                              "task": self.task, "presets": list(self.selected)})
        self.Close(True)


# ---------------------------------------------------------------- terminal

SENDKEYS_SPECIAL = set("+^%~(){}[]")


def prompt_text(note):
    """The note as safe prompt text: one line, no leading command characters, no @ mentions."""
    text = " ".join((note or "").replace("@", " at ").split())
    if text[:1] in ("/", "!", "#"):
        text = "Note: " + text
    return text


def sendkeys_escape(text):
    return "".join("{%s}" % ch if ch in SENDKEYS_SPECIAL else ch for ch in text)


def set_clipboard_image(png_path):
    """Put the marked image on the clipboard as a plain Windows bitmap (CF_DIB)."""
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import Clipboard
    img = System.Drawing.Bitmap(png_path)
    try:
        Clipboard.SetImage(img)
    finally:
        img.Dispose()


def paste_into_claude(note, png_path):
    """Bring Windows Terminal forward and put the note and the image into Claude Code's prompt.

    Keys are sent only once the terminal is confirmed as the foreground window, so nothing is
    ever typed into the wrong program. Claude Code pastes images with Alt+V on Windows (Ctrl+V
    elsewhere). The prompt is left unsent: the user reads it, adds to it, presses Enter.
    """
    import ctypes
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import SendKeys
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    for p in System.Diagnostics.Process.GetProcessesByName("WindowsTerminal"):
        h = p.MainWindowHandle
        if h == System.IntPtr.Zero:
            continue
        target = h.ToInt64()
        hwnd = ctypes.c_void_p(target)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        for _ in range(40):
            if (user32.GetForegroundWindow() or 0) == target:
                break
            time.sleep(0.05)
        else:
            return "the terminal did not come forward, so nothing was typed"
        time.sleep(0.2)
        set_clipboard_image(png_path)
        text = prompt_text(note)
        if text:
            SendKeys.SendWait(sendkeys_escape(text + " "))
        SendKeys.SendWait("%v")
        return "pasted into Claude Code; check it and press Enter there"
    return "Windows Terminal isn't running"


# ---------------------------------------------------------------- command

def preset_tag(task, presets):
    """'[Lighting: Golden hour, Sunset HDRI | Render: Final 4K]' for the front of the typed prompt."""
    labels = dict((c, l) for c, l, _ in CATEGORIES)
    parts = []
    for cat, _, _ in CATEGORIES:
        names = [p["name"] for p in presets if p["category"] == cat]
        if names:
            parts.append("%s: %s" % (labels[cat], ", ".join(names)))
    if not parts:
        return "" if task == "model" else "[%s]" % labels[task]
    return "[%s]" % " | ".join(parts)


def send(doc, view, work, meta, strokes, note, w, h, bitmaps, task="model", presets=(), under=(), camera=None):
    """Write marked.png and meta.json, then publish the folder to the inbox. Returns its path.

    `under` are marks drawn beneath the user’s (Claude's crit, when answering a reply). `camera` is a saved
    camera to pick with instead of the live view (a reply's capture camera)."""
    meta["task"] = task
    meta["presets"] = list(presets)
    compose(os.path.join(work, "clean.png"), list(under) + list(strokes), os.path.join(work, "marked.png"), w, h, bitmaps)
    used = set(st["file"] for st in strokes if st["kind"] == "ref")
    used |= set(st["original"] for st in strokes if st["kind"] == "ref" and st.get("original"))
    for f in os.listdir(work):
        if f.startswith("ref") and f not in used:
            try:
                os.remove(os.path.join(work, f))
            except Exception:
                pass
    meta["note"] = note
    meta["strokes"] = strokes
    meta["picks"] = pick_hints(doc, view, strokes, camera)
    with open(os.path.join(work, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    final = os.path.join(INBOX, os.path.basename(work).lstrip("."))
    os.rename(work, final)
    try:
        set_clipboard_image(os.path.join(final, "marked.png"))
    except Exception:
        pass
    return final


def main():
    doc = sc.doc
    view = doc.Views.ActiveView
    if view is None:
        print("ClaudeMark: no active view.")
        return
    vp = view.ActiveViewport
    w, h = vp.Size.Width, vp.Size.Height
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    work = os.path.join(INBOX, "." + stamp)
    if not os.path.isdir(work):
        os.makedirs(work)

    bmp = view.CaptureToBitmap(System.Drawing.Size(w, h))
    if bmp is None:
        print("ClaudeMark: the view capture failed.")
        shutil.rmtree(work, True)
        return
    bmp.Save(os.path.join(work, "clean.png"), System.Drawing.Imaging.ImageFormat.Png)
    meta = gather_meta(doc, view, w, h)

    settings = load_settings()
    info = "%s   |   %s   |   %s   |   %d x %d" % (doc.Name or "Untitled", vp.Name,
                                                  vp.DisplayMode.EnglishName, w, h)
    dlg = MarkupDialog(work, w, h, info, settings)
    ok = dlg.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    dlg.chrome_timer.Stop()
    if not ok:
        dlg.image.Dispose()
        shutil.rmtree(work, True)
        print("ClaudeMark: cancelled.")
        return

    presets = dlg.chosen_presets()
    final = send(doc, view, work, meta, dlg.strokes, dlg.note_text, w, h, dlg.bitmaps, dlg.task, presets)
    save_settings(dlg.settings)
    status = "type your request in Claude Code; the next prompt picks it up"
    if dlg.settings.get("jump"):
        try:
            typed = (preset_tag(dlg.task, presets) + " " + (dlg.note_text or "")).strip()
            status = paste_into_claude(typed, os.path.join(final, "marked.png"))
        except Exception as ex:
            status = "paste failed (%s); the next prompt still picks it up" % ex
    print("ClaudeMark: sent %s (%d marks): %s." % (os.path.basename(final), len(dlg.strokes), status))


def pending_reply():
    """The newest unanswered reply in outbox/: (folder, the capture it answers) or None."""
    if not os.path.isdir(OUTBOX):
        return None
    names = sorted(n for n in os.listdir(OUTBOX)
                   if not n.startswith(".") and os.path.isfile(os.path.join(OUTBOX, n, "reply.json")))
    if not names:
        return None
    folder = os.path.join(OUTBOX, names[-1])
    try:
        with open(os.path.join(folder, "reply.json")) as f:
            return folder, json.load(f).get("reply_to", "?")
    except Exception:
        return None


def main_reply():
    """ClaudeReply: open Claude's newest reply (its crit drawn on the user’s capture) so the user can answer on top."""
    pend = pending_reply()
    if pend is None:
        print("ClaudeMark: no reply from Claude is waiting.")
        return
    folder = pend[0]
    with open(os.path.join(folder, "reply.json")) as f:
        reply = json.load(f)
    reply["_folder"] = folder
    base = reply.get("meta") or {}
    w, h = base["view"]["size"]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    work = os.path.join(INBOX, "." + stamp)
    if not os.path.isdir(work):
        os.makedirs(work)
    shutil.copyfile(os.path.join(folder, "clean.png"), os.path.join(work, "clean.png"))
    settings = load_settings()
    is_options = reply.get("kind") == "options"
    info = "%s %s   |   %s" % ("Options for" if is_options else "Reply to", reply.get("reply_to", "?"),
                               reply.get("title") or ("Options" if is_options else "Claude's crit"))
    dlg = MarkupDialog(work, w, h, info, settings, reply=reply)
    ok = dlg.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    dlg.chrome_timer.Stop()
    if not ok:
        dlg.image.Dispose()
        shutil.rmtree(work, True)
        print("ClaudeMark: reply left for later; ClaudeReply opens it again.")
        return
    doc = sc.doc
    meta = copy.deepcopy(base)
    meta.update({
        "tool": "ClaudeMark", "version": 3,
        "captured": datetime.datetime.now().isoformat(), "captured_unix": time.time(),
        "rhino_pid": System.Diagnostics.Process.GetCurrentProcess().Id,
        "reply_to": reply.get("reply_to"), "reply_id": os.path.basename(folder),
        "reply_title": reply.get("title", ""), "claude_strokes": dlg.claude_strokes,
        "selection": [], "subobject_selection": [], "history_tail": history_tail(),
    })
    meta["doc"] = dict(meta.get("doc") or {}, path=doc.Path or "", name=doc.Name or "Untitled",
                       modified=doc.Modified, object_count=doc.Objects.Count)
    if dlg.options:
        # The user picked the option showing when the user sent: the user’s marks are drawn on that option's sketch.
        opt = dlg.current_option()
        meta["chosen_option"] = dict((k, opt.get(k)) for k in ("label", "title", "idea", "settings"))
        meta["options"] = [dict((k, o.get(k)) for k in ("label", "title", "idea")) for o in dlg.options]
        if reply.get("mode") == "style":
            meta["options_mode"] = "style"
            meta["styles_file"] = reply.get("styles")
            meta["styles_previews"] = reply.get("previews")
        try:
            shutil.copyfile(os.path.join(folder, opt["image"]), os.path.join(work, "clean.png"))
        except Exception:
            pass
    presets = dlg.chosen_presets()
    final = send(doc, doc.Views.ActiveView, work, meta, dlg.strokes, dlg.note_text, w, h, dlg.bitmaps,
                 dlg.task, presets, under=dlg.claude_strokes, camera=base.get("camera"))
    save_settings(dlg.settings)
    try:
        answered = os.path.join(OUTBOX, "answered")
        if not os.path.isdir(answered):
            os.makedirs(answered)
        shutil.move(folder, os.path.join(answered, os.path.basename(folder)))
    except Exception:
        pass
    status = "type your answer in Claude Code; the next prompt picks it up"
    if dlg.settings.get("jump"):
        try:
            word = "Style" if reply.get("mode") == "style" else "Option"
            tag = "[%s %s]" % (word, dlg.current_option()["label"]) if dlg.options else "[Reply to Claude]"
            typed = (tag + " " + (dlg.note_text or "")).strip()
            status = paste_into_claude(typed, os.path.join(final, "marked.png"))
        except Exception as ex:
            status = "paste failed (%s); the next prompt still picks it up" % ex
    print("ClaudeMark: answered %s (%d marks): %s." % (reply.get("reply_to", "?"), len(dlg.strokes), status))


if not globals().get("CLAUDEMARK_NO_UI"):
    if globals().get("CLAUDEMARK_MODE") == "reply":
        main_reply()
    else:
        main()
