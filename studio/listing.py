"""Listing photo set: 2000 × 2000 sRGB JPEGs under 1 MB, plus a ZIP, for Etsy-style shops.

Etsy (policy of 2026-10-22) wants at least one real, unedited photo of the product in every
listing, so the set refuses to build without the job's real photo and always puts it first,
untouched apart from the square crop.
"""
import io
from pathlib import Path
import zipfile

from PIL import Image, ImageOps

EDGE = 2000
MAX_BYTES = 1_000_000
# Common blank colours; the flat previews show how the design reads on each.
SHIRT_COLORS = {'trang': (245, 245, 242), 'den': (24, 24, 26), 'xam': (160, 162, 165), 'navy': (31, 40, 66)}
NOTES = (
    'Bộ ảnh listing tạo bởi POD Studio.\n'
    '- Ảnh 01 là ảnh thật của sản phẩm (Etsy yêu cầu ít nhất 1 ảnh thật chưa chỉnh từ 22/10/2026). '
    'Không thay ảnh này bằng mockup.\n'
    '- Các ảnh còn lại là mockup/xem trước: khai báo có dùng công cụ tạo ảnh/AI theo quy định của sàn.\n'
    '- Đăng listing thủ công hoặc ở chế độ nháp; Etsy cấm listing do bot tạo hoàn toàn.\n'
)


def save_jpeg(image, path, max_bytes=MAX_BYTES):
    """Highest JPEG quality that fits under max_bytes (sRGB, no metadata)."""
    image = image.convert('RGB')
    data = b''
    for quality in (92, 88, 84, 80, 75, 70, 64, 56, 48, 40, 32, 24):
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=quality, optimize=True, progressive=True)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            break
    Path(path).write_bytes(data)
    return len(data)


def square_cover(image, edge=EDGE):
    return ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'), (edge, edge), Image.Resampling.LANCZOS)


def square_contain(image, edge=EDGE, background=(255, 255, 255), margin=0.06):
    image = ImageOps.exif_transpose(image)
    inner = round(edge * (1 - 2 * margin))
    image = ImageOps.contain(image, (inner, inner), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (edge, edge), background)
    position = ((edge - image.width) // 2, (edge - image.height) // 2)
    if image.mode in ('RGBA', 'LA'):
        canvas.paste(image, position, image.getchannel('A'))
    else:
        canvas.paste(image.convert('RGB'), position)
    return canvas


def flat_preview(design_png, color, edge=EDGE):
    """Design centred on a plain fabric-coloured square, about the size it prints on a chest."""
    with Image.open(design_png) as design:
        design = design.convert('RGBA')
    box = design.getbbox()
    if box:
        design = design.crop(box)
    return square_contain(design, edge, color, margin=0.16)


def build(out_dir, real_photo, mockups=(), designs=(), panels=()):
    """real_photo: path (required). mockups: source photos. designs: [(label, transparent PNG, colour names)].
    panels: [(label, image)] for 3D print panels. Returns {'files': [...], 'zip': name}."""
    if not real_photo or not Path(real_photo).is_file():
        raise ValueError('Chưa có ảnh mẫu thật: hãy thêm ảnh chụp sản phẩm thật trước khi tạo bộ ảnh listing.')
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*.jpg'):
        old.unlink()
    shots = []

    def add(name, image):
        n = len(shots) + 1
        file = f'{n:02d}-{name}.jpg'
        size = save_jpeg(image, out / file)
        shots.append({'file': file, 'bytes': size})

    with Image.open(real_photo) as photo:
        add('anh-that', square_cover(photo))
    for i, mockup in enumerate(mockups):
        with Image.open(mockup) as image:
            add(f'mockup-{i + 1}', square_contain(image))
    for label, design, colors in designs:
        for color_name in colors:
            add(f'{label}-{color_name}', flat_preview(design, SHIRT_COLORS[color_name]))
    for label, panel in panels:
        with Image.open(panel) as image:
            add(f'manh-{label}', square_contain(image, background=(238, 238, 238)))
    (out / 'GHI-CHU.txt').write_text(NOTES, encoding='utf-8')
    zip_name = 'listing.zip'
    with zipfile.ZipFile(out / zip_name, 'w', zipfile.ZIP_STORED) as archive:   # JPEGs do not compress
        for shot in shots:
            archive.write(out / shot['file'], shot['file'])
        archive.write(out / 'GHI-CHU.txt', 'GHI-CHU.txt')
    return {'files': shots, 'zip': zip_name, 'edge': EDGE}
