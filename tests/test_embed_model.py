"""The ONNX wrapper's own decisions, with the backend faked.

What is pinned here: e5's prefixes are the wrapper's to apply (the
model was trained with them, and the library question of task 1.1
ended with "no library below this module does"), masked mean pooling
ignores padding, the vector lands as float16 little-endian, and the
loader opens only the two files it names — a missing model is a
configuration error, never a download.
"""

from pathlib import Path

import numpy as np
import pytest

from itgraph.embed.model import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    EmbeddingModel,
)

# Two dimensions are enough to reason about; the wrapper owns no
# dimension of its own — the model it loads does.
DIMS = 2

# One real token, one real token, one pad: the mask's third zero is
# what the pooling test needs the hidden state to survive.
IDS = [7, 8, 9]
MASK = [1, 1, 0]


class FakeEncoding:
    @property
    def ids(self) -> list[int]:
        return IDS

    @property
    def attention_mask(self) -> list[int]:
        return MASK


class FakeTokenizer:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def encode_batch(self, texts: list[str]) -> list[FakeEncoding]:
        self.seen.append(list(texts))
        return [FakeEncoding() for _ in texts]


class FakeSession:
    def __init__(self) -> None:
        self.feeds: list[dict[str, np.ndarray]] = []

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> list[np.ndarray]:
        self.feeds.append(input_feed)
        batch = input_feed["input_ids"].shape[0]
        # (0.6, 0.8) is already unit; the pooled result must be exactly
        # it, with the huge third position masked away.
        hidden = np.tile(
            np.array(
                [[[0.6, 0.8], [0.6, 0.8], [99.0, 99.0]]], dtype=np.float32
            ),
            (batch, 1, 1),
        )
        return [hidden]


def _model() -> tuple[EmbeddingModel, FakeTokenizer, FakeSession]:
    tokenizer = FakeTokenizer()
    session = FakeSession()
    return EmbeddingModel(session, tokenizer), tokenizer, session


def test_passages_carry_the_e5_prefix() -> None:
    model, tokenizer, _ = _model()

    model.embed_passages(["Kubernetes убивает поды", "postgres scaling"])

    assert tokenizer.seen == [
        [
            PASSAGE_PREFIX + "Kubernetes убивает поды",
            PASSAGE_PREFIX + "postgres scaling",
        ]
    ]


def test_the_query_carries_the_e5_prefix() -> None:
    model, tokenizer, _ = _model()

    model.embed_query("почему падают контейнеры")

    assert tokenizer.seen == [[QUERY_PREFIX + "почему падают контейнеры"]]


def test_the_feed_is_bert_shaped() -> None:
    model, _, session = _model()

    model.embed_passages(["one", "two"])

    (feed,) = session.feeds
    assert set(feed) == {"input_ids", "attention_mask", "token_type_ids"}
    assert feed["input_ids"].shape == (2, len(IDS))
    assert feed["attention_mask"].tolist() == [MASK, MASK]
    assert (feed["token_type_ids"] == 0).all()


def test_pooling_ignores_padding_and_emits_unit_fp16() -> None:
    model, _, _ = _model()

    (blob,) = model.embed_passages(["a text"])

    vector = np.frombuffer(blob, dtype="<f2").astype(np.float32)
    assert vector.shape == (DIMS,)
    assert vector == pytest.approx([0.6, 0.8], abs=1e-3)
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-3)


def test_no_passages_embed_nothing() -> None:
    model, tokenizer, session = _model()

    assert model.embed_passages([]) == []
    assert tokenizer.seen == []
    assert session.feeds == []


def test_a_missing_model_is_a_named_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as raised:
        EmbeddingModel.load(tmp_path)

    message = str(raised.value)
    assert str(tmp_path) in message
    assert "model_O4.onnx" in message
    assert "tokenizer.json" in message
