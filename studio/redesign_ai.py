"""Redesign: let Claude Code on this Mac read the design and describe its structure.

Uses the same signed-in `claude -p` as the Hỏi Claude tab, in read-only mode (no shell, no file
writes), so it costs no API money. Claude looks at the design with the step 1 layer boxes drawn
on it and returns JSON: subject, text, layout, style, typography, motifs, effects, print issues,
why it sells, a short name for every layer, and three redesign concepts that fill the
keep/change/remove choices. Everything it returns is checked and cut to size before use.
"""
import json
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw, ImageFont

import claude_chat

HERE = Path(__file__).resolve().parent
TIMEOUT = 240
PREVIEW_EDGE = 1400
FIELDS = ('subject', 'layout', 'style', 'typography', 'effects')
LISTS = ('text', 'motifs', 'print_issues')
SELLS = ('hook', 'formula', 'niche', 'tone', 'audience')

PROMPT = """Read the image file {boxes} with the Read tool. It is a print-on-demand garment design; the coloured boxes
with ids ({ids}) mark the layers a script found (L = layer). Layer list: {layers}

Analyse the design's structure and spirit, not just what is in it, then answer with ONLY one JSON object, no other text:
{{
 "subject": "main subject and what it is doing",
 "text": ["every line of text, exactly as spelled"],
 "layout": "how the parts are arranged (e.g. text arched above, subject centred, small motifs around)",
 "style": "illustration style (e.g. vintage retro, distressed screenprint, kawaii, hand-drawn)",
 "typography": "type styles, weights, sizes, positions",
 "motifs": ["each decorative motif with its position"],
 "effects": "print effects such as outline, distressed texture, halftone, shadow",
 "print_issues": ["problems for printing, e.g. soft glow, thin lines, cut at edge; empty list if none"],
 "sells": {{"hook": "why a buyer stops (pun, identity, humour...)", "formula": "the sellable formula with a [placeholder], e.g. A Halloween phrase reinterpreted through a [animal]'s habits", "niche": "Niche > sub-niche > occasion", "tone": "tone", "audience": "who buys it"}},
 "layers": {{"L1": "short English name of what this layer is"}},
 "concepts": [
  {{"title": "tên ngắn bằng tiếng Việt", "goal": "one English sentence: the redesign direction that keeps the formula",
    "layers": {{"L1": {{"action": "keep|change|remove", "note": "English: how to change it (empty when keep)"}}}}}}
 ]
}}
Give exactly 3 concepts that each change only a few layers and keep the sellable formula (e.g. another holiday,
another animal, another niche). Use only the layer ids listed.
"""


def boxes_image(source, scene, target):
    """The design on a light checkerboard with each layer's box and id, saved for Claude to Read."""
    w, h = scene['size']
    scale = min(1.0, PREVIEW_EDGE / max(w, h))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    board = Image.new('RGBA', size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(board)
    step = max(8, size[0] // 40)
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            if (x // step + y // step) % 2:
                draw.rectangle([x, y, x + step, y + step], fill=(232, 232, 236, 255))
    board.alpha_composite(source.convert('RGBA').resize(size, Image.Resampling.LANCZOS))
    draw = ImageDraw.Draw(board)
    try:
        font = ImageFont.load_default(max(14, size[0] // 50))
    except TypeError:
        font = ImageFont.load_default()
    colours = [(37, 99, 235), (234, 88, 12), (22, 163, 74), (190, 24, 93), (124, 58, 237), (8, 145, 178)]
    for i, layer in enumerate(scene['layers']):
        x, y, bw, bh = (round(v * scale) for v in layer['bbox'])
        c = colours[i % len(colours)]
        draw.rectangle([x, y, x + bw, y + bh], outline=(*c, 255), width=max(2, size[0] // 400))
        draw.text((x + 3, y + 2), layer['id'], fill=(*c, 255), font=font, stroke_width=3, stroke_fill=(255, 255, 255, 255))
    board.convert('RGB').save(target)
    return target


def prompt(scene, boxes_rel):
    layers = '; '.join(f'{l["id"]} {l["type"]} "{l["label"]}" bbox {l["bbox"]}' for l in scene['layers'])
    return PROMPT.format(boxes=boxes_rel, ids=', '.join(l['id'] for l in scene['layers']), layers=layers)


def _text(v, limit=400):
    return re.sub(r'\s+', ' ', v).strip()[:limit] if isinstance(v, str) else ''


def parse(answer, scene):
    """The checked analysis dict from Claude's answer text. Raises ValueError when there is none."""
    match = re.search(r'\{.*\}', answer or '', re.S)
    if not match:
        raise ValueError('Claude không trả về JSON phân tích.')
    try:
        data = json.loads(match.group(0))
    except ValueError:
        raise ValueError('JSON phân tích của Claude bị lỗi; bấm phân tích lại.') from None
    if not isinstance(data, dict):
        raise ValueError('JSON phân tích của Claude không đúng dạng.')
    ids = {l['id'] for l in scene['layers']}
    out = {k: _text(data.get(k)) for k in FIELDS}
    for k in LISTS:
        out[k] = [_text(v, 200) for v in data.get(k) or [] if _text(v, 200)][:20] if isinstance(data.get(k), list) else []
    sells = data.get('sells') if isinstance(data.get('sells'), dict) else {}
    out['sells'] = {k: _text(sells.get(k)) for k in SELLS}
    names = data.get('layers') if isinstance(data.get('layers'), dict) else {}
    out['layers'] = {k: _text(v, 80) for k, v in names.items() if k in ids and _text(v, 80)}
    concepts = []
    for c in (data['concepts'] if isinstance(data.get('concepts'), list) else []):
        if not isinstance(c, dict):
            continue
        picks = {}
        for lid, pick in (c['layers'].items() if isinstance(c.get('layers'), dict) else []):
            if lid in ids and isinstance(pick, dict) and pick.get('action') in ('keep', 'change', 'remove'):
                picks[lid] = {'action': pick['action'], 'note': _text(pick.get('note'), 500)}
        if _text(c.get('title'), 80) and picks:
            concepts.append({'title': _text(c.get('title'), 80), 'goal': _text(c.get('goal'), 600), 'layers': picks})
    out['concepts'] = concepts[:3]
    if not out['subject'] and not out['layers']:
        raise ValueError('Claude không phân tích được thiết kế này.')
    return out


def run_claude(text, cwd, finder=claude_chat.find_claude, run=subprocess.run, timeout=TIMEOUT):
    """One read-only `claude -p` call; returns the final answer text."""
    binary = finder()
    if not binary:
        raise ValueError('Chưa có Claude Code trên Mac (cài rồi chạy claude một lần để đăng nhập).')
    out = Path(cwd) / 'studio' / 'runtime' / 'claude'
    try:
        proc = run(claude_chat.command(binary, 'read', out, None, cwd), cwd=cwd, env=claude_chat.environment(cwd, out),
                   input=text, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError(f'Claude phân tích quá {timeout} giây; thử lại sau.') from None
    except OSError as exc:
        raise ValueError(f'Không chạy được Claude Code: {exc}') from None
    for line in reversed(proc.stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'result':
            if event.get('is_error') or event.get('subtype') != 'success':
                raise ValueError(f'Claude báo lỗi: {str(event.get("result") or event.get("subtype"))[:300]}')
            return event.get('result') or ''
    raise ValueError((proc.stderr.strip() or f'Claude Code dừng (mã {proc.returncode}).')[-300:])


def analyze(folder, scene, cwd=HERE.parent, runner=run_claude):
    folder = Path(folder)
    with Image.open(folder / 'source.png') as source:
        target = boxes_image(source, scene, folder / 'boxes.jpg')
    rel = target.relative_to(cwd) if target.is_relative_to(cwd) else target
    return parse(runner(prompt(scene, rel), cwd), scene)
