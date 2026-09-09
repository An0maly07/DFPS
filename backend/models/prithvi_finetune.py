"""Regional fine-tune of the Sen1Floods11 Prithvi model on weak-labelled S2 chips (PLAN.md §3.2).

  python -m backend.models.prithvi_finetune --epochs 15 --batch 4 --lr 3e-5

Evaluates the *released* checkpoint on the validation chips first (zero-shot baseline),
fine-tunes, keeps the best-IoU epoch, and writes:
  backend/models/artifacts/prithvi/prithvi_flood_<region>.pt      (state dict)
  backend/models/artifacts/prithvi/prithvi_flood_<region>.onnx    (opset 17, dynamic batch)
  backend/models/artifacts/prithvi/finetune_meta.json             (metrics, label protocol)
Sized for a 4 GB GPU: batch 4 @ 224², fp16 autocast, frozen patch-embed + first 6 blocks.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from backend.models.prithvi_data import IGNORE, PRITHVI_DIR, normalise
from backend.models.prithvi_model import PrithviFlood, load_checkpoint

ART = Path(__file__).resolve().parent / "artifacts" / "prithvi"
BASE_CKPT = ART / "sen1floods11_Prithvi_100M.pth"
CE_WEIGHTS = torch.tensor([0.3, 0.7])  # as in the Sen1Floods11 fine-tune


def _load(split_dir: Path, name: str):
    z = np.load(split_dir / f"{name}.npz")
    return torch.from_numpy(normalise(z["x"].reshape(-1, 6, 224, 224))), torch.from_numpy(z["y"].astype(np.int64))


def _confusion(p, g) -> dict:
    tp = int(((p == 1) & (g == 1)).sum()); fp = int(((p == 1) & (g == 0)).sum())
    fn = int(((p == 0) & (g == 1)).sum()); tn = int(((p == 0) & (g == 0)).sum())
    return {
        "water_iou": round(tp / max(tp + fp + fn, 1), 4),
        "precision": round(tp / max(tp + fp, 1), 4),
        "recall": round(tp / max(tp + fn, 1), 4),
        "accuracy": round((tp + tn) / max(tp + fp + fn + tn, 1), 4),
        "n_valid_px": tp + fp + fn + tn,
    }


def _metrics(model, x, y, device, batch=4, thresholds=(0.5,)) -> dict | list[dict]:
    """Water metrics on valid pixels at one or more P(water) thresholds (argmax == 0.5)."""
    model.eval()
    probs, gts = [], []
    with torch.no_grad(), torch.autocast(device_type=device.type, dtype=torch.float16, enabled=device.type == "cuda"):
        for i in range(0, len(x), batch):
            pw = torch.softmax(model(x[i: i + batch].to(device)).float(), dim=1)[:, 1].cpu()
            gt = y[i: i + batch]
            valid = gt != IGNORE
            probs.append(pw[valid]); gts.append(gt[valid])
    pw, g = torch.cat(probs), torch.cat(gts)
    out = [{"threshold": t, **_confusion((pw >= t).long(), g)} for t in thresholds]
    return out[0] if len(thresholds) == 1 else out


def _soft_dice(logits, y, eps: float = 1.0):
    """Soft Dice on the water class over valid pixels — counters the ~1 % water prior that
    makes weighted cross-entropy collapse to 'all land' on this scene."""
    valid = (y != IGNORE).float()
    p = torch.softmax(logits.float(), dim=1)[:, 1] * valid
    t = (y == 1).float()
    inter = (p * t).sum()
    return 1 - (2 * inter + eps) / (p.sum() + t.sum() + eps)


def _augment(x, y):
    if torch.rand(1) < 0.5:
        x, y = x.flip(-1), y.flip(-1)
    if torch.rand(1) < 0.5:
        x, y = x.flip(-2), y.flip(-2)
    return x, y


def export_onnx(model: PrithviFlood, path: Path, device) -> None:
    model.eval().float()
    m = PrithviFlood(with_aux=False)
    m.load_state_dict({k: v for k, v in model.state_dict().items() if not k.startswith("auxiliary_head.")})
    m.eval()
    dummy = torch.zeros(1, 6, 224, 224)
    torch.onnx.export(
        m, dummy, str(path), opset_version=17, input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}}, dynamo=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--scene", default="2021-07-28")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=2, help="micro-batch (VRAM-bound: 2 fits a 4 GB card)")
    ap.add_argument("--accum", type=int, default=2, help="gradient accumulation steps (effective batch = batch*accum)")
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--freeze-blocks", type=int, default=6)
    ap.add_argument("--dice", type=float, default=1.0, help="weight of the soft-Dice term added to weighted CE")
    ap.add_argument("--land-ratio", type=float, default=1.5,
                    help="keep every training chip containing water plus this many × as many water-free chips "
                         "(the scene is ~99 %% land; a GTX 1650 needs ~4 s per chip)")
    ap.add_argument("--skip-train", action="store_true", help="only evaluate the released checkpoint + export ONNX")
    ap.add_argument("--export-only", action="store_true",
                    help="load the saved fine-tuned .pt, evaluate, sweep the water threshold, export ONNX + meta")
    ap.add_argument("--history-log", help="training log to recover per-epoch metrics from (with --export-only)")
    a = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(0)
    chips = PRITHVI_DIR / f"chips_{a.region}_{a.scene}"
    manifest = json.loads((chips / "manifest.json").read_text())
    xtr, ytr = _load(chips, "train")
    xva, yva = _load(chips, "val")
    n_all = len(xtr)
    if a.land_ratio >= 0:
        has_water = (ytr == 1).flatten(1).any(1)
        wet = torch.nonzero(has_water).flatten()
        dry = torch.nonzero(~has_water).flatten()
        dry = dry[torch.randperm(len(dry))[: int(len(wet) * a.land_ratio)]]
        keep = torch.cat([wet, dry])
        xtr, ytr = xtr[keep], ytr[keep]
        print(f"train chips: {len(wet)} with water + {len(dry)} land-only of {n_all}", flush=True)
    print(f"device={device}  train={len(xtr)} chips  val={len(xva)} chips", flush=True)

    model = PrithviFlood(with_aux=True)
    info = load_checkpoint(model, BASE_CKPT, strict=True)
    print(f"loaded {info['n_loaded']} tensors from {BASE_CKPT.name} (strict)", flush=True)
    model.to(device)

    baseline = _metrics(model, xva, yva, device)
    print("zero-shot (released checkpoint) on val:", baseline, flush=True)
    history = []
    best = {"epoch": 0, **baseline}
    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    pt_path = ART / f"prithvi_flood_{a.region}.pt"

    if a.export_only:
        best_state = torch.load(pt_path, map_location="cpu")
        model.load_state_dict(best_state)
        if a.history_log:
            import ast
            for line in Path(a.history_log).read_text().splitlines():
                if line.startswith("{'epoch'"):
                    history.append(ast.literal_eval(line))
            if history:
                top = max(history, key=lambda h: h["water_iou"])
                best = {"epoch": top["epoch"], **{k: v for k, v in top.items() if k not in ("epoch", "train_loss", "sec")}}
        print("loaded fine-tuned weights from", pt_path.name, flush=True)

    if not a.skip_train and not a.export_only:
        for p in model.backbone.patch_embed.parameters():
            p.requires_grad = False
        for blk in model.backbone.blocks[: a.freeze_blocks]:
            for p in blk.parameters():
                p.requires_grad = False
        params = [p for p in model.parameters() if p.requires_grad]
        opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.05)
        micro_per_epoch = (len(xtr) + a.batch - 1) // a.batch
        steps = a.epochs * ((micro_per_epoch + a.accum - 1) // a.accum)
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps + 1, pct_start=0.1)
        scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
        torch.backends.cudnn.benchmark = True
        w = CE_WEIGHTS.to(device)
        for epoch in range(1, a.epochs + 1):
            model.train()
            perm = torch.randperm(len(xtr))
            t0, tot, micro = time.time(), 0.0, 0
            opt.zero_grad(set_to_none=True)
            for i in range(0, len(xtr), a.batch):
                idx = perm[i: i + a.batch]
                xb, yb = _augment(xtr[idx], ytr[idx])
                xb, yb = xb.to(device), yb.to(device)
                with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=device.type == "cuda"):
                    out, aux = model(xb, return_aux=True)
                    loss = F.cross_entropy(out, yb, weight=w, ignore_index=IGNORE) \
                        + 0.4 * F.cross_entropy(aux, yb, weight=w, ignore_index=IGNORE) \
                        + a.dice * _soft_dice(out, yb)
                scaler.scale(loss / a.accum).backward()
                tot += float(loss) * len(idx)
                micro += 1
                if micro % a.accum == 0 or i + a.batch >= len(xtr):
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(params, 1.0)
                    scaler.step(opt)
                    scaler.update()
                    sched.step()
                    opt.zero_grad(set_to_none=True)
                if micro % 20 == 0:
                    print(f"  epoch {epoch} micro-batch {micro}/{micro_per_epoch}  {time.time() - t0:.0f}s  "
                          f"vram {torch.cuda.max_memory_allocated() / 1e9:.2f} GB" if device.type == "cuda" else
                          f"  epoch {epoch} micro-batch {micro}/{micro_per_epoch}  {time.time() - t0:.0f}s", flush=True)
            m = _metrics(model, xva, yva, device)
            rec = {"epoch": epoch, "train_loss": round(tot / len(xtr), 4), "sec": round(time.time() - t0, 1), **m}
            history.append(rec)
            print(rec, flush=True)
            if m["water_iou"] > best["water_iou"]:
                best = {"epoch": epoch, **m}
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    # Water-probability threshold: the weak labels are ~1 % water, so argmax (0.5) is rarely
    # the best operating point. Pick the IoU-maximising threshold on the validation chips.
    model.to(device)
    sweep = _metrics(model, xva, yva, device, thresholds=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9))
    chosen = max(sweep, key=lambda s: s["water_iou"])
    print("threshold sweep:", [(s["threshold"], s["water_iou"]) for s in sweep], "->", chosen["threshold"], flush=True)
    model.cpu()
    torch.save(best_state, pt_path)
    onnx_path = ART / f"prithvi_flood_{a.region}.onnx"
    export_onnx(model, onnx_path, torch.device("cpu"))
    meta = {
        "water_threshold": chosen["threshold"],
        "threshold_sweep": sweep,
        "best_val_at_threshold": chosen,
        "model_version": "prithvi-eo-1.0-100m-sen1floods11+regional-v1",
        "base_checkpoint": BASE_CKPT.name,
        "region": a.region, "s2_scene": a.scene,
        "label_protocol": manifest.get("label_protocol"), "label_stats": manifest.get("label_stats"),
        "n_train": int(len(xtr)), "n_train_available": manifest["n_train"], "n_val": manifest["n_val"], "split": "spatial west/east",
        "zero_shot_val": baseline, "best_val": best, "history": history,
        "train_args": vars(a), "device": str(device),
        "onnx": onnx_path.name, "onnx_mb": round(onnx_path.stat().st_size / 1e6, 1),
        "caveat": "Validation labels are SAR-derived weak labels on a 6-day-offset pass, not hand-digitised truth.",
    }
    (ART / "finetune_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: v for k, v in meta.items() if k != "history"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
