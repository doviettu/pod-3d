"""Copyright / trademark screening for every job (a helper filter, not legal advice).

Cheap checks first, all local and free:
1. Internal blacklist: perceptual hash (pHash) of art the user marked Red. A match is Red.
2. Brand keywords found in the text read from the art (Tesseract OCR when installed) or in
   the source link / file name (shop links often carry the title: ".../street-fighter-sweater").
   A hit is Yellow.
3. Nothing found: Green, meaning "no sign seen", never "safe".
The AI check (logos, characters, celebrities) rides on the AI design-brief call when that runs;
reverse image search and the USPTO search are buttons the user opens by hand.
"""
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from urllib.parse import quote, unquote

import cv2
import numpy as np

HAMMING_MATCH = 10   # of 64 bits; re-encodes and resizes of the same art stay under ~6
DEFAULT_KEYWORDS = [
    # studios, franchises, characters
    'disney', 'pixar', 'marvel', 'avengers', 'spider-man', 'spiderman', 'star wars', 'mandalorian', 'mickey',
    'minnie', 'frozen', 'lilo', 'stitch', 'winnie the pooh', 'dc comics', 'batman', 'superman', 'joker',
    'harry potter', 'hogwarts', 'lord of the rings', 'pokemon', 'pikachu', 'nintendo', 'mario', 'zelda',
    'sonic', 'sega', 'capcom', 'street fighter', 'mortal kombat', 'hello kitty', 'sanrio', 'looney tunes',
    'bugs bunny', 'scooby', 'snoopy', 'peanuts', 'garfield', 'simpsons', 'spongebob', 'sesame street',
    'grinch', 'dr seuss', 'home alone', 'friends tv', 'star trek', 'stranger things', 'game of thrones',
    'naruto', 'dragon ball', 'one piece', 'demon slayer', 'studio ghibli', 'totoro', 'barbie', 'transformers',
    'care bears', 'teenage mutant', 'ninja turtles', 'nightmare before christmas', 'jack skellington',
    # sports leagues and teams
    'nfl', 'nba', 'mlb', 'nhl', 'fifa', 'ncaa', 'super bowl', 'olympic',
    # fashion and consumer brands
    'nike', 'adidas', 'supreme', 'gucci', 'louis vuitton', 'chanel', 'prada', 'versace', 'balenciaga',
    'coca-cola', 'coca cola', 'pepsi', 'starbucks', 'harley-davidson', 'jeep', 'ford mustang', 'ferrari',
    'playstation', 'xbox', 'netflix', 'mcdonald',
]


def phash(image):
    """64-bit DCT perceptual hash as a 16-char hex string. image: BGR/RGB/RGBA/gray array."""
    if image.ndim == 3 and image.shape[2] == 4:
        a = image[..., 3:4].astype(np.float32) / 255
        image = (image[..., :3].astype(np.float32) * a + 128 * (1 - a)).astype(np.uint8)
    gray = image if image.ndim == 2 else cv2.cvtColor(image[..., :3], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].ravel()
    bits = low > np.median(low[1:])   # median without the DC term
    return f'{int("".join("1" if b else "0" for b in bits), 2):016x}'


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count('1')


def _normal(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def find_keywords(text, keywords):
    """Keywords appearing as whole words in text (case, accents, dashes and underscores ignored)."""
    hay = f' {_normal(text)} '
    found = []
    for word in keywords:
        w = _normal(word)
        if w and f' {w} ' in hay and word not in found:
            found.append(word)
    return found


def ocr_available():
    return shutil.which('tesseract') is not None


def read_text(image_path, timeout=30):
    """Text on the art via the Tesseract command line (Apache-2.0), or None when not installed."""
    exe = shutil.which('tesseract')
    if not exe:
        return None
    try:
        done = subprocess.run([exe, str(image_path), 'stdout', '--psm', '11'], capture_output=True,
                              timeout=timeout, check=False)
        return done.stdout.decode('utf-8', 'ignore')[:4000]
    except (OSError, subprocess.SubprocessError):
        return None


def lookup_links(source_url=None, text=''):
    links = []
    if source_url:
        links.append({'label': 'Google Lens', 'url': 'https://lens.google.com/uploadbyurl?url=' + quote(source_url, safe='')})
        links.append({'label': 'TinEye', 'url': 'https://tineye.com/search?url=' + quote(source_url, safe='')})
    words = ' '.join(_normal(text).split()[:8])
    if len(words) >= 3:
        links.append({'label': 'USPTO (nhãn hiệu chữ)',
                      'url': 'https://tmsearch.uspto.gov/search/search-results?query=' + quote(words)})
    return links


class RightsChecker:
    def __init__(self, runtime):
        self.path = Path(runtime) / 'rights.json'
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        return {'keywords': data.get('keywords', list(DEFAULT_KEYWORDS)), 'blacklist': data.get('blacklist', [])}

    def _save(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def config(self):
        data = self._load()
        return {'keywords': data['keywords'], 'blacklist_count': len(data['blacklist']),
                'ocr': ocr_available(),
                'note': 'Bộ lọc hỗ trợ, không phải ý kiến pháp lý. Xanh nghĩa là chưa thấy dấu hiệu.'}

    def set_keywords(self, words):
        if not isinstance(words, list) or len(words) > 2000 or not all(isinstance(w, str) and len(w) <= 80 for w in words):
            raise ValueError('Danh sách từ khoá không hợp lệ (tối đa 2000 từ, mỗi từ 80 ký tự).')
        clean = []
        for w in words:
            w = w.strip()
            if w and w.lower() not in (c.lower() for c in clean):
                clean.append(w)
        with self.lock:
            data = self._load()
            data['keywords'] = clean
            self._save(data)
        return self.config()

    def check(self, images, *, source_url=None, extra_text=''):
        """images: [(label, array or path)]. Returns (level, reason, evidence)."""
        data = self._load()
        hashes, texts, matches = [], [], []
        for label, image in images:
            path = None
            if isinstance(image, (str, Path)):
                path = Path(image)
                image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if image is None:
                    continue
            h = phash(image)
            hashes.append({'label': label, 'hash': h})
            for entry in data['blacklist']:
                if hamming(h, entry['hash']) <= HAMMING_MATCH:
                    matches.append({'label': label, 'sku': entry.get('sku'), 'reason': entry.get('reason')})
            if ocr_available():
                if path is None:
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        path = Path(tmp.name)
                    cv2.imwrite(str(path), image)
                    text = read_text(path)
                    path.unlink(missing_ok=True)
                else:
                    text = read_text(path)
                if text and text.strip():
                    texts.append(text.strip())
        link_text = unquote(source_url or '') + ' ' + (extra_text or '')
        art_words = find_keywords(' '.join(texts), data['keywords'])
        link_words = find_keywords(link_text, data['keywords'])
        evidence = {'checked_at': datetime.now().isoformat(timespec='seconds'), 'hashes': hashes,
                    'blacklist_matches': matches, 'ocr': ocr_available(), 'text': ' | '.join(texts)[:1000],
                    'keywords_in_art': art_words, 'keywords_in_link': link_words,
                    'links': lookup_links(source_url, ' '.join(texts)), 'ai': None}
        if matches:
            m = matches[0]
            return 'red', f'Trùng art đã đánh dấu Đỏ ({m.get("sku") or "danh sách đen"}).', evidence
        if art_words or link_words:
            where = (['chữ trên art: ' + ', '.join(art_words)] if art_words else []) + \
                    (['link/tên: ' + ', '.join(link_words)] if link_words else [])
            return 'yellow', 'Có từ khoá thương hiệu (' + '; '.join(where) + '). Hãy kiểm tra trước khi duyệt.', evidence
        missing = '' if ocr_available() else ' Chưa đọc chữ trên art (cài Tesseract để bật).'
        return 'green', 'Chưa thấy dấu hiệu bản quyền (không phải bảo đảm).' + missing, evidence

    def remember_red(self, job, reason=''):
        """Add a Red job's art hashes to the blacklist so the same art is caught next time."""
        hashes = ((job.get('rights_evidence') or {}).get('hashes')) or []
        with self.lock:
            data = self._load()
            data['blacklist'] = [e for e in data['blacklist'] if e.get('job') != job['id']]
            for h in hashes:
                data['blacklist'].append({'hash': h['hash'], 'job': job['id'], 'sku': job.get('sku'),
                                          'label': h.get('label'), 'reason': reason[:200],
                                          'added': datetime.now().isoformat(timespec='seconds')})
            self._save(data)
        return len(hashes)

    def forget(self, job_id):
        with self.lock:
            data = self._load()
            before = len(data['blacklist'])
            data['blacklist'] = [e for e in data['blacklist'] if e.get('job') != job_id]
            self._save(data)
        return before - len(data['blacklist'])
