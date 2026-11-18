"""Send heavy AI steps (Real-ESRGAN, LaMa, SAM 3) from the server to a faster worker (the Mac).

The worker connects out and long-polls the server (studio/worker.py), so nothing has to be
opened on the Mac. The server only offloads when POD_OFFLOAD=1 and a worker polled in the last
WORKER_TTL seconds; otherwise, or when the worker fails or is too slow, run() returns None and
the caller runs the step locally as before. Payloads are .npz (arrays) + a small JSON params dict.
"""
import io
import json
import os
import threading
import time
import uuid

import numpy as np

WORKER_TTL = 20        # s since the last poll for the worker to count as online
CLAIM_TIMEOUT = 15     # s a task may wait unclaimed before running locally
RESULT_TIMEOUT = 900   # s a claimed task may take before running locally


def pack(arrays):
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def unpack(data):
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


class Task:
    def __init__(self, kind, payload, params):
        self.id = uuid.uuid4().hex
        self.kind, self.payload, self.params = kind, payload, params
        self.claimed = None
        self.result = None
        self.error = None
        self.done = threading.Event()


class Broker:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Condition()
        self.pending = []
        self.tasks = {}
        self.last_poll = None
        self.stats = {'offloaded': 0, 'fallback': 0}

    def worker_online(self):
        """A worker counts as online when it polled recently or is busy with a task it claimed."""
        if any(t.claimed is not None and not t.done.is_set() for t in self.tasks.values()):
            return True
        return self.last_poll is not None and self.clock() - self.last_poll < WORKER_TTL

    def status(self):
        with self.lock:
            return {'worker_online': self.worker_online(), 'pending': len(self.pending),
                    'running': sum(1 for t in self.tasks.values() if t.claimed and not t.done.is_set()),
                    **self.stats}

    # server side, called from the pipeline threads
    def run(self, kind, arrays, params=None, claim_timeout=CLAIM_TIMEOUT, result_timeout=RESULT_TIMEOUT):
        with self.lock:
            if not self.worker_online():
                return None
            task = Task(kind, pack(arrays), params or {})
            self.tasks[task.id] = task
            self.pending.append(task)
            self.lock.notify_all()
        try:
            if not task.done.wait(claim_timeout):
                with self.lock:
                    if task.claimed is None:
                        self.pending.remove(task)
                        self.stats['fallback'] += 1
                        return None
                if not task.done.wait(result_timeout):
                    self.stats['fallback'] += 1
                    return None
            if task.error is not None:
                self.stats['fallback'] += 1
                return None
            self.stats['offloaded'] += 1
            return unpack(task.result)
        finally:
            with self.lock:
                self.tasks.pop(task.id, None)

    # worker side, called from the HTTP handler
    def next(self, wait=25.0):
        deadline = self.clock() + wait
        with self.lock:
            while True:
                self.last_poll = self.clock()
                if self.pending:
                    task = self.pending.pop(0)
                    task.claimed = self.clock()
                    return task
                left = deadline - self.clock()
                if left <= 0:
                    return None
                self.lock.wait(min(left, 5.0))

    def finish(self, task_id, result=None, error=None):
        with self.lock:
            self.last_poll = self.clock()
            task = self.tasks.get(task_id)
            if task is None or task.claimed is None or task.done.is_set():
                return False
            task.result, task.error = result, error
            task.done.set()
            return True


BROKER = Broker()


def enabled():
    return os.environ.get('POD_OFFLOAD') == '1'


def run(kind, arrays, params=None):
    """Result arrays from the worker, or None to run locally."""
    if not enabled():
        return None
    try:
        return BROKER.run(kind, arrays, params)
    except Exception:  # noqa: BLE001 - offload must never break the local path
        return None


def headers_for(task):
    return {'X-Task-Id': task.id, 'X-Task-Kind': task.kind, 'X-Task-Params': json.dumps(task.params)}
