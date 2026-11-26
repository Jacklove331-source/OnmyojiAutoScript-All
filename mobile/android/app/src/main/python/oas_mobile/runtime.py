"""Cooperative controls shared by tasks, login and Android device operations."""
from __future__ import annotations

import threading
from contextlib import contextmanager


class TaskCancelled(InterruptedError):
    """The user stopped this task; this is not a successful completion."""


_LOCAL = threading.local()


@contextmanager
def task_control(cancel: threading.Event):
    previous = getattr(_LOCAL, 'cancel', None)
    _LOCAL.cancel = cancel
    try:
        yield
    finally:
        _LOCAL.cancel = previous


def checkpoint() -> None:
    cancel = getattr(_LOCAL, 'cancel', None)
    if cancel is None:
        return
    from . import bridge
    while True:
        if cancel.is_set():
            raise TaskCancelled('stopped by user')
        if not bridge.paused():
            return
        cancel.wait(.1)
