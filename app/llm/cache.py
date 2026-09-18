"""In-process LRU cache for note interpretations, keyed on everything that could change the
answer. Makes repeated identical requests instant and bit-identical (§4.2) — the belt to
temperature=0's braces.
"""

import hashlib
import json
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


def cache_key(
    prompt_version: str,
    model_chain: list[str],
    capacity_kwh: float,
    initial_kwh: float,
    minimum_kwh: float,
    max_charge: float,
    max_discharge: float,
    all_notes: list[str],
    target_index: int,
) -> str:
    payload = {
        "prompt_version": prompt_version,
        "model_chain": model_chain,
        "battery": [capacity_kwh, initial_kwh, minimum_kwh, max_charge, max_discharge],
        "notes": all_notes,
        "target_index": target_index,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class InterpretationCache(Generic[T]):
    def __init__(self, max_size: int = 2048):
        self._max_size = max_size
        self._store: OrderedDict[str, T] = OrderedDict()

    def get(self, key: str) -> T | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: str, value: T) -> None:
        if self._max_size <= 0:
            return
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)
