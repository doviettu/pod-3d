"""Trusted processing instructions, not calibrated garment geometry.

Market records contribute only known reference IDs. Panels name conceptual
regions; they do not define cutting patterns, dimensions, or unwrap maps.
"""
from copy import deepcopy


_POLICY = (
    'Style recognition only; no geometric maps or factory dimensions. '
    'Use only independently calibrated geometry for the exact template and view; '
    'otherwise require separate calibration and factory approval.'
)


def _style(id, label, features, panels, techniques, reference_ids, prompt):
    return dict(id=id, label=label, features=features, panels=panels,
                techniques=techniques, reference_ids=reference_ids, prompt=prompt,
                geometry_policy=_POLICY, production_ready=False)


_STYLES = [
    _style('hoodie_raglan', 'Hoodie · Pullover raglan',
           ['pullover', 'raglan', 'slim-fit'],
           ['Front', 'Back', 'Left', 'Right', 'Hood'],
           ['raglan-seam recognition', 'separate sleeve artwork recovery'],
           ['ind-raglan'],
           'Recognize diagonal raglan seams from neckline to underarm. Preserve '
           'diagonal seams and separate body from sleeves; never substitute '
           'set-in shoulder boundaries. Recover visible artwork per panel; '
           'do not invent hidden artwork or infer sewing geometry.'),
    _style('hoodie_zip', 'Hoodie · Full zip',
           ['full-zip', 'split-front'],
           ['FrontLeft', 'FrontRight', 'Back', 'Left', 'Right', 'Hood'],
           ['zipper recognition', 'split-front artwork recovery'],
           ['ind-heavy-zip', 'represent-zip'],
           'Recognize a full-length center zipper. Preserve zipper center '
           'separation: process left and right front independently, keep teeth '
           'and placket clear, and never paint or bridge artwork across the '
           'zipper. Preserve visible pocket and hood boundaries.'),
    _style('hoodie_boxy', 'Hoodie · Boxy drop shoulder',
           ['pullover', 'boxy', 'drop-shoulder', 'oversized'],
           ['Front', 'Back', 'Left', 'Right', 'Hood'],
           ['drop-shoulder recognition', 'body/sleeve boundary separation'],
           ['ind-boulevard'],
           'Recognize the wide boxy body and lowered drop shoulder seams. '
           'Distinguish drop shoulder body extensions from sleeve boundaries; '
           'do not use a raglan diagonal or a standard shoulder cut. Preserve '
           'body width and recover sleeves separately without stretching artwork.'),
    _style('hoodie_crop', 'Hoodie · Cropped pullover',
           ['pullover', 'cropped', 'short-body'],
           ['Front', 'Back', 'Left', 'Right', 'Hood'],
           ['cropped-hem recognition', 'crop safe-area artwork placement'],
           ['ind-crop'],
           'Recognize the shortened body and actual cropped hem. Keep the crop '
           'artwork safe area inside the visible body, clear of hem, seams and '
           'pockets; do not extend the torso or squeeze a full-length graphic '
           'onto it. Determine print margins only from approved calibration.'),
    _style('hoodie_pullover', 'Hoodie · Standard pullover',
           ['pullover', 'continuous-front'],
           ['Front', 'Back', 'Left', 'Right', 'Hood'],
           ['pullover recognition', 'panel-wise artwork recovery'],
           ['ind-loopback', 'gymshark-crest'],
           'Recognize a continuous pullover front without a center zipper. '
           'Separate body, sleeves, hood and visible pocket boundaries. Do not '
           'assume raglan seams from an athletic or slim fit. Preserve observed '
           'fabric texture; reconstruct only artwork supported by visible views.'),
    _style('hoodie_blanket', 'Hoodie · Wearable blanket',
           ['hooded-blanket', 'oversized', 'sherpa-lined'],
           ['Front', 'Back', 'Left', 'Right', 'Hood'],
           ['wearable-blanket recognition', 'repeat motif recovery'],
           ['oodie-avocado'],
           'Recognize an oversized wearable blanket with hood, large pocket and '
           'lining. Require separate calibration for the oversized blanket; '
           'never reuse standard hoodie dimensions or maps. Recover visible '
           'repeat motifs without claiming all-over printing or a sewing pattern '
           'from retail photos; keep lining and outer fabric separate.'),
    _style('sweater', 'Sweater', ['sweater', 'no-hood'],
           ['Front', 'Back', 'Left', 'Right', 'Collar', 'Hem'],
           ['neckline recognition', 'rib/knit texture preservation'], [],
           'Recognize a sweater neckline without a hood. Preserve collar, rib '
           'and knit texture and separate sleeve boundaries. Existing sweater '
           'calibration belongs only to its exact template; do not transfer it '
           'to another silhouette or invent hidden artwork.'),
    _style('tshirt', 'T-shirt · Back only', ['tshirt', 'back-only'], ['Back'],
           ['back-view recognition', 'visible back artwork recovery'], [],
           'Recognize the T-shirt back view and recover only visible back '
           'artwork. Preserve neckline, armholes and hem. Do not invent the '
           'front, sleeves or collar artwork; reference pixels are not factory '
           'print dimensions. Require exact-template calibration for geometry.'),
]

_TEMPLATE_STYLES = {
    'sweater': 'sweater',
    'hoodie': 'hoodie_pullover',
    'hoodie_back_front_34': 'hoodie_pullover',
    'hoodie_front_back': 'hoodie_pullover',
    'hoodie_model_front_back': 'hoodie_pullover',
    'hoodie_halloween_team': 'hoodie_pullover',
    'hoodie_pumpkin_texture': 'hoodie_pullover',
    'tshirt_stepback_back': 'tshirt',
}


def style_catalog(market_items):
    """Return eight fresh style records, linking only supplied known references.

    ``market_items`` is the market library's ``items`` list. Its text, URLs,
    style tags and readiness flags never become processing instructions.
    """
    ids = {item['id'] for item in market_items
           if isinstance(item, dict) and isinstance(item.get('id'), str)}
    styles = deepcopy(_STYLES)
    for style in styles:
        style['reference_ids'] = [id for id in style['reference_ids'] if id in ids]
    return styles


def style_for_template(name):
    """Return the style ID for an explicitly known template, otherwise None.

    This mapping conveys garment type, never calibration or production status.
    """
    return _TEMPLATE_STYLES.get(name) if isinstance(name, str) else None


def validate_style(id):
    """Return a fresh trusted style record with its known reference IDs.

    Raise ValueError for any unknown or malformed ID; never accept client
    metadata as a style definition.
    """
    for style in _STYLES:
        if isinstance(id, str) and style['id'] == id:
            return deepcopy(style)
    raise ValueError('Unknown garment style ID.')
