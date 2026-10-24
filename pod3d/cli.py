"""Command line: `python -m pod3d run mockup.jpg --template sweater` and `python -m pod3d calibrate ...`."""
import argparse
import json
from pathlib import Path

from . import calibrate, pipeline, template


def _sets_from_dirs(dirs, tpl):
    """Each calibration folder holds one mockup (any name with 'mockup' or the only non-panel image)
    plus the print files named after the panels (Front.jpg, Back.jpg, ...)."""
    names = {p.name.lower() for p in tpl.panels}
    sets = []
    for d in dirs:
        d = Path(d)
        imgs = [p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        s = {}
        for p in imgs:
            if p.stem.lower() in names:
                s[next(x.name for x in tpl.panels if x.name.lower() == p.stem.lower())] = p
        rest = [p for p in imgs if p.stem.lower() not in names]
        if len(rest) != 1:
            raise SystemExit(f"{d}: expected exactly one mockup image besides the panels, found {[p.name for p in rest]}")
        s["mockup"] = rest[0]
        sets.append(s)
    return sets


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pod3d")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="make print panels from a mockup")
    r.add_argument("mockup")
    r.add_argument("--template", "-t", required=True, choices=template.available())
    r.add_argument("--out", "-o", help="output folder (default: next to the mockup)")
    r.add_argument("--no-model", action="store_true", help="skip the AI upscaler, Lanczos only")
    r.add_argument("--png", action="store_true", help="write PNG instead of JPG")
    c = sub.add_parser("calibrate", help="learn a template from sample folders (mockup + print files)")
    c.add_argument("template")
    c.add_argument("folders", nargs="+")
    sub.add_parser("templates", help="list templates")
    a = ap.parse_args(argv)
    if a.cmd == "templates":
        for n in template.available():
            t = template.load(n)
            print(n, json.dumps({p.name: list(p.size) for p in t.panels}))
    elif a.cmd == "calibrate":
        t = template.load(a.template)
        calibrate.run(t, _sets_from_dirs(a.folders, t))
    else:
        t = template.load(a.template)
        out = Path(a.out) if a.out else Path(a.mockup).with_name(Path(a.mockup).stem + "_panels")
        pipeline.run(t, a.mockup, out, use_model=not a.no_model, fmt="png" if a.png else "jpg")


if __name__ == "__main__":
    main()
