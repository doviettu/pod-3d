"""Fixed, free, local image tools that "Hỏi Claude" may use (no paid API is ever called here).

In the read and image modes Claude has no shell; claude_mcp.py exposes these commands as tools.
Inputs must be files under studio/runtime/; outputs always go to the chat's output folder
(POD_CLAUDE_OUT, set by the Studio), so Claude cannot write anywhere else through these tools.
Claude draws artwork itself as SVG and the `svg` command renders it to a print-size PNG.
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RUNTIME = HERE / 'runtime'


def out_dir():
    raw = os.environ.get('POD_CLAUDE_OUT', '')
    folder = Path(raw).resolve() if raw else None
    if folder is None or not folder.is_relative_to((RUNTIME / 'claude').resolve()) or not folder.is_dir():
        raise SystemExit('Chỉ chạy được từ khung Hỏi Claude của Studio.')
    return folder


def input_file(path):
    target = Path(path)
    if not target.is_absolute():
        # A bare name means a file Claude just wrote into the chat's output folder.
        target = out_dir() / target if len(target.parts) == 1 else HERE.parent / target
    target = target.resolve()
    if not target.is_relative_to(RUNTIME.resolve()) or not target.is_file():
        raise SystemExit(f'Chỉ dùng được ảnh trong studio/runtime/: {path}')
    return target


def output_file(name, suffix='.png'):
    stem = re.sub(r'[^\w.-]+', '-', Path(name).stem).strip('.-')[:80] or 'ket-qua'
    return out_dir() / f'{stem}{suffix}'


def parse_size(text):
    try:
        width, height = (int(v) for v in text.lower().split('x'))
    except ValueError:
        raise SystemExit('Kích thước phải dạng RỘNGxCAO, ví dụ 4500x5400.') from None
    if not (16 <= width <= 12000 and 16 <= height <= 12000):
        raise SystemExit('Kích thước phải từ 16 đến 12000 px.')
    return width, height


def show(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_svg(args):
    """Free, local: render an SVG that Claude wrote into a transparent PNG at an exact print size and DPI."""
    import resvg_py
    width, height = parse_size(args.size)
    source = input_file(args.svg)
    if source.suffix.lower() != '.svg':
        raise SystemExit('File vẽ phải là .svg.')
    fonts = [str(p) for p in (RUNTIME / 'fonts', Path('/System/Library/Fonts'), Path('/Library/Fonts')) if p.is_dir()]
    try:
        raw = resvg_py.svg_to_bytes(svg_path=str(source), width=width, height=height,
                                    resources_dir=str(source.parent), font_dirs=fonts)
    except ValueError as exc:
        raise SystemExit(f'SVG không vẽ được: {exc}') from None
    target = output_file(args.out)
    with Image.open(io.BytesIO(bytes(raw))) as img:
        img.convert('RGBA').save(target, format='PNG', dpi=(args.dpi, args.dpi))
        size = list(img.size)
    show({'file': str(target), 'size': size, 'dpi': args.dpi})


def cmd_art2d(args):
    """Free, local: pull the print art off a shirt photo into a transparent 4500x5400 PNG at 300 DPI."""
    import art2d
    sources = []
    for path in args.image[:2]:
        with Image.open(input_file(path)) as img:
            sources.append(np.asarray(img.convert('RGB')))
    folder = out_dir() / re.sub(r'[^\w.-]+', '-', args.name)[:60]
    outputs, _ = art2d.run(sources, folder, sides=args.sides, log=lambda *a: None)
    show({side: str(folder / info['file']) for side, info in outputs.items()})


def cmd_resize(args):
    """Free, local: resize/pad an image to an exact print size and set DPI."""
    width, height = parse_size(args.size)
    with Image.open(input_file(args.image)) as img:
        img = img.convert('RGBA')
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
    target = output_file(args.out)
    canvas.save(target, format='PNG', dpi=(args.dpi, args.dpi))
    show({'file': str(target), 'size': [width, height], 'dpi': args.dpi})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Công cụ ảnh cho Hỏi Claude')
    sub = parser.add_subparsers(dest='command', required=True)
    svg = sub.add_parser('svg', help='xuất file SVG thành PNG trong suốt đúng khổ in (miễn phí)')
    svg.add_argument('--svg', required=True, help='file .svg trong studio/runtime/')
    svg.add_argument('--size', default='4500x5400')
    svg.add_argument('--dpi', type=int, default=300)
    svg.add_argument('--out', required=True)
    svg.set_defaults(run=cmd_svg)
    art = sub.add_parser('art2d', help='tách art 2D từ ảnh áo (miễn phí)')
    art.add_argument('--image', action='append', required=True)
    art.add_argument('--name', default='art2d')
    art.add_argument('--sides', choices=['auto', 'front', 'back', 'both'], default='auto')
    art.set_defaults(run=cmd_art2d)
    resize = sub.add_parser('resize', help='đổi kích thước và DPI (miễn phí)')
    resize.add_argument('--image', required=True)
    resize.add_argument('--size', required=True, help='ví dụ 4500x5400')
    resize.add_argument('--dpi', type=int, default=300)
    resize.add_argument('--out', required=True)
    resize.set_defaults(run=cmd_resize)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == '__main__':
    main()
