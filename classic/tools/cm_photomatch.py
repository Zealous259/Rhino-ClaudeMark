"""Solve a site photo's camera from ClaudeMark photo-match pairs (CPython 3: numpy, scipy, OpenCV).

    python cm_photomatch.py solve pairs.json camera.json [--photo PHOTO]

pairs.json: {"image_size": [W, H], "pairs": [{"n": 1, "uv": [u, v], "xyz": [X, Y, Z]}, ...],
             "focal_35mm": optional}
  uv are fractions of the photo's width and height (ClaudeMark stores them that way, so any
  resolution of the photo works); xyz are model points in Rhino world units (snapped corners).

camera.json gets the pinhole camera in Rhino terms (location, direction, up, focal in px, vertical
field of view, Rhino lens for a landscape viewport = 24 f / H), the reprojection residual of every
pair, and the leave-one-out error of every pair: each point predicted by a camera solved WITHOUT it.
That last one is the honest check; a residual on a point the solve was fitted to proves little.
"""
import argparse
import json
import math
import sys

import numpy as np
import cv2
from scipy.optimize import least_squares


def exif_focal_35mm(photo):
    try:
        from PIL import Image
        ex = Image.open(photo).getexif()
        sub = ex.get_ifd(0x8769) if hasattr(ex, "get_ifd") else {}
        f35 = sub.get(0xA405) or ex.get(0xA405)
        return float(f35) if f35 else None
    except Exception:
        return None


def exif_datetime(photo):
    try:
        from PIL import Image
        ex = Image.open(photo).getexif()
        sub = ex.get_ifd(0x8769) if hasattr(ex, "get_ifd") else {}
        return sub.get(0x9003) or ex.get(0x0132)   # DateTimeOriginal, else DateTime
    except Exception:
        return None


def _K(f, W, H):
    return np.array([[f, 0, W / 2.0], [0, f, H / 2.0], [0, 0, 1.0]])


def _project(params, X, W, H):
    rvec, tvec, logf = params[:3], params[3:6], params[6]
    pts, _ = cv2.projectPoints(X, rvec, tvec, _K(math.exp(logf), W, H), None)
    return pts.reshape(-1, 2)


def _pnp(X, x, f, W, H):
    flag = cv2.SOLVEPNP_SQPNP if len(X) >= 3 else cv2.SOLVEPNP_ITERATIVE
    ok, rvec, tvec = cv2.solvePnP(X, x, _K(f, W, H), None, flags=flag)
    if not ok:
        return None
    pr, _ = cv2.projectPoints(X, rvec, tvec, _K(f, W, H), None)
    err = np.sqrt(np.mean(np.sum((pr.reshape(-1, 2) - x) ** 2, axis=1)))
    return err, rvec.ravel(), tvec.ravel()


def solve_core(X, x, W, H, f0=None):
    """Best (rvec, tvec, f) by a focal sweep (or the EXIF focal) then a joint least-squares refine."""
    diag = math.hypot(W, H)
    if f0:
        cands = [f0 * k for k in (0.8, 0.9, 1.0, 1.1, 1.25)]
    else:
        cands = [diag * f35 / 43.27 for f35 in np.geomspace(14, 200, 48)]
    best = None
    for f in cands:
        r = _pnp(X, x, f, W, H)
        if r and (best is None or r[0] < best[0]):
            best = (r[0], r[1], r[2], f)
    if best is None:
        raise RuntimeError("solvePnP failed for every focal length")
    _, rvec, tvec, f = best
    p0 = np.concatenate([rvec, tvec, [math.log(f)]])
    res = least_squares(lambda p: (_project(p, X, W, H) - x).ravel(), p0, method="lm" if len(X) * 2 >= 7 else "trf")
    return res.x


def camera_out(p, W, H):
    rvec, tvec, f = p[:3], p[3:6], math.exp(p[6])
    R, _ = cv2.Rodrigues(rvec)
    C = (-R.T @ tvec).tolist()
    fwd = (R.T @ np.array([0, 0, 1.0])).tolist()
    up = (R.T @ np.array([0, -1.0, 0])).tolist()
    vfov = 2 * math.degrees(math.atan(H / 2.0 / f))
    hfov = 2 * math.degrees(math.atan(W / 2.0 / f))
    return {"location": C, "direction": fwd, "up": up, "focal_px": f, "image_size": [W, H],
            "vfov_deg": vfov, "hfov_deg": hfov,
            "lens_35mm_equiv": f * 43.27 / math.hypot(W, H),
            "rhino_lens_landscape": 24.0 * f / min(W, H) if W >= H else 24.0 * f / W}


def _rms(p, X, x, W, H):
    return float(np.sqrt(np.mean(np.sum((_project(p, X, W, H) - x) ** 2, axis=1))))


def solve(pairs, W, H, focal_35mm=None):
    from itertools import combinations
    X = np.array([p["xyz"] for p in pairs], dtype=np.float64)
    x = np.array([[p["uv"][0] * W, p["uv"][1] * H] for p in pairs], dtype=np.float64)
    n = len(pairs)
    if n < 4:
        raise ValueError("need at least 4 pairs (6 or more, spread out, is far better); got %d" % n)
    f0 = focal_35mm * math.hypot(W, H) / 43.27 if focal_35mm else None
    good_rms = max(4.0, 0.0015 * math.hypot(W, H))   # ~7.5 px on a 12 MP photo
    p = solve_core(X, x, W, H, f0)
    dropped = []
    if _rms(p, X, x, W, H) > good_rms and n >= 7:
        # The pairs disagree: usually a numbering slip. Find the smallest set to leave out that fits.
        best = None
        for k in range(1, min(3, n - 6) + 1):
            for out in combinations(range(n), k):
                keep = [j for j in range(n) if j not in out]
                try:
                    pk = solve_core(X[keep], x[keep], W, H, f0)
                except Exception:
                    continue
                r = _rms(pk, X[keep], x[keep], W, H)
                if best is None or r < best[0]:
                    best = (r, out, pk)
            if best is not None and best[0] <= good_rms:
                break
        if best is not None and best[0] <= good_rms:
            dropped = list(best[1])
            p = best[2]
    inl = [j for j in range(n) if j not in dropped]
    resid = np.linalg.norm(_project(p, X, W, H) - x, axis=1)
    loo = []
    if len(inl) >= 6:
        for i in range(n):
            keep = [j for j in inl if j != i]
            try:
                pi = solve_core(X[keep], x[keep], W, H, math.exp(p[6]))
                loo.append(float(np.linalg.norm(_project(pi, X[i:i + 1], W, H)[0] - x[i])))
            except Exception:
                loo.append(None)
    # how well-posed: 3D spread (planar sets leave the focal length weakly determined)
    Xc = X - X.mean(axis=0)
    sv = np.linalg.svd(Xc, compute_uv=False)
    planarity = float(sv[-1] / sv[0]) if sv[0] > 0 else 0.0
    out = camera_out(p, W, H)
    rms_inl = float(np.sqrt(np.mean(resid[inl] ** 2)))
    out.update({
        "pairs": n,
        "used": [pairs[j]["n"] for j in inl],
        "dropped": [pairs[j]["n"] for j in dropped],
        "rms_px": rms_inl,
        "residual_px": [{"n": pr["n"], "px": float(r)} for pr, r in zip(pairs, resid)],
        "leave_one_out_px": [{"n": pr["n"], "px": e} for pr, e in zip(pairs, loo)] if loo else None,
        "planarity": planarity,
        "warnings": [],
    })
    for j in dropped:
        out["warnings"].append("pair %d left out: it disagrees with the rest by %.0f px (wrong number, or "
                               "clicked on a different corner?)" % (pairs[j]["n"], resid[j]))
    if rms_inl > good_rms:
        out["warnings"].append("the pairs don't agree (rms %.0f px): check the numbering on the photo and the model"
                               % rms_inl)
    if len(inl) < 6:
        out["warnings"].append("only %d usable pairs: no leave-one-out check; add points" % len(inl))
    if planarity < 0.05:
        out["warnings"].append("the model points are nearly on one plane (%.3f): focal length is weakly determined" % planarity)
    if loo:
        good = [loo[k] for k, j in enumerate(range(n)) if j in inl and loo[k] is not None]
        if good:
            med = float(np.median(good))
            for k, (pr, e) in enumerate(zip(pairs, loo)):
                if k in inl and e is not None and e > max(3 * med, 0.01 * math.hypot(W, H)):
                    out["warnings"].append("pair %d is doubtful: predicted %.0f px from where it was clicked" % (pr["n"], e))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("solve")
    s.add_argument("pairs")
    s.add_argument("out")
    s.add_argument("--photo")
    a = ap.parse_args(argv)
    if a.cmd != "solve":
        ap.print_help()
        return 2
    with open(a.pairs) as fh:
        d = json.load(fh)
    W, H = d["image_size"]
    f35 = d.get("focal_35mm") or (exif_focal_35mm(a.photo) if a.photo else None)
    cam = solve(d["pairs"], W, H, f35)
    if a.photo:
        cam["photo"] = a.photo
        cam["exif_datetime"] = exif_datetime(a.photo)
    cam["focal_prior_35mm"] = f35
    with open(a.out, "w") as fh:
        json.dump(cam, fh, indent=1)
    print("rms %.2f px over %d pairs; lens %.1f mm equiv; %s" % (
        cam["rms_px"], cam["pairs"], cam["lens_35mm_equiv"], "; ".join(cam["warnings"]) or "no warnings"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
