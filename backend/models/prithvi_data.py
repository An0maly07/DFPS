"""Chips + weak labels for the regional Prithvi fine-tune (PLAN.md §3.2).

Label protocol (documented honestly in the pitch — these are *weak* labels, not
hand-digitised ground truth):

* Optical scene: Sentinel-2 L2A, 2021-07-28 (first partially cloud-free acquisition
  after the 22-23 July peak; the peak itself is 100 % cloud — which is the point).
* Water label = SAR edge-Otsu class ∈ {permanent/pre water, flood} on the nearest
  *later* Sentinel-1 pass (2021-08-03, 6 days after). Flood water recedes, so any pixel
  that was water on 2021-07-22 but not on 2021-08-03 is ambiguous for 07-28 and is
  marked IGNORE rather than guessed.
* IGNORE also covers S2 nodata / saturated / cloud shadow / cloud / cirrus / snow
  (SCL 0,1,3,8,9,10,11) and S1 nodata.

Chips are 224 x 224 (the size the Sen1Floods11 checkpoint was fine-tuned at) and are
split spatially (west = train, east = validation) so the validation score is not
inflated by neighbouring, near-identical chips.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from backend.config import COG_DIR, DATA_DIR

PRITHVI_DIR = DATA_DIR / "prithvi"
CHIP = 224
IGNORE = -1
# Sen1Floods11 fine-tuning normalisation (hls-foundation-os sen1floods11_config.py),
# applied to reflectance (DN * 1e-4) in band order B2,B3,B4,B8A,B11,B12.
MEANS = np.array([0.14245495, 0.13921481, 0.12434631, 0.31420089, 0.20743526, 0.12046503], dtype=np.float32)
STDS = np.array([0.04036231, 0.04186983, 0.05267646, 0.0822221, 0.06834774, 0.05294205], dtype=np.float32)
SCL_INVALID = {0, 1, 3, 8, 9, 10, 11}
WATER_CLASSES = (2, 3)  # sar_otsu class raster: 0 nodata, 1 land, 2 permanent/pre water, 3 flood


def _warp_to(src_path: Path, like: rasterio.DatasetReader, resampling=Resampling.nearest) -> np.ndarray:
    """Reproject a single-band raster onto `like`'s grid."""
    out = np.zeros((like.height, like.width), dtype=np.int16)
    with rasterio.open(src_path) as src:
        reproject(
            rasterio.band(src, 1), out,
            src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
            dst_transform=like.transform, dst_crs=like.crs, dst_nodata=0, resampling=resampling,
        )
    return out


def normalise(dn: np.ndarray) -> np.ndarray:
    """(6,H,W) int16 DN → float32 standardised reflectance."""
    x = dn.astype(np.float32) * 1e-4
    return (x - MEANS[:, None, None]) / STDS[:, None, None]


def weak_labels(scene: Path, sar_after: Path, sar_before: Path | None) -> tuple[np.ndarray, dict]:
    with rasterio.open(scene) as s2:
        scl = s2.read(7)
        any_zero = (s2.read(list(range(1, 7))) == 0).any(axis=0)
        after = _warp_to(sar_after, s2)
        before = _warp_to(sar_before, s2) if sar_before else None
    label = np.zeros(scl.shape, dtype=np.int8)
    label[np.isin(after, WATER_CLASSES)] = 1
    ignore = np.isin(scl, list(SCL_INVALID)) | any_zero | (after == 0)
    if before is not None:
        receded = np.isin(before, WATER_CLASSES) & ~np.isin(after, WATER_CLASSES)
        ignore |= receded
    label[ignore] = IGNORE
    stats = {
        "pixels": int(label.size),
        "water_pct": round(float((label == 1).mean() * 100), 2),
        "land_pct": round(float((label == 0).mean() * 100), 2),
        "ignore_pct": round(float((label == IGNORE).mean() * 100), 2),
        "cloud_pct": round(float(np.isin(scl, [3, 8, 9, 10]).mean() * 100), 2),
    }
    return label, stats


def make_chips(
    scene: Path,
    label: np.ndarray,
    out: Path,
    split_lon: float | None = None,
    min_valid: float = 0.5,
) -> dict:
    """Tile scene+label into 224² chips; write train/val npz + a manifest."""
    with rasterio.open(scene) as s2:
        dn = s2.read(list(range(1, 7)))
        transform = s2.transform
        w, h = s2.width, s2.height
    if split_lon is None:
        split_lon = transform.c + transform.a * w * 0.65  # west 65 % train, east 35 % val
    tr_x, tr_y, va_x, va_y, meta = [], [], [], [], []
    for r in range(0, h - CHIP + 1, CHIP):
        for c in range(0, w - CHIP + 1, CHIP):
            lab = label[r: r + CHIP, c: c + CHIP]
            valid = (lab != IGNORE).mean()
            if valid < min_valid:
                continue
            water = float((lab == 1).mean())
            lon = transform.c + transform.a * (c + CHIP / 2)
            lat = transform.f + transform.e * (r + CHIP / 2)
            x = dn[:, r: r + CHIP, c: c + CHIP]
            split = "train" if lon < split_lon else "val"
            (tr_x if split == "train" else va_x).append(x)
            (tr_y if split == "train" else va_y).append(lab)
            meta.append({"row": r, "col": c, "lon": round(lon, 4), "lat": round(lat, 4),
                         "valid": round(float(valid), 3), "water": round(water, 4), "split": split})
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "train.npz", x=np.stack(tr_x), y=np.stack(tr_y))
    np.savez_compressed(out / "val.npz", x=np.stack(va_x), y=np.stack(va_y))
    manifest = {
        "scene": scene.name, "chip": CHIP, "split_lon": split_lon,
        "n_train": len(tr_x), "n_val": len(va_x),
        "train_water_frac": round(float(np.mean([m["water"] for m in meta if m["split"] == "train"])), 4),
        "val_water_frac": round(float(np.mean([m["water"] for m in meta if m["split"] == "val"])), 4),
        "chips": meta,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def build(region: str = "kolhapur-sangli", s2_date: str = "2021-07-28",
          sar_after: str = "2021-08-03", sar_before: str | None = "2021-07-22") -> dict:
    scene = PRITHVI_DIR / f"s2_{region}_{s2_date}.tif"
    after = COG_DIR / f"flood_mask_{region}_{sar_after}.tif"
    before = COG_DIR / f"flood_mask_{region}_{sar_before}.tif" if sar_before else None
    for p in (scene, after, before):
        if p is not None and not p.exists():
            raise FileNotFoundError(p)
    label, stats = weak_labels(scene, after, before)
    out = PRITHVI_DIR / f"chips_{region}_{s2_date}"
    with rasterio.open(scene) as s2:
        prof = s2.profile.copy()
    prof.update(count=1, dtype="int8", nodata=IGNORE, compress="deflate")
    with rasterio.open(PRITHVI_DIR / f"weak_label_{region}_{s2_date}.tif", "w", **prof) as dst:
        dst.write(label, 1)
    manifest = make_chips(scene, label, out)
    manifest["label_stats"] = stats
    manifest["label_protocol"] = {
        "s2_scene": s2_date, "sar_after": sar_after, "sar_before": sar_before,
        "rule": "water := SAR water on sar_after; ignore := clouds/nodata or (water on sar_before and not on sar_after)",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    m = build()
    print(json.dumps({k: v for k, v in m.items() if k != "chips"}, indent=1))
