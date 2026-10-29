"""Pitch geometry and calibration, driven by the sport pack's `pitch` section.

Coordinates are metres: x along the pitch from the left end line (as the camera sees it),
y across it from the near (camera-side) sideline. Veo's panoramic camera doesn't move, so one
homography per match maps pixels to pitch coordinates. It is fitted from landmarks a coach
clicks on a frame ("left 45m line x near sideline" ...).

Caveat, untested on real footage: a stitched panorama is not a perfect pinhole image, so a single
homography may be off near the edges. The reprojection error reported here measures that.
"""
from dataclasses import dataclass

import numpy as np


class CalibrationError(ValueError):
    pass


@dataclass
class Landmark:
    id: str
    label: str
    x: float
    y: float


def dims(pack: dict, length: float | None = None, width: float | None = None) -> tuple[float, float]:
    """Pitch length/width, defaulted and range-checked against the pack."""
    p = pack["pitch"]
    out = []
    for name, value in (("length", length), ("width", width)):
        spec = p[name]
        v = spec["default"] if value is None else float(value)
        if not spec["min"] <= v <= spec["max"]:
            raise CalibrationError(f"pitch {name} {v:g}m is outside {spec['min']}-{spec['max']}m for {pack['name']}")
        out.append(v)
    return out[0], out[1]


def landmarks(pack: dict, length: float, width: float) -> list[Landmark]:
    p, L, W = pack["pitch"], length, width
    marks = []
    for end, x0, sign in (("left", 0.0, 1), ("right", L, -1)):
        End = end.capitalize()
        for y, side in ((0.0, "near"), (W, "far")):
            marks.append(Landmark(f"{end}.corner.{side}", f"{End} end line × {side} sideline", x0, y))
            for d in p.get("lines_from_each_end", []):
                marks.append(Landmark(f"{end}.{d:g}m.{side}", f"{End} {d:g}m line × {side} sideline", x0 + sign * d, y))
        for box in p.get("boxes", []):
            depth, half = box["depth"], box["width"] / 2
            for y, side in ((W / 2 - half, "near"), (W / 2 + half, "far")):
                marks.append(Landmark(f"{end}.{box['name']}.{side}.end", f"{End} {box['name']}, {side} corner on end line", x0, y))
                marks.append(Landmark(f"{end}.{box['name']}.{side}.front", f"{End} {box['name']}, {side} front corner", x0 + sign * depth, y))
        g = p.get("goal_width")
        if g:
            for y, side in ((W / 2 - g / 2, "near"), (W / 2 + g / 2, "far")):
                marks.append(Landmark(f"{end}.post.{side}", f"{End} goal, {side} post (base)", x0, y))
    for y, side in ((0.0, "near"), (W, "far")):
        marks.append(Landmark(f"halfway.{side}", f"Halfway line × {side} sideline", L / 2, y))
    return marks


def segments(pack: dict, length: float, width: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Pitch markings as straight segments, for drawing the fitted pitch over a frame."""
    p, L, W = pack["pitch"], length, width
    segs = [((0, 0), (L, 0)), ((0, W), (L, W)), ((0, 0), (0, W)), ((L, 0), (L, W)), ((L / 2, 0), (L / 2, W))]
    for d in p.get("lines_from_each_end", []):
        segs += [((d, 0), (d, W)), ((L - d, 0), (L - d, W))]
    for box in p.get("boxes", []):
        depth, a, b = box["depth"], W / 2 - box["width"] / 2, W / 2 + box["width"] / 2
        for x0, x1 in ((0, depth), (L, L - depth)):
            segs += [((x0, a), (x1, a)), ((x1, a), (x1, b)), ((x1, b), (x0, b))]
    return segs


# ---------- homography ----------

def _normalizer(pts: np.ndarray) -> np.ndarray:
    """Similarity transform moving points to mean 0, mean distance sqrt(2) (Hartley)."""
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2) / d if d > 0 else 1.0
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])


def fit_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3x3 H with dst ~ H @ src, from >= 4 point pairs (normalised DLT, least squares)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if len(src) < 4:
        raise CalibrationError("need at least 4 landmarks")
    Ts, Td = _normalizer(src), _normalizer(dst)
    s = np.c_[src, np.ones(len(src))] @ Ts.T
    d = np.c_[dst, np.ones(len(dst))] @ Td.T
    rows = []
    for (x, y, _), (u, v, _) in zip(s, d):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, sv, vt = np.linalg.svd(np.array(rows))
    # A good fit has one ~zero singular value; a second small one means the points were
    # (nearly) collinear and don't pin down a plane.
    if sv[-2] < 1e-6 * sv[0]:
        raise CalibrationError("landmarks are (nearly) in a line - pick points spread over the pitch")
    H = np.linalg.inv(Td) @ vt[-1].reshape(3, 3) @ Ts
    return H / H[2, 2]


def apply(H: np.ndarray, pts) -> np.ndarray:
    pts = np.asarray(pts, float).reshape(-1, 2)
    out = np.c_[pts, np.ones(len(pts))] @ np.asarray(H, float).T
    return out[:, :2] / out[:, 2:3]


def calibrate(pack: dict, length: float, width: float, points: list[dict]) -> dict:
    """Fit pixel -> pitch from clicked landmarks. `points` = [{landmark, u, v}] (u, v pixels).
    Returns the homography both ways and the fit error in metres and pixels."""
    by_id = {m.id: m for m in landmarks(pack, length, width)}
    seen = set()
    for p in points:
        if p["landmark"] not in by_id:
            raise CalibrationError(f"unknown landmark '{p['landmark']}'")
        if p["landmark"] in seen:
            raise CalibrationError(f"landmark '{p['landmark']}' given twice")
        seen.add(p["landmark"])
    px = np.array([[p["u"], p["v"]] for p in points], float)
    world = np.array([[by_id[p["landmark"]].x, by_id[p["landmark"]].y] for p in points], float)
    H = fit_homography(px, world)
    H_inv = np.linalg.inv(H)
    H_inv /= H_inv[2, 2]
    err_m = np.sqrt(((apply(H, px) - world) ** 2).sum(axis=1))
    err_px = np.sqrt(((apply(H_inv, world) - px) ** 2).sum(axis=1))
    return {
        "H": H.tolist(), "H_inv": H_inv.tolist(),
        "rms_error_m": round(float(np.sqrt((err_m ** 2).mean())), 3),
        "max_error_m": round(float(err_m.max()), 3),
        "rms_error_px": round(float(np.sqrt((err_px ** 2).mean())), 2),
        # 4 points fit exactly (error 0 says nothing); 5+ give a real check.
        "overdetermined": len(points) > 4,
    }


# ---------- zones ----------

def attacking_right(team: str, period: int, home_attacks_first: str) -> bool:
    """Whether `team` attacks the right-hand end in `period` (ends swap each period)."""
    home_right = (home_attacks_first == "right") == (period % 2 == 1)
    return home_right if team == "home" else not home_right


def zone_attributes(pack: dict, event_type: str, x: float, y: float, length: float, width: float,
                    attacking_right_end: bool) -> dict[str, str]:
    """The pack's zone/side attribute values for a point, seen by the team attacking that way.
    Only attributes (and values) the event type actually has are returned."""
    p = pack["pitch"]
    allowed = {a["id"]: set(a["values"]) for a in pack["_types"][event_type]["attributes"]}
    # Normalise so the attacked end line is at x = length and "left" is the attacker's left.
    ax = x if attacking_right_end else length - x
    ay = (width - y) if attacking_right_end else y  # facing right, the far sideline is on the left
    out = {}
    if "zone" in allowed:
        for rule in p.get("zones", []):
            if rule["value"] not in allowed["zone"]:
                continue
            if "from_goal" in rule and not rule["from_goal"][0] <= length - ax <= rule["from_goal"][1]:
                continue
            if "third" in rule and not rule["third"][0] <= ax / length <= rule["third"][1]:
                continue
            if "box" in rule:
                box = next(b for b in p["boxes"] if b["name"] == rule["box"])
                if not (length - ax <= box["depth"] and abs(ay - width / 2) <= box["width"] / 2):
                    continue
            out["zone"] = rule["value"]
            break
    sides = p.get("sides")
    if sides and "side" in allowed:
        lane = min(len(sides) - 1, max(0, int(ay / width * len(sides))))
        if sides[lane] in allowed["side"]:
            out["side"] = sides[lane]
    return out
