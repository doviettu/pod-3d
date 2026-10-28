"""Garment templates: panel sizes, how each panel is produced, and calibration data."""
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class Panel:
    name: str
    size: tuple  # (width, height) in px, taken from the sample print files
    source: str  # "unwrap" | "solid" | "copy"
    region: tuple = (0.0, 0.0, 1.0, 1.0)  # mockup area (x0, y0, x1, y1), normalized, where this panel is visible
    sample: tuple = None  # mockup area to sample the color of a solid panel
    copy_of: str = None  # for source == "copy"
    mirror: bool = False  # flip horizontally after copy
    method: str = "match"  # "match": feature matching + flow; "strip": band panel along a curve (sleeves);
    # "quad" / "tube": drawn from geometry, no sample print file (see geometry.py)
    curve: list = None  # strip/tube method: mockup polyline from seam to cuff, normalized
    halfwidth: float = 0.03  # strip/tube method: half width across the curve, normalized
    quad: list = None  # quad method: mockup corners TL, TR, BR, BL, normalized
    center: float = None  # quad method: mockup x of the panel's middle line, for a 3/4 view
    wrap: float = 0.6  # quad/tube: 0 flat cloth, 1 full cylinder
    hide: list = None  # quad/tube: mockup polygons covering this panel (another view in front)
    hint: str = ""  # marks: where to click, shown on the marking page


@dataclass
class Template:
    name: str
    dpi: int
    panels: list = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return ROOT / self.name

    def panel(self, name):
        return next(p for p in self.panels if p.name == name)

    def map_path(self, panel_name):
        return self.dir / f"map_{panel_name}.npz"

    @property
    def color_path(self):
        return self.dir / "color.npz"

    @property
    def silhouette_path(self):
        return self.dir / "silhouette.png"


def load(name: str) -> Template:
    data = json.loads((ROOT / name / "template.json").read_text())
    panels = [
        Panel(
            name=p["name"],
            size=tuple(p["size"]),
            source=p["source"],
            region=tuple(p.get("region", (0, 0, 1, 1))),
            sample=tuple(p["sample"]) if p.get("sample") else None,
            copy_of=p.get("copy_of"),
            mirror=p.get("mirror", False),
            method=p.get("method", "match"),
            curve=p.get("curve"),
            halfwidth=p.get("halfwidth", 0.03),
            hint=p.get("hint", ""),
            quad=p.get("quad"),
            center=p.get("center"),
            wrap=p.get("wrap", 0.6),
            hide=p.get("hide"),
        )
        for p in data["panels"]
    ]
    return Template(name=data["name"], dpi=data.get("dpi", 300), panels=panels)


def available():
    return sorted(p.name for p in ROOT.iterdir() if (p / "template.json").exists())
