"""Request-wide budget tracker (monotonic clock). Deliberately dependency-free — every other
pipeline module (llm, optimizer, api) imports this, so it must never import them back.
"""

import time


class Deadline:
    def __init__(self, total_s: float):
        self._start = time.monotonic()
        self._total_s = total_s

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def remaining(self) -> float:
        return max(0.0, self._total_s - self.elapsed())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def timeout_for(self, stage_budget: float, safety_margin: float = 0.3) -> float:
        return max(0.05, min(stage_budget, self.remaining() - safety_margin))
