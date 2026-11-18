"""Per-job quality switch. "Chất lượng cao" trades time for fewer artefacts:
Real-ESRGAN with 8-way test-time augmentation and wider tile overlap, LaMa at a larger working size.
Set per job thread with hq_scope(); the worker (Mac) gets the flag in the task params."""
from contextlib import contextmanager
import contextvars

HQ = contextvars.ContextVar('pod3d_hq', default=False)


def enabled():
    return HQ.get()


@contextmanager
def hq_scope(flag):
    token = HQ.set(bool(flag))
    try:
        yield
    finally:
        HQ.reset(token)
