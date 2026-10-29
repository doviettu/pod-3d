"""Fixed image tools that "Hỏi Claude" may run when the Studio is opened through the online link.

Online, Claude cannot run arbitrary commands; it may only run this script:
    .venv/bin/python studio/claude_tools.py <command> ...
Inputs must be files under studio/runtime/; outputs always go to the chat's output folder
(POD_CLAUDE_OUT, set by the Studio), so Claude cannot write anywhere else through these tools.
Paid OpenAI calls go through the same budget cap and ledger as the rest of the Studio.
"""
import argparse
import base64
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
import ai_sheet  # noqa: E402
import openai_api  # noqa: E402

RUNTIME = HERE / 'runtime'
MAX_INPUTS = 4
MAX_PROMPT = 4000
SIZES = {'1K': '1024x1024', '2K': '2048x2048'}


def out_dir():
    raw = os.environ.get('POD_CLAUDE_OUT', '')
    folder = Path(raw).resolve() if raw else None
    if folder is None or not folder.is_relative_to((RUNTIME / 'claude').resolve()) or not folder.is_dir():
        raise SystemExit('Chỉ chạy được từ khung Hỏi Claude của Studio.')
    return folder


def input_file(path):
    target = Path(path)
    target = (target if target.is_absolute() else HERE.parent / target).resolve()
    if not target.is_relative_to(RUNTIME.resolve()) or not target.is_file():
        raise SystemExit(f'Chỉ dùng được ảnh trong studio/runtime/: {path}')
    return target


def output_file(name, suffix='.png'):
    stem = re.sub(r'[^\w.-]+', '-', Path(name).stem).strip('.-')[:80] or 'ket-qua'
    return out_dir() / f'{stem}{suffix}'


def show(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_budget(args):
    show(ai_sheet.Budget(RUNTIME).status())


def cmd_ai_image(args):
    """One paid OpenAI image call: generate from text, or edit/compose from up to 4 reference images."""
    if not args.prompt.strip() or len(args.prompt) > MAX_PROMPT:
        raise SystemExit(f'Prompt phải có từ 1 đến {MAX_PROMPT} ký tự.')
    if len(args.image) > MAX_INPUTS:
        raise SystemExit(f'Tối đa {MAX_INPUTS} ảnh tham chiếu.')
    settings = openai_api.Settings()
    budget = ai_sheet.Budget(RUNTIME)
    usd = budget.price(args.size, args.quality)
    if args.dry_run:
        show({'estimated_usd': usd, **budget.status()})
        return
    try:
        budget.check(usd)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    target = output_file(args.out)
    payload = {'model': settings.model, 'prompt': args.prompt, 'n': 1, 'size': SIZES[args.size],
               'quality': args.quality, 'output_format': 'png'}
    endpoint = 'images/generations'
    if args.image:
        refs = []
        for path in args.image:
            with Image.open(input_file(path)) as img:
                img = img.convert('RGB')
                img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
            refs.append({'image_url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()})
        payload['images'] = refs
        endpoint = 'images/edits'
    try:
        result = openai_api._request(settings, payload, endpoint=endpoint)
        raw = base64.b64decode(result['data'][0]['b64_json'], validate=True)
        with Image.open(io.BytesIO(raw)) as generated:
            generated.load()
            generated.save(target, format='PNG')
    except openai_api.OpenAIError as exc:
        raise SystemExit(str(exc)) from None
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        raise SystemExit('OpenAI không trả về ảnh hợp lệ; chưa tự động thử lại.') from None
    budget.record({'kind': 'claude-chat', 'usd': usd, 'size': args.size, 'quality': args.quality,
                   'model': settings.model, 'file': target.name})
    show({'file': str(target), 'estimated_usd': usd})


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
    width, height = (int(v) for v in args.size.lower().split('x'))
    if not (16 <= width <= 12000 and 16 <= height <= 12000):
        raise SystemExit('Kích thước phải từ 16 đến 12000 px.')
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
    sub.add_parser('budget', help='xem trần ngân sách AI và số đã dùng').set_defaults(run=cmd_budget)
    ai = sub.add_parser('ai-image', help='tạo/sửa ảnh bằng OpenAI (tốn phí)')
    ai.add_argument('--prompt', required=True)
    ai.add_argument('--out', required=True, help='tên file kết quả')
    ai.add_argument('--image', action='append', default=[], help='ảnh tham chiếu trong studio/runtime/')
    ai.add_argument('--size', choices=sorted(SIZES), default='1K')
    ai.add_argument('--quality', choices=openai_api.QUALITIES, default='medium')
    ai.add_argument('--dry-run', action='store_true', help='chỉ ước tính chi phí')
    ai.set_defaults(run=cmd_ai_image)
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
