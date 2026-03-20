"""
Thread-safe token-bucket rate limiter.
Usage:
    limiter = RateLimiter(requests_per_second=3.0)
    limiter.acquire()   # blocks until a token is available
    # ... make API call
"""
import threading
import time


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._rate = requests_per_second
        self._tokens = requests_per_second
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                sleep_time = (1.0 - self._tokens) / self._rate
            else:
                sleep_time = 0.0
            self._tokens -= 1.0

        if sleep_time > 0:
            time.sleep(sleep_time)
