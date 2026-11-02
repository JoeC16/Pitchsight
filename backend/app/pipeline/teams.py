"""Team assignment from kit colours.

Each track gets a colour sample (e.g. the median colour of its torso pixels over many frames).
Outfield players of the two teams form two clusters; the cluster closer to the declared home
kit colour is home. Tracks far from both clusters (goalkeepers, referees) are left unassigned,
since their kits differ from their team's on purpose.
"""
import numpy as np


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 0-255 -> CIE Lab, where distances roughly match how different colours look."""
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def assign_teams(track_colours: dict[int, tuple[int, int, int]], home_rgb, away_rgb,
                 max_spread: float = 70.0, margin: float = 0.6, iters: int = 20) -> dict[int, str | None]:
    """{track id: 'home' | 'away' | None}. Two-cluster k-means in Lab, seeded from the declared
    kits. A track gets the nearer team when it's within `max_spread` (Lab units) of it AND clearly
    nearer it than the other (distance ratio below `margin`). Kits look darker or washed out far
    from the camera and in bad light, so closeness is relative; keepers and referees, far from
    both kits, stay unassigned. Limit: a keeper whose kit is close to an outfield kit (orange next
    to red) is assigned to that team; telling keepers apart needs a detector class for them."""
    if not track_colours:
        return {}
    ids = list(track_colours)
    X = _rgb_to_lab(np.array([track_colours[i] for i in ids], float))
    centres = _rgb_to_lab(np.array([home_rgb, away_rgb], float))
    for _ in range(iters):
        d = np.linalg.norm(X[:, None, :] - centres[None, :, :], axis=2)
        label = d.argmin(axis=1)
        # Robust update: only points near a centre move it, so keepers/refs don't drag it.
        near = [(label == k) & (d[:, k] <= max_spread) for k in (0, 1)]
        new = np.array([X[near[k]].mean(axis=0) if near[k].any() else centres[k] for k in (0, 1)])
        if np.allclose(new, centres):
            break
        centres = new
    d = np.linalg.norm(X[:, None, :] - centres[None, :, :], axis=2)
    out = {}
    for i, row in zip(ids, d):
        k = int(row.argmin())
        clear = row[k] <= margin * row[1 - k]
        out[i] = ("home", "away")[k] if row[k] <= max_spread and clear else None
    return out
