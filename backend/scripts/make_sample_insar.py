"""Generate a synthetic InSAR velocity layer for testing. NOT real data!

Mimics an EGMS-style point product: each point has a mean line-of-sight (LOS)
velocity in mm/year (negative = moving away from the satellite).

Run from the backend/ folder:
    python scripts/make_sample_insar.py
"""
import json
import math
import random
from pathlib import Path

random.seed(42)  # same "random" points every time

OUTPUT = Path("samples/insar_velocity.geojson")
POINT_COUNT = 8000
BBOX = (21.02, 49.61, 21.17, 49.665)  # min_lon, min_lat, max_lon, max_lat

# Moving areas: (lon, lat, peak velocity mm/yr, radius in degrees of latitude)
MOVING_AREAS = [
    (21.095, 49.634, -32.0, 0.004),   # inside sample landslide A (fast)
    (21.131, 49.646, -9.0, 0.004),    # inside sample landslide B (slow)
    (21.066, 49.6265, -18.0, 0.003),  # NOT in the landslide inventory - only InSAR sees it
]


def velocity_at(lon: float, lat: float) -> float:
    """Sum of smooth 'bumps' of motion plus measurement noise."""
    v = 0.0
    for c_lon, c_lat, peak, radius in MOVING_AREAS:
        # Scale longitude by cos(lat) so the bumps are round on the ground
        dx = (lon - c_lon) * math.cos(math.radians(lat))
        dy = lat - c_lat
        v += peak * math.exp(-(dx * dx + dy * dy) / (radius * radius))
    return v + random.gauss(0, 1.5)


def main() -> None:
    features = []
    for pid in range(POINT_COUNT):
        lon = random.uniform(BBOX[0], BBOX[2])
        lat = random.uniform(BBOX[1], BBOX[3])
        features.append({
            "type": "Feature",
            "properties": {
                "pid": pid,
                "mean_velocity": round(velocity_at(lon, lat), 2),
                "velocity_std": round(random.uniform(0.5, 2.0), 2),
            },
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    print(f"Saved {len(features)} points to {OUTPUT}")


if __name__ == "__main__":
    main()