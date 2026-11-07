"""UserPromptSubmit hook: attach pending ClaudeMark captures to the prompt being sent.

ClaudeMark (the Rhino command, claude_mark.py in the folder above this one) publishes each capture as
inbox/<stamp>/ with clean.png, marked.png and meta.json. On the next prompt typed in any
Claude Code session, this moves every pending capture to sent/ (or stale/ if it is older than
MAX_AGE_S) and injects a short description with the file paths as additional context.

Prompts starting with "/" (slash commands) or "<" (system-generated) leave the inbox alone.
Must run synchronously: an async hook's context arrives too late for the prompt.
"""
import datetime
import json
import os
import shutil
import sys
import time

# CLAUDEMARK_ROOT points a test run at a scratch copy; never pipe-test against the live inbox.
# The kit is the folder above hook/.
ROOT = os.environ.get("CLAUDEMARK_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "inbox")
SENT = os.path.join(ROOT, "sent")
STALE = os.path.join(ROOT, "stale")
MAX_AGE_S = 6 * 3600

GUIDE = (
    "[ClaudeMark] The user marked up a Rhino view with the ClaudeMark command and sent it with "
    "this prompt. Look at the marked image before acting: it is usually also pasted inline in the "
    "prompt, and the user's note typed in front of it. meta.json holds the camera recorded at the "
    "moment of capture (world_to_screen 4x4 row-major, frustum, location/target/up/lens; image "
    "px = viewport px, origin top-left, y down), every stroke in image px, Rhino's hits under "
    "pins, arrow ends and stroke samples, the selection, active clip planes and the "
    "command-history tail. Map pixels to 3D with this saved camera, not the live view, which "
    "has probably moved since. Strokes are markup, not geometry. Boxes, ellipses, clouds and pins carry "
    "numbers (label 1, 2...) so the user’s note can say 'fix 2 and 4'; a mark's comment is the user’s instruction for "
    "that mark. A measure is a real distance Rhino took between the model points under its two ends. "
    "A reference (R1, R2...) is an "
    "image the user dropped onto the spot it applies to, e.g. the type of window wanted there: open its "
    "full image before designing, and treat what lies under the card as the place. A text note is "
    "the user’s instruction written on the spot it applies to: read it with what lies under it. Photo match "
    "points pair a site photo (u, v as fractions of its size) with model pixels by number, for a camera "
    "solve. A capture marked as a reply answers your own crit: C1, C2... are your marks, drawn in orange, "
    "and the user’s marks answer the nearest ones. Presets are the user’s "
    "standing instructions for this kind of task (materials, lighting, render...): follow them unless "
    "the user’s note says otherwise, and use the files they name. Check meta rhino_pid and "
    "doc.path against the Rhino the MCP is driving before modelling. If the prompt is clearly "
    "not about this capture, say that it arrived rather than acting on it. Schema: "
    + os.path.join(ROOT, "README.md")
)


def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % round(seconds / 60.0)
    return "%.1f h" % (seconds / 3600.0)


def fmt_hit(hit):
    if not hit:
        return "nothing"
    if "error" in hit:
        return "pick error"
    name = " '%s'" % hit["name"] if hit.get("name") else ""
    pt = hit.get("point") or []
    where = " at (%s)" % ", ".join("%.2f" % v for v in pt) if pt else ""
    return "%s%s on %s%s [%s]" % (hit.get("type", "?"), name, hit.get("layer") or "?",
                                  where, (hit.get("id") or "")[:8])


def key_point(st):
    """The one pixel that stands for a mark, to relate the user’s marks to Claude's."""
    pts = st.get("points") or [[0, 0]]
    kind = st.get("kind")
    if kind == "text":
        w, h = st.get("size") or [0, 0]
        return pts[0][0] + w / 2.0, pts[0][1] + h / 2.0
    if kind == "arrow":
        return pts[-1][0], pts[-1][1]
    if kind in ("box", "pen", "ellipse", "cloud", "highlight", "measure"):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return pts[0][0], pts[0][1]


def said(st):
    """The user’s comment on a mark, as a suffix."""
    c = (st.get("comment") or "").strip()
    return ('; the user’s comment: "%s"' % " / ".join(ln.strip() for ln in c.splitlines() if ln.strip())) if c else ""


def near_claude(st, claude, limit=90.0):
    claude = [c for c in claude or [] if c.get("label")]   # only Claude's numbered findings (C1, C2...)
    if not claude:
        return ""
    x, y = key_point(st)
    best = min(claude, key=lambda c: (key_point(c)[0] - x) ** 2 + (key_point(c)[1] - y) ** 2)
    cx, cy = key_point(best)
    if ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5 > limit:
        return ""
    return " (near your %s)" % (best.get("label") or best.get("kind"))


def match_lines(meta, folder, picks):
    """Photo-match pairs, grouped by the photo they pair with."""
    strokes = meta.get("strokes") or []
    refs = dict((st.get("label"), st) for st in strokes if st.get("kind") == "ref")
    groups = {}
    for i, st in enumerate(strokes):
        if st.get("kind") == "match":
            groups.setdefault(st.get("ref"), []).append((i, st))
    lines = []
    for label, ms in sorted(groups.items(), key=lambda kv: str(kv[0])):
        ref = refs.get(label) or {}
        uvs = ref.get("matches") or []
        orig = ref.get("original")
        lines.append("  photo match with %s: %d points on the photo, %d on the model, paired by number%s" % (
            label or "(no photo opened)", len(uvs), len(ms),
            ("; full-resolution original: " + os.path.join(folder, orig)) if orig else ""))
        for i, st in sorted(ms, key=lambda m: m[1].get("label", 0))[:16]:
            k = st.get("label", 0)
            uv = uvs[k - 1] if 0 < k <= len(uvs) else None
            hit = next((p.get("hit") for p in picks if p.get("stroke") == i), None)
            lines.append("    %d: photo %s <-> model px (%.0f, %.0f) -> %s" % (
                k, "(%.4f, %.4f)" % tuple(uv) if uv else "(none yet)", st["points"][0][0], st["points"][0][1],
                fmt_hit(hit)))
    return lines


def stroke_lines(meta, folder):
    strokes = meta.get("strokes") or []
    picks = meta.get("picks") or []
    claude = meta.get("claude_strokes") or []
    lines = []
    pen_n = arrow_n = box_n = text_n = 0
    shown = [(i, st) for i, st in enumerate(strokes) if st.get("kind") != "match"]
    for i, st in shown[:12]:
        before = len(lines)
        mine = [p for p in picks if p.get("stroke") == i]
        by_role = {}
        for p in mine:
            by_role.setdefault(p.get("role"), []).append(p.get("hit"))
        kind = st.get("kind")
        colour = st.get("colour", "")
        if kind == "pin":
            lines.append("  pin %s (%s): %s%s" % (st.get("label"), colour, fmt_hit((by_role.get("pin") or [None])[0]), said(st)))
        elif kind == "ref":
            src = st.get("source") or ""
            lines.append("  reference %s (%s), placed over: %s; full image: %s%s" % (
                st.get("label"), os.path.basename(src) or src, fmt_hit((by_role.get("ref") or [None])[0]),
                os.path.join(folder, st.get("file", "")), said(st)))
        elif kind in ("box", "ellipse", "cloud"):
            box_n += 1
            name = {"box": "box", "ellipse": "ellipse", "cloud": "revision cloud"}[kind]
            num = st.get("label") if st.get("label") is not None else box_n
            edge_role = "edge" if kind == "ellipse" else "corner"
            edges = [fmt_hit(h) for h in by_role.get(edge_role, []) if h]
            lines.append("  %s %s (%s): centre -> %s%s%s" % (
                name, num, colour, fmt_hit((by_role.get("centre") or [None])[0]),
                ("; %ss -> " % edge_role + "; ".join(edges[:4])) if edges else "", said(st)))
        elif kind == "measure":
            m = st.get("measure") or {}
            if m.get("method") in ("surface", "plane"):
                how = "on the model" if m.get("method") == "surface" else "on the view plane through the camera target"
                lines.append("  measure (%s): %s %s, from %s to %s" % (
                    colour, m.get("text", "?"), how, fmt_hit((by_role.get("start") or [None])[0]),
                    fmt_hit((by_role.get("end") or [None])[0])) + said(st))
            else:
                lines.append("  measure (%s): no distance (an end missed the model); from %s to %s%s" % (
                    colour, fmt_hit((by_role.get("start") or [None])[0]), fmt_hit((by_role.get("end") or [None])[0]), said(st)))
        elif kind == "text":
            text_n += 1
            words = " / ".join(ln.strip() for ln in (st.get("text") or "").splitlines() if ln.strip())
            lines.append('  text %d (%s): "%s", over: %s; where it starts: %s' % (
                text_n, colour, words, fmt_hit((by_role.get("centre") or [None])[0]),
                fmt_hit((by_role.get("anchor") or [None])[0])))
        elif kind == "arrow":
            arrow_n += 1
            lines.append("  arrow %d (%s): tip -> %s; tail -> %s%s" % (
                arrow_n, colour, fmt_hit((by_role.get("tip") or [None])[0]),
                fmt_hit((by_role.get("tail") or [None])[0]), said(st)))
        else:
            pen_n += 1
            shape = "closed loop" if st.get("closed") else "line"
            if kind == "highlight":
                shape = "highlight, " + ("closed" if st.get("closed") else "a stroke")
            parts = []
            if "centre" in by_role:
                parts.append("centre -> %s" % fmt_hit(by_role["centre"][0]))
            seen = {}
            for role in ("start", "end", "along"):
                for hit in by_role.get(role, []):
                    if hit and hit.get("id"):
                        seen.setdefault(hit["id"], hit)
            if seen:
                parts.append("under the stroke -> " + "; ".join(fmt_hit(h) for h in list(seen.values())[:3]))
            elif not parts:
                parts.append("nothing under it")
            lines.append("  pen %d (%s, %s): %s%s" % (pen_n, colour, shape, ", ".join(parts), said(st)))
        if len(lines) > before:
            lines[-1] += near_claude(st, claude)
    if len(shown) > 12:
        lines.append("  ... %d more marks in meta.json" % (len(shown) - 12))
    lines.extend(match_lines(meta, folder, picks))
    return lines


def describe(meta, folder, age):
    doc = meta.get("doc") or {}
    view = meta.get("view") or {}
    cam = meta.get("camera") or {}
    size = view.get("size") or ["?", "?"]
    lens = ("%s mm" % cam.get("lens_35mm")) if cam.get("projection") == "perspective" else "parallel"
    when = meta.get("captured", "")[11:19]
    lines = ["ClaudeMark capture %s: %s (%s), view '%s' %s, %s, %sx%s px, captured %s (%s ago)." % (
        os.path.basename(folder), doc.get("name", "?"), doc.get("path") or "unsaved",
        view.get("name", "?"), view.get("display_mode", "?"), lens, size[0], size[1],
        when, fmt_age(age))]
    if meta.get("chosen_option") and meta.get("options_mode") == "style":
        ch = meta["chosen_option"]
        others = ", ".join("%s %s" % (o.get("label"), o.get("title", "")) for o in meta.get("options") or []
                           if o.get("label") != ch.get("label"))
        lines.append('  this PICKS style %s ("%s": %s) from your render-style board for %s; the user’s marks are drawn on that '
                     "style's V-Ray preview. Not picked: %s. Set the picked style up for real with the user’s tweaks "
                     '(tools/TOOLS.md "Render styles", step 7): styles %s, previews and grades %s.' % (
                         ch.get("label"), ch.get("title", ""), ch.get("settings") or ch.get("idea", ""),
                         meta.get("reply_to"), others or "none", meta.get("styles_file"), meta.get("styles_previews")))
    elif meta.get("chosen_option"):
        ch = meta["chosen_option"]
        others = ", ".join("%s %s" % (o.get("label"), o.get("title", "")) for o in meta.get("options") or []
                           if o.get("label") != ch.get("label"))
        lines.append('  this PICKS option %s ("%s": %s) from your options board for %s; the user’s marks are drawn on that '
                     'option\'s sketch. Not picked: %s. Model only the picked option, with the user’s changes.' % (
                         ch.get("label"), ch.get("title", ""), ch.get("idea", ""), meta.get("reply_to"), others or "none"))
    elif meta.get("reply_to"):
        found = [str(c.get("label")) for c in meta.get("claude_strokes") or [] if c.get("label")]
        lines.append('  this ANSWERS your crit of %s ("%s"): your %d finding%s (%s) are drawn in orange; the user’s marks are on top' % (
            meta["reply_to"], meta.get("reply_title", ""), len(found), "" if len(found) == 1 else "s",
            ", ".join(found)[:80] or "unlabelled"))
    lines.append("  marked image (read it): " + os.path.join(folder, "marked.png"))
    lines.append("  clean capture: %s   meta: %s" % (os.path.join(folder, "clean.png"),
                                                    os.path.join(folder, "meta.json")))
    note = (meta.get("note") or "").strip()
    if note:
        lines.append('  the user\'s note: "%s"' % note)
    task = meta.get("task")
    if task and task not in ("model", "general"):
        lines.append("  task: %s" % task)
    for p in meta.get("presets") or []:
        files = "; files: " + " | ".join(p.get("files") or []) if p.get("files") else ""
        lines.append("  preset %s / %s: %s%s" % (p.get("category"), p.get("name"), p.get("instructions", ""), files))
    lines.extend(stroke_lines(meta, folder) or ["  no marks drawn: the capture itself (and the selection) is the message"])
    sel = meta.get("selection") or []
    sub = meta.get("subobject_selection") or []
    if sel or sub:
        names = ["%s on %s" % (s.get("type"), s.get("layer")) for s in sel[:3]]
        names += ["%s on %s %s" % (s.get("type"), s.get("layer"), ",".join(s.get("components", [])[:4])) for s in sub[:3]]
        lines.append("  selected at capture: %d object(s), %d sub-object(s): %s" % (len(sel), len(sub), "; ".join(names)))
    clips = meta.get("clipping_planes") or []
    if clips:
        lines.append("  %d clipping plane(s) active in this view" % len(clips))
    return "\n".join(lines)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    prompt = (payload.get("prompt") or "").lstrip()
    if prompt.startswith(("/", "<")) or not os.path.isdir(INBOX):
        return 0
    names = sorted(n for n in os.listdir(INBOX)
                   if not n.startswith(".") and os.path.isfile(os.path.join(INBOX, n, "meta.json"))
                   and not os.path.exists(os.path.join(INBOX, n, ".attached")))
    if not names:
        return 0
    now = time.time()
    blocks, views = [], []
    for n in names:
        src = os.path.join(INBOX, n)
        try:
            with open(os.path.join(src, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        age = now - float(meta.get("captured_unix", now))
        fresh = age <= MAX_AGE_S
        dest_root = SENT if fresh else STALE
        os.makedirs(dest_root, exist_ok=True)
        dest = os.path.join(dest_root, n)
        if os.path.exists(dest):
            dest += "-%d" % int(now)
        moved = False
        for _ in range(12):   # a virus scan of the fresh PNGs can hold them for a moment; a failed move used to drop the capture
            try:
                os.rename(src, dest)
                moved = True
                break
            except FileNotFoundError:
                break   # another session's hook took it first
            except OSError:
                time.sleep(0.15)
        if not moved:
            if not os.path.isdir(src):
                continue
            # still locked: attach it from where it is, and mark it so the next prompt doesn't attach it again
            try:
                open(os.path.join(src, ".attached"), "w").close()
            except Exception:
                continue
            dest = src
        if fresh:
            blocks.append(describe(meta, dest, age))
            views.append("%s, %d marks" % ((meta.get("view") or {}).get("name", "?"),
                                           len(meta.get("strokes") or [])))
    if not blocks:
        return 0
    out = {
        "systemMessage": "ClaudeMark: attached %d Rhino capture%s (%s)" % (
            len(blocks), "" if len(blocks) == 1 else "s", "; ".join(views)),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": GUIDE + "\n\n" + "\n\n".join(blocks),
        },
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # never block a prompt over a markup problem
