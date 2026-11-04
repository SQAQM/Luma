"""Cancellation is a request; ownership is released only after safe close."""
import threading


class Shutdown:
    def __init__(self):
        self.closing = threading.Event()
        self.lock = threading.RLock()
        self.active = {}

    def begin(self):
        with self.lock:
            self.closing.set()
            for event in self.active.values(): event.set()

    def finish(self, executor, service):
        self.begin()
        executor.shutdown(wait=True, cancel_futures=True)
        service.close()
