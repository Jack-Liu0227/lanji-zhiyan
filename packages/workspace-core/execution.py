"""Cooperative cancellation shared by models, tools and file writes."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from queue import Empty, Queue
import threading


class Cancelled(RuntimeError):
    pass


_cancel = ContextVar("execution_cancel", default=None)


def current_check():
    return _cancel.get()


def check():
    callback = current_check()
    if callback and callback():
        raise Cancelled("运行已中断")


@contextmanager
def scope(callback=None):
    token = _cancel.set(callback or current_check())
    try:
        check()
        yield
    finally:
        _cancel.reset(token)


def interruptible(function, *args, cancel_check=None, **kwargs):
    """Wait for model/network I/O without blocking cancellation of the caller.

    Only I/O belongs here: database and filesystem mutations stay on the caller.
    Late responses are discarded, and streaming callbacks must check cancellation.
    """
    callback = cancel_check or current_check()
    if not callback:
        return function(*args, **kwargs)
    if callback():
        raise Cancelled("运行已中断")
    result = Queue()
    for key in ("on_delta", "on_reasoning"):
        original = kwargs.get(key)
        if original:
            def forward(value, handler=original):
                if callback():
                    raise Cancelled("运行已中断")
                result.put(("callback", (handler, value)))
            kwargs[key] = forward
    context = copy_context()

    def invoke():
        try:
            value = context.run(function, *args, **kwargs)
            if callback():
                if hasattr(value, "close"):
                    value.close()
                return
            result.put((True, value))
        except BaseException as exc:
            if not callback():
                result.put((False, exc))

    threading.Thread(target=invoke, daemon=True, name="cancellable-io").start()
    while True:
        if callback():
            raise Cancelled("运行已中断")
        try:
            ok, value = result.get(timeout=0.05)
        except Empty:
            continue
        if ok == "callback":
            handler, payload = value
            handler(payload)
            continue
        if ok:
            return value
        raise value
