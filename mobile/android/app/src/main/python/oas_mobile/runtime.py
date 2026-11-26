"""Cooperative controls shared by tasks, login and Android device operations."""
from __future__ import annotations

import threading
from contextlib import contextmanager


class TaskCancelled(InterruptedError):
    """The user stopped this task; this is not a successful completion."""


_LOCAL = threading.local()


@contextmanager
def task_control(cancel: threading.Event, on_pause=None):
    previous = getattr(_LOCAL, 'cancel', None)
    previous_pause = getattr(_LOCAL, 'on_pause', None)
    _LOCAL.cancel = cancel
    _LOCAL.on_pause = on_pause
    try:
        yield
    finally:
        _LOCAL.cancel = previous
        _LOCAL.on_pause = previous_pause


def checkpoint() -> None:
    cancel = getattr(_LOCAL, 'cancel', None)
    if cancel is None:
        return
    from . import bridge
    on_pause = getattr(_LOCAL, 'on_pause', None)
    entered_pause = False
    try:
        while True:
            if cancel.is_set():
                raise TaskCancelled('stopped by user')
            if not bridge.paused():
                return
            if not entered_pause:
                entered_pause = True
                if callable(on_pause):
                    try:
                        on_pause(True)
                    except Exception:
                        pass
            cancel.wait(.1)
    finally:
        if entered_pause and callable(on_pause):
            try:
                on_pause(False)
            except Exception:
                pass
