"""Deterministic stand-ins for the embedding model, and the vectors
tests seed the tables with.

No weights, no onnxruntime: the wrapper and the loop take their backend
by protocol, so a fake that answers with vectors a test names exercises
every decision the real model would be trusted with.
"""

from collections.abc import Sequence

import numpy as np


def vector(*values: float) -> bytes:
    """A unit float16 little-endian vector laid on the given axes.

    Axes are the positions given; everything else is zero. Two vectors
    built on disjoint axes are orthogonal, which is what the ranking
    tests mean by "unrelated". A fixed width, because one model's
    vectors all share one dimension — that is an invariant of the
    table, not something the ranking should have to survive.
    """
    axes = np.zeros(WIDTH, dtype=np.float32)
    axes[: len(values)] = values
    axes /= np.linalg.norm(axes)
    return axes.astype(np.float16).tobytes()


WIDTH = 8


class FakeEmbeddingModel:
    """Passages and queries answered with vectors a test names.

    A passage text found in ``passages`` gets its vector; every other
    text gets ``default``, so a test names only the vectors it means to
    reason about and the rest still embed as something valid. The query
    is always ``query`` — one query per search is the interface.
    """

    def __init__(
        self,
        *,
        passages: dict[str, bytes] | None = None,
        query: bytes | None = None,
        default: bytes | None = None,
    ) -> None:
        self.passages = passages or {}
        self.query = query if query is not None else vector(1.0)
        self.default = default if default is not None else vector(0.0, 1.0)
        self.passages_seen: list[str] = []
        self.queries_seen: list[str] = []

    def embed_passages(self, texts: Sequence[str]) -> list[bytes]:
        self.passages_seen.extend(texts)
        return [self.passages.get(text, self.default) for text in texts]

    def embed_query(self, text: str) -> bytes:
        self.queries_seen.append(text)
        return self.query


class FlakyModel:
    """A model that gives up after so many calls, mid-loop.

    What the interrupted-backfill test needs: the failure must arrive
    after a commit, or there is nothing to resume from.
    """

    def __init__(self, inner: FakeEmbeddingModel, calls_before_failure: int):
        self.inner = inner
        self.calls_left = calls_before_failure

    def embed_passages(self, texts: Sequence[str]) -> list[bytes]:
        if self.calls_left <= 0:
            raise RuntimeError("interrupted")
        self.calls_left -= 1
        return self.inner.embed_passages(texts)

    def embed_query(self, text: str) -> bytes:
        return self.inner.embed_query(text)
