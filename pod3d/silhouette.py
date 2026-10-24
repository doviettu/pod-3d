"""Is a mockup the same 3D shot as the template was calibrated on? Compared by the garment's
outline on the white background: the same mockup template with another design keeps it exactly,
another photo or pose does not, and its unwrap would cut the model, the jeans or the background."""
import cv2
import numpy as np

SIZE = 64
MIN_IOU = 0.9  # same shot measured 0.99+ on the samples, other shots 0.59-0.71


def outline(img):
    small = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    return small.min(2) < 235


def save(template, mockup):
    cv2.imwrite(str(template.silhouette_path), outline(mockup).astype(np.uint8) * 255)


def match(template, mockup):
    """IoU of the mockup's outline with the template's, or None when the template has none."""
    path = template.silhouette_path
    if not path.exists():
        return None
    ref = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) > 127
    m = outline(mockup)
    return float((ref & m).sum() / max((ref | m).sum(), 1))
