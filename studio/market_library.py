"""Read-only, sourced market references; never calibrated sewing/print templates."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

DEFAULT = Path(__file__).resolve().parent / 'hoodie_market_library.json'


def _https(value):
    if not isinstance(value,str) or len(value) > 2048:
        raise ValueError('Liên kết nguồn không hợp lệ.')
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('Nguồn tham chiếu phải là HTTPS công khai.')
    return value


def load_market_library(path=DEFAULT):
    with Path(path).open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Thư viện tham chiếu quá lớn.')
    data = json.loads(raw)
    if (not isinstance(data,dict) or data.get('version') != 1
            or data.get('purpose') != 'market-reference-not-sewing-pattern'
            or not isinstance(data.get('researched_on'),str)
            or not isinstance(data.get('items'),list) or len(data['items']) > 100):
        raise ValueError('Thư viện tham chiếu không hợp lệ.')
    ids = set()
    required = {'id','label','brand','product_title','product_url','shopify_evidence_url',
                'evidence_note','style_tags','notes','image_url','manufacturing_ready'}
    for item in data['items']:
        if not isinstance(item,dict) or set(item) != required or item['manufacturing_ready'] is not False:
            raise ValueError('Ảnh thị trường không phải mẫu rập sản xuất.')
        if (not isinstance(item['id'],str) or not re.fullmatch(r'[a-z0-9-]{1,80}',item['id'])
                or item['id'] in ids):
            raise ValueError('Mã tham chiếu trùng hoặc không hợp lệ.')
        ids.add(item['id'])
        for key in ('label','brand','product_title','evidence_note','notes'):
            if not isinstance(item[key],str) or not 1 <= len(item[key]) <= 2000:
                raise ValueError('Mô tả tham chiếu không hợp lệ.')
        for key in ('product_url','shopify_evidence_url'):
            _https(item[key])
        if item['image_url'] is not None:
            _https(item['image_url'])
        tags = item['style_tags']
        if (not isinstance(tags,list) or not 1 <= len(tags) <= 16
                or any(not isinstance(tag,str) or not re.fullmatch(r'[a-z0-9-]{1,50}',tag) for tag in tags)):
            raise ValueError('Nhóm kiểu dáng không hợp lệ.')
    return data
