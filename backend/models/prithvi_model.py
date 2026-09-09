"""Prithvi-EO-1.0-100M + Sen1Floods11 segmentation model, in plain PyTorch.

Mirrors the architecture in `sen1floods11_Prithvi_100M.py` (hls-foundation-os /
mmsegmentation): TemporalViTEncoder (ViT-B/16, 6 bands, 1 frame) → token-to-image
neck (two ConvTranspose stacks, 14² → 224²) → FCNHead (1 conv + classifier, 2 classes).
Parameter names match the released checkpoint so `load_checkpoint()` is strict:
nothing is silently left randomly initialised.

Why not TerraTorch's registry: the public sen1floods11 checkpoint is an mmseg state
dict (backbone/neck/decode_head keys). TerraTorch loads the *foundation* backbone
and would pair it with a freshly initialised decoder, discarding the flood-specific
neck/head we want to start from. This module loads all of it.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

IN_CHANS = 6
IMG_SIZE = 224
PATCH = 16
EMBED = 768
DEPTH = 12
HEADS = 12
NUM_CLASSES = 2


class PatchEmbed(nn.Module):
    """3-D (t,h,w) patch embedding with tubelet 1 → same as 2-D but keeps the mmseg key layout."""

    def __init__(self):
        super().__init__()
        self.proj = nn.Conv3d(IN_CHANS, EMBED, kernel_size=(1, PATCH, PATCH), stride=(1, PATCH, PATCH))

    def forward(self, x):  # (B,C,T,H,W)
        x = self.proj(x)  # (B,E,T,h,w)
        return x.flatten(2).transpose(1, 2)  # (B, T*h*w, E)


class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.num_heads = HEADS
        self.qkv = nn.Linear(EMBED, EMBED * 3, bias=True)
        self.proj = nn.Linear(EMBED, EMBED)

    def forward(self, x):
        B, N, C = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv.unbind(0)
        x = F.scaled_dot_product_attention(q, k, v)
        return self.proj(x.transpose(1, 2).reshape(B, N, C))


class Mlp(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(EMBED, EMBED * 4)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(EMBED * 4, EMBED)

    def forward(self, x):
        return self.fc2(self.act(self.fc1(x)))


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm1 = nn.LayerNorm(EMBED, eps=1e-6)
        self.attn = Attention()
        self.norm2 = nn.LayerNorm(EMBED, eps=1e-6)
        self.mlp = Mlp()

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        return x + self.mlp(self.norm2(x))


class TemporalViTEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.patch_embed = PatchEmbed()
        n = (IMG_SIZE // PATCH) ** 2
        self.cls_token = nn.Parameter(torch.zeros(1, 1, EMBED))
        self.pos_embed = nn.Parameter(torch.zeros(1, n + 1, EMBED), requires_grad=False)  # sincos, loaded from ckpt
        self.blocks = nn.ModuleList([Block() for _ in range(DEPTH)])
        self.norm = nn.LayerNorm(EMBED, eps=1e-6)

    def forward(self, x):  # (B,6,1,224,224)
        x = self.patch_embed(x) + self.pos_embed[:, 1:, :]
        cls = (self.cls_token + self.pos_embed[:, :1, :]).expand(x.shape[0], -1, -1)
        x = torch.cat((cls, x), dim=1)
        for blk in self.blocks:
            x = blk(x)
        return self.norm(x)


class Norm2d(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.ln = nn.LayerNorm(dim, eps=1e-6)

    def forward(self, x):
        return self.ln(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2).contiguous()


class ConvTransformerTokensToEmbeddingNeck(nn.Module):
    """Drop CLS, reshape tokens to (E,14,14), upsample ×16 with two ConvTranspose pairs."""

    def __init__(self):
        super().__init__()
        self.Hp = self.Wp = IMG_SIZE // PATCH
        self.fpn1 = nn.Sequential(
            nn.ConvTranspose2d(EMBED, EMBED, kernel_size=2, stride=2), Norm2d(EMBED), nn.GELU(),
            nn.ConvTranspose2d(EMBED, EMBED, kernel_size=2, stride=2),
        )
        self.fpn2 = nn.Sequential(
            nn.ConvTranspose2d(EMBED, EMBED, kernel_size=2, stride=2), Norm2d(EMBED), nn.GELU(),
            nn.ConvTranspose2d(EMBED, EMBED, kernel_size=2, stride=2),
        )

    def forward(self, x):
        x = x[:, 1:, :].transpose(1, 2).reshape(x.shape[0], EMBED, self.Hp, self.Wp)
        return self.fpn2(self.fpn1(x))


class ConvModule(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, 3, padding=1, bias=False)
        self.bn = nn.BatchNorm2d(cout)
        self.activate = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.activate(self.bn(self.conv(x)))


class FCNHead(nn.Module):
    def __init__(self, num_convs: int, channels: int = 256, dropout: float = 0.1):
        super().__init__()
        convs = [ConvModule(EMBED, channels)] + [ConvModule(channels, channels) for _ in range(num_convs - 1)]
        self.convs = nn.Sequential(*convs)
        self.dropout = nn.Dropout2d(dropout)
        self.conv_seg = nn.Conv2d(channels, NUM_CLASSES, 1)

    def forward(self, x):
        return self.conv_seg(self.dropout(self.convs(x)))


class PrithviFlood(nn.Module):
    def __init__(self, with_aux: bool = True):
        super().__init__()
        self.backbone = TemporalViTEncoder()
        self.neck = ConvTransformerTokensToEmbeddingNeck()
        self.decode_head = FCNHead(num_convs=1)
        self.auxiliary_head = FCNHead(num_convs=2) if with_aux else None

    def forward(self, x, return_aux: bool = False):
        """x: (B,6,224,224) normalised reflectance → logits (B,2,224,224)."""
        feats = self.neck(self.backbone(x.unsqueeze(2)))
        out = self.decode_head(feats)
        if return_aux and self.auxiliary_head is not None:
            return out, self.auxiliary_head(feats)
        return out


def load_checkpoint(model: PrithviFlood, path: Path, strict: bool = True) -> dict:
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    sd = ckpt.get("state_dict", ckpt)
    if model.auxiliary_head is None:
        sd = {k: v for k, v in sd.items() if not k.startswith("auxiliary_head.")}
    missing, unexpected = model.load_state_dict(sd, strict=strict)
    return {"missing": list(missing), "unexpected": list(unexpected), "n_loaded": len(sd)}
