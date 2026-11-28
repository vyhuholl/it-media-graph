"""`intfloat/multilingual-e5-small`, loaded straight from its ONNX export.

No embedding library sits between this module and the weights: fastembed
was dropped because its registry does not carry e5-small, and what it
would have added here — prefix handling it does not do, downloads the
job's egress forbids — is nothing this wrapper wants. The files it loads
are the canonical repo's own `model_O4.onnx` (int8, 235 MB; measured
within 0.0002 cosine of the fp32 export) and `tokenizer.json`, fetched
into ``settings.embed_model_dir`` at image build or by hand on a
developer machine — never at runtime, from this code.

e5's ``query:`` / ``passage:`` prefixes are applied here, because the
model was trained with them and nothing below this module adds them.

One text yields exactly one vector: a text longer than the model's
512-token window is head-truncated by the tokenizer, at a length this
module names, not a library default.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

# What every embedding row names as its maker. The name carries the
# weights, not just the architecture: the O4 int8 export produces
# different vectors than the fp32 one, so switching weights without
# switching this key would quietly mix two exports under one name —
# exactly what the key exists to prevent.
MODEL_KEY = "multilingual-e5-small-o4"

MAX_TOKENS = 512
# The design's neighbourly pod: four ONNX threads inside a 4-CPU limit.
THREADS = 4

# The two files the image bakes; see the Dockerfile's fetch step.
WEIGHTS_FILE = "model_O4.onnx"
TOKENIZER_FILE = "tokenizer.json"

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class EncodingLike(Protocol):
    """What the wrapper reads off a tokenizer's encoding."""

    @property
    def ids(self) -> list[int]: ...

    @property
    def attention_mask(self) -> list[int]: ...


class TokenizerLike(Protocol):
    """The tokenizer's one method the wrapper calls.

    Spelled as ``tokenizers`` spells it, so the real class satisfies the
    protocol without a cast.
    """

    def encode_batch(
        self,
        input: Sequence[Any],
        is_pretokenized: bool = ...,
        add_special_tokens: bool = ...,
    ) -> list[EncodingLike]: ...


class SessionLike(Protocol):
    """The inference session's one method the wrapper calls."""

    def run(
        self,
        output_names: Sequence[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> list[np.ndarray]: ...


class EmbeddingModel:
    """Passages in, float16 bytes out; one query at a time.

    The session and tokenizer arrive by protocol rather than by class,
    so the tests stand in for both without the weights and without
    pretending a 235 MB file exists.
    """

    def __init__(self, session: SessionLike, tokenizer: TokenizerLike) -> None:
        self.session = session
        self.tokenizer = tokenizer

    @classmethod
    def load(cls, model_dir: Path) -> EmbeddingModel:
        """Load the baked weights from ``model_dir``.

        Nothing here downloads. A missing directory is a configuration
        error, and the operator should hear exactly that, not watch a
        job try to reach the internet its egress denies.
        """
        weights = model_dir / WEIGHTS_FILE
        tokenizer_file = model_dir / TOKENIZER_FILE
        if not weights.is_file() or not tokenizer_file.is_file():
            raise FileNotFoundError(
                f"the embedding model is not at {model_dir}: expected "
                f"{WEIGHTS_FILE} and {TOKENIZER_FILE} there. The image "
                "bakes them at build; a developer machine fetches them "
                "once with huggingface-hub into this directory."
            )
        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        session = ort.InferenceSession(
            str(weights),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        # Explicit, not the library default: the head of a long text is
        # the vector, and the tail is discarded.
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        # Batches pad to their longest member, so every encoding the
        # wrapper builds carries a full attention mask.
        tokenizer.enable_padding()
        # One cast at the third-party boundary: tokenizers' stub
        # declares `ids` and `attention_mask` as methods while at
        # runtime they are properties, so mypy cannot be shown what the
        # wrapper's every call proves. Everything inside is checked
        # against the protocol; only this handover is trusted.
        return cls(session, cast(TokenizerLike, tokenizer))

    def embed_passages(self, texts: Sequence[str]) -> list[bytes]:
        """Embed stored texts: each becomes one float16 little-endian blob."""
        if not texts:
            return []
        return self._embed([PASSAGE_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> bytes:
        """Embed the one text a search is ranked against."""
        return self._embed([QUERY_PREFIX + text])[0]

    def _embed(self, prefixed: list[str]) -> list[bytes]:
        encodings = self.tokenizer.encode_batch(prefixed)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        }
        hidden = self.session.run(None, feed)[0]
        # The sentence-e5 recipe: mean of the tokens the mask kept, then
        # unit length, so a cosine is a dot product.
        kept = mask.sum(axis=1, keepdims=True)
        pooled = (hidden * mask[:, :, None]).sum(axis=1) / kept
        pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        # float16 little-endian: every platform the image and the
        # developer machines run is little-endian, and numpy's float16
        # has no byte-order form of its own to ask for.
        return [row.astype(np.float16).tobytes() for row in pooled]
