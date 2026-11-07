# -*- coding: utf-8 -*-
"""Post a crit back onto the user’s capture as a ClaudeMark reply. Runs inside Rhino (IronPython 2.7):

    exec(open(r"<kit folder>\\tools\\cm_core.py").read())
    exec(open(r"<kit folder>\\tools\\cm_reply.py").read())
    out = post_reply("20261018-150514", "Critic: the canopy", [
        {"n": 1, "at": [12.0, 40.5, 16.4], "label": "no visible support at this corner"},
        {"n": 2, "px": [880, 412], "label": "reads as two gestures from here"},
    ], summary="Two findings; the argument is in chat under C1, C2.")

Each finding gets a pin "C<n>" at the element (a world point is projected through the capture camera;
"px" is an image pixel), a short leader and a label tag placed clear of the others. Extra marks
(arrows, boxes, pen lines) go in `extra` as ClaudeMark strokes in image px. The user opens it with
ClaudeReply in Rhino and answers on top; that answer comes back as a capture with reply_to set.
"""
import os
import json
import copy
import shutil
import datetime

_CM_PATH = os.path.join(CM_ROOT, "claude_mark.py")
_ACCENT = [217, 119, 87]


def _claudemark():
    ns = {"__name__": "claudemark_lib", "CLAUDEMARK_NO_UI": True, "__file__": _CM_PATH}
    exec(compile(open(_CM_PATH).read(), _CM_PATH, "exec"), ns)
    return ns


def _overlaps(a, b, pad=6.0):
    return not (a[0] + a[2] + pad < b[0] or b[0] + b[2] + pad < a[0] or
                a[1] + a[3] + pad < b[1] or b[1] + b[3] + pad < a[1])


def post_reply(stamp, title, findings, summary="", extra=(), font_px=26.0, background="auto"):
    """background: "auto" recaptures the user’s model now from the capture camera and uses that image if the
    model changed since the capture (so marks placed by world point land on what is there now);
    "capture" keeps the user’s original image; "fresh" always recaptures."""
    folder, meta = load_capture(stamp)
    cam = Cam(meta)
    cm = _claudemark()
    W, H = meta["view"]["size"]
    rid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-reply"
    tmp = os.path.join(CM_OUTBOX, "." + rid)
    if not os.path.isdir(tmp):
        os.makedirs(tmp)
    used_bg, bg_note = "capture", ""
    if background in ("auto", "fresh"):
        fresh = os.path.join(tmp, "fresh.png")
        err = capture_from_meta(meta, fresh)
        if err > 1.5:
            bg_note = "the user’s view's aspect has changed, so the fresh capture doesn't register (%.1f px); kept the original" % err
        else:
            diff = image_difference(os.path.join(folder, "clean.png"), fresh)
            if background == "fresh" or diff > 1.0:
                used_bg = "fresh"
                bg_note = "the model changed since the capture (difference %.1f), so the reply shows it as it is now" % diff
        if used_bg == "fresh":
            shutil.move(fresh, os.path.join(tmp, "clean.png"))
        elif os.path.exists(fresh):
            os.remove(fresh)
    strokes, boxes = [], []
    base = {"by": "claude", "colour": "claude", "rgb": list(_ACCENT), "width": 6}
    pins = []
    for f in findings:
        if "px" in f:
            x, y = float(f["px"][0]), float(f["px"][1])
        else:
            x, y, _ = cam.project(rg.Point3d(*f["at"]))
        x, y = min(max(x, 0.0), W - 1.0), min(max(y, 0.0), H - 1.0)
        pins.append((f, x, y))
        boxes.append((x - 22, y - 22, 44, 44))   # keep labels off every pin
    for f, x, y in pins:
        label = "C%d" % f["n"]
        note = dict(base, kind="text", text="%s  %s" % (label, f["label"]), font_px=font_px, points=[[0, 0]])
        cm["measure_text"](note)
        tw, th = note["size"]
        prefer = [tuple(f["offset"])] if f.get("offset") else []
        options = prefer + [(40, -40 - th), (40, 30), (-40 - tw, -40 - th), (-40 - tw, 30),
                            (60, -th / 2.0), (-60 - tw, -th / 2.0), (-tw / 2.0, -80 - th), (-tw / 2.0, 70)]
        spot = None
        for dx, dy in options:
            bx, by = x + dx, y + dy
            if bx < 4 or by < 4 or bx + tw > W - 4 or by + th > H - 4:
                continue
            if any(_overlaps((bx, by, tw, th), b) for b in boxes):
                continue
            spot = (bx, by)
            break
        if spot is None:   # crowded: take the first option that fits on the image
            for dx, dy in options:
                bx, by = min(max(x + dx, 4.0), W - tw - 4), min(max(y + dy, 4.0), H - th - 4)
                spot = (bx, by)
                break
        bx, by = spot
        boxes.append((bx, by, tw, th))
        note["points"] = [[round(bx, 1), round(by, 1)]]
        # leader from the pin's edge to the nearest point of the tag
        lx, ly = min(max(x, bx), bx + tw), min(max(y, by), by + th)
        strokes.append(dict(base, kind="pen", width=3, points=[[round(x, 1), round(y, 1)], [round(lx, 1), round(ly, 1)]]))
        strokes.append(note)
        strokes.append(dict(base, kind="pin", label=label, points=[[round(x, 1), round(y, 1)]]))
    for st in extra:
        strokes.append(dict(base, **st))

    if used_bg == "capture":
        shutil.copyfile(os.path.join(folder, "clean.png"), os.path.join(tmp, "clean.png"))
    keep = dict((k, copy.deepcopy(meta[k])) for k in ("camera", "view", "doc", "cplane", "image", "clipping_planes")
                if k in meta)
    rec = {"reply_to": stamp, "created": datetime.datetime.now().isoformat(), "title": title,
           "summary": summary, "strokes": strokes, "meta": keep, "background": used_bg, "background_note": bg_note}
    with open(os.path.join(tmp, "reply.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    cm["compose"](os.path.join(tmp, "clean.png"), strokes, os.path.join(tmp, "marked.png"), int(W), int(H), {})
    final = os.path.join(CM_OUTBOX, rid)
    os.rename(tmp, final)
    rhino_notice("Claude replied to %s (%d findings). Type ClaudeReply to open it and answer on top." % (
        stamp, len(findings)))
    if bg_note:
        print("background: " + bg_note)
    return os.path.join(final, "marked.png")
