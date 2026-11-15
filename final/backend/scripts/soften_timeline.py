"""Soft spots on the ground-motion timeline: turns the pixel-by-pixel frames into gradient spots.

The Tabasco frames come from an 80 m analysis grid where the motion lies in small scattered groups of
pixels, so in the 2D view they read as hard squares (Portuguese Bend has large, continuous areas, which
the display already blurs into soft spots). Each frame is softened here: the opacity of the sinking (red)
and of the rising (blue) pixels is blurred separately with a small gaussian (sigma 1.3 px, about 100 m)
and raised again, so a group of pixels becomes one soft spot, and a lone pixel a faint dot.
The spots keep their place and size; nothing is added where the frame was clear.

build_tabasco_scene.py softens the frames as it writes them. On frames that are already built:
    python scripts/soften_timeline.py ../Dane/model3d_tabasco_mexico/data
(needs numpy, scipy and pillow; "timeline.softened" in meta.json marks frames that are done, so a second
run changes nothing)
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

SIGMA_PX = 1.3      # blur radius in pixels of the frame
GAIN = 2.2          # brings the middle of a spot back to its colour after the blur
MAX_ALPHA = 0.9     # the strongest colour of the frames (0.35 + 0.55 at the full-colour change)
DOWN_RGB, UP_RGB = (214, 39, 40), (31, 104, 196)


def soften(rgba: np.ndarray) -> np.ndarray:
    """rgba: [h][w][4] uint8 frame (red = down, blue = up, alpha = strength) -> softened frame, same shape."""
    alpha = rgba[..., 3] / 255.0
    down = rgba[..., 0] > rgba[..., 2]
    dn, up = (np.clip(gaussian_filter(np.where(m, alpha, 0.0), SIGMA_PX) * GAIN, 0, MAX_ALPHA) for m in (down, ~down))
    out = np.zeros_like(rgba)
    out[..., :3] = np.where((dn >= up)[..., None], np.array(DOWN_RGB), np.array(UP_RGB))
    out[..., 3] = np.round(np.maximum(dn, up) * 255)
    return out


def main() -> None:
    from PIL import Image

    if len(sys.argv) != 2:
        sys.exit(__doc__)
    scene = Path(sys.argv[1])
    meta_path = scene / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    tl = meta.get("timeline")
    if not tl:
        sys.exit("No timeline in meta.json.")
    if tl.get("softened"):
        print("Frames already softened - nothing to do.")
        return
    for f in tl["files"]:
        path = scene / tl["dir"] / f
        Image.fromarray(soften(np.array(Image.open(path).convert("RGBA")))).save(path, optimize=True)
    tl["softened"] = {"sigma_px": SIGMA_PX, "gain": GAIN}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"softened {len(tl['files'])} frames in {scene / tl['dir']}")


if __name__ == "__main__":
    main()
