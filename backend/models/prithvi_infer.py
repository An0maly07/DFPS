"""ONNX inference wrapper for the fine-tuned Prithvi flood model (PLAN.md §4.1 prithvi_infer.py).

Sliding-window (224², configurable stride) over a 6-band Sentinel-2 scene, logits
averaged in the overlaps, then classified into the same scheme as the SAR mask so the
two sources render side by side with one legend:

    0 nodata · 1 land · 2 permanent/pre-event water · 3 flood · 4 cloud / shadow (optical blind)

"Permanent" water is taken from the SAR reference mask (class 2 of the nearest S1 pass)
because a single optical scene cannot tell a river from a flooded field.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from backend.models.prithvi_data import CHIP, SCL_INVALID, normalise

ART = Path(__file__).resolve().parent / "artifacts" / "prithvi"
CLOUD_SCL = (3, 8, 9, 10)
CLASS_NODATA, CLASS_LAND, CLASS_PERM, CLASS_FLOOD, CLASS_CLOUD = 0, 1, 2, 3, 4


@lru_cache(maxsize=1)
def session(region: str = "kolhapur-sangli") -> ort.InferenceSession:
    path = ART / f"prithvi_flood_{region}.onnx"
    if not path.exists():
        raise FileNotFoundError(f"{path} — run backend.models.prithvi_finetune first")
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in ort.get_available_providers() else ["CPUExecutionProvider"]
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    return ort.InferenceSession(str(path), so, providers=providers)


def meta(region: str = "kolhapur-sangli") -> dict:
    p = ART / "finetune_meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _pad_to(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    ph, pw = max(0, h - arr.shape[-2]), max(0, w - arr.shape[-1])
    if ph or pw:
        arr = np.pad(arr, [(0, 0)] * (arr.ndim - 2) + [(0, ph), (0, pw)])
    return arr


def _torch_runner(region: str):
    """Offline alternative to ONNX Runtime: fine-tuned weights on the GPU (the CPU ORT path is ~10× slower)."""
    import torch

    from backend.models.prithvi_model import PrithviFlood

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    m = PrithviFlood(with_aux=False)
    sd = torch.load(ART / f"prithvi_flood_{region}.pt", map_location="cpu")
    m.load_state_dict({k: v for k, v in sd.items() if not k.startswith("auxiliary_head.")})
    m.to(dev).eval()

    def run(xb: np.ndarray) -> np.ndarray:
        with torch.no_grad(), torch.autocast(device_type=dev.type, dtype=torch.float16, enabled=dev.type == "cuda"):
            return m(torch.from_numpy(xb).to(dev)).float().cpu().numpy()

    return run


def predict_water_prob(dn: np.ndarray, stride: int = CHIP, batch: int = 8, region: str = "kolhapur-sangli",
                       progress=None, backend: str = "onnx") -> np.ndarray:
    """dn: (6,H,W) int16 S2 DN → (H,W) float32 P(water) from averaged softmax."""
    if backend == "torch":
        run = _torch_runner(region)
    else:
        sess = session(region)
        run = lambda xb: sess.run(["logits"], {"image": xb})[0]  # noqa: E731
    _, H, W = dn.shape
    x = normalise(dn)
    Hp, Wp = (max(H, CHIP) + stride - 1) // stride * stride + CHIP - stride, (max(W, CHIP) + stride - 1) // stride * stride + CHIP - stride
    x = _pad_to(x, Hp, Wp)
    prob = np.zeros((Hp, Wp), dtype=np.float32)
    cnt = np.zeros((Hp, Wp), dtype=np.float32)
    windows = [(r, c) for r in range(0, Hp - CHIP + 1, stride) for c in range(0, Wp - CHIP + 1, stride)]
    for i in range(0, len(windows), batch):
        wb = windows[i: i + batch]
        xb = np.stack([x[:, r: r + CHIP, c: c + CHIP] for r, c in wb])
        logits = run(xb)  # (B,2,224,224)
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        p = e[:, 1] / e.sum(axis=1)
        for (r, c), pw in zip(wb, p):
            prob[r: r + CHIP, c: c + CHIP] += pw
            cnt[r: r + CHIP, c: c + CHIP] += 1
        if progress and (i // batch) % 20 == 0:
            progress(i + len(wb), len(windows))
    return (prob / np.maximum(cnt, 1))[:H, :W]


def classify_scene(scene: Path, sar_reference: Path | None, out: Path, stride: int = CHIP,
                   region: str = "kolhapur-sangli", progress=None, backend: str = "onnx") -> dict:
    """Write the 5-class raster for a scene; returns summary stats + water probability path."""
    with rasterio.open(scene) as s2:
        dn = s2.read(list(range(1, 7)))
        scl = s2.read(7)
        profile = s2.profile.copy()
        perm = None
        if sar_reference is not None:
            perm = np.zeros((s2.height, s2.width), dtype=np.int16)
            with rasterio.open(sar_reference) as ref:
                reproject(rasterio.band(ref, 1), perm, src_transform=ref.transform, src_crs=ref.crs,
                          src_nodata=ref.nodata, dst_transform=s2.transform, dst_crs=s2.crs,
                          dst_nodata=0, resampling=Resampling.nearest)
        px_km2 = abs(s2.transform.a * 111.32 * np.cos(np.radians(s2.bounds.top))) * abs(s2.transform.e * 111.32)

    prob = predict_water_prob(dn, stride=stride, region=region, progress=progress, backend=backend)
    nodata = (dn == 0).any(axis=0) | np.isin(scl, [0, 1])
    cloud = np.isin(scl, list(CLOUD_SCL))
    threshold = float(meta(region).get("water_threshold", 0.5))  # chosen on validation chips at fine-tune time
    water = prob >= threshold
    cls = np.full(prob.shape, CLASS_LAND, dtype=np.uint8)
    cls[water] = CLASS_FLOOD
    if perm is not None:
        cls[water & (perm == 2)] = CLASS_PERM
    cls[cloud] = CLASS_CLOUD
    cls[nodata] = CLASS_NODATA

    profile.update(count=1, dtype="uint8", nodata=0, compress="deflate")
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(cls, 1)
    prob_path = out.with_name(out.stem + "_prob.tif")
    profile.update(dtype="uint8", nodata=255)
    with rasterio.open(prob_path, "w", **profile) as dst:
        p8 = np.clip(prob * 100, 0, 100).astype(np.uint8)
        p8[nodata] = 255
        dst.write(p8, 1)
    return {
        "class_raster": str(out), "prob_raster": str(prob_path),
        "flood_area_km2": round(float((cls == CLASS_FLOOD).sum() * px_km2), 2),
        "permanent_water_km2": round(float((cls == CLASS_PERM).sum() * px_km2), 2),
        "cloud_pct": round(float(cloud.mean() * 100), 1),
        "usable_pct": round(float((~cloud & ~nodata).mean() * 100), 1),
        "pixel_km2": px_km2,
        "water_threshold": threshold,
    }
