"""Regions of interest. Bounds must match the rows seeded by backend/sql/schema.sql."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    name: str
    bounds: list[float]          # [min_lon, min_lat, max_lon, max_lat]
    lat: float                   # representative point (city / gauge)
    lon: float

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.bounds[1] + self.bounds[3]) / 2, (self.bounds[0] + self.bounds[2]) / 2)


REGIONS: dict[str, Region] = {
    # Flood focus: Panchganga / Krishna floodplain (PLAN.md target region)
    "kolhapur-sangli": Region("kolhapur-sangli", [73.90, 16.35, 74.75, 17.05], 16.70, 74.24),
    # Drought focus: Beed district, Marathwada (PLAN.md alternate validation region)
    "marathwada-beed": Region("marathwada-beed", [75.30, 18.60, 76.30, 19.30], 18.99, 75.76),
}


def get_region(name: str) -> Region:
    if name not in REGIONS:
        raise KeyError(f"unknown region '{name}'; known: {sorted(REGIONS)}")
    return REGIONS[name]
