"""What is embedded, from both sides of the boundary.

The Python functions say what a payload's text is; the selection query
in `db.embed` states the same YouTube join as SQL, because due rows are
found in the database. The drift test runs both over the same rows and
refuses a disagreement — a changed join on one side only would quietly
re-embed or skip texts, and nothing else would ever notice.
"""

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import text

from itgraph.db.embed import _YT_HASH, _YT_TEXT
from itgraph.db.models import YtDiscoveryPath, YtRawVideo
from itgraph.db.session import Database
from itgraph.embed.text import content_hash, tg_text, yt_text

NOW_TEXT = "2026-11-21T12:00:00+00:00"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _video_payload(
    *, title: str | None, description: str | None
) -> dict[str, object]:
    snippet: dict[str, object] = {"title": title, "publishedAt": NOW_TEXT}
    if description is not None:
        snippet["description"] = description
    return {"id": "fakevideo01", "snippet": snippet}


async def _seed_videos(
    database: Database, payloads: dict[str, object]
) -> None:
    async with database.session() as session:
        for video_id, payload in payloads.items():
            session.add(
                YtRawVideo(
                    video_id=video_id,
                    payload=payload,
                    fetched_at=NOW,
                    discovered_at=NOW,
                    discovered_via=YtDiscoveryPath.FIRST,
                    due_at=None,
                )
            )


# --- Telegram -----------------------------------------------------------


def test_a_texted_message_has_its_text() -> None:
    payload = {
        "id": 10,
        "message": "Простой пост со смыслом",
        "date": NOW_TEXT,
    }

    assert tg_text(payload) == "Простой пост со смыслом"


def test_a_media_only_message_has_no_text() -> None:
    assert tg_text({"id": 11, "message": "", "date": NOW_TEXT}) is None
    assert tg_text({"id": 12, "date": NOW_TEXT}) is None


# --- YouTube ------------------------------------------------------------


def test_title_and_description_join_over_a_blank_line() -> None:
    payload = _video_payload(
        title="Scaling Postgres", description="Partitions and pooling."
    )

    assert yt_text(payload) == ("Scaling Postgres\n\nPartitions and pooling.")


def test_a_video_without_a_description_embeds_the_title_alone() -> None:
    empty = _video_payload(title="Just a title", description="")
    missing = _video_payload(title="Just a title", description=None)

    assert yt_text(empty) == "Just a title"
    assert yt_text(missing) == "Just a title"


def test_a_payload_without_a_title_is_not_a_text() -> None:
    assert yt_text({"snippet": {"description": "no title"}}) is None


def test_the_hash_is_sha256_hex_of_the_utf8_bytes() -> None:
    assert (
        content_hash("Scaling Postgres\n\nPartitions.")
        == sha256(b"Scaling Postgres\n\nPartitions.").hexdigest()
    )


# --- the SQL join cannot drift from the Python one ----------------------


async def test_the_selection_sql_agrees_with_the_extraction(
    database: Database,
) -> None:
    payloads = {
        "fakevideo01": _video_payload(
            title="With Description", description="The body text."
        ),
        "fakevideo02": _video_payload(
            title="Empty Description", description=""
        ),
        "fakevideo03": _video_payload(
            title="Missing Description", description=None
        ),
    }
    await _seed_videos(database, payloads)

    async with database.session() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT video_id, {_YT_TEXT} AS body, {_YT_HASH} AS hash "
                    "FROM yt_raw_videos v ORDER BY video_id"
                )
            )
        ).all()

    for row in rows:
        expected = yt_text(payloads[row.video_id])
        assert row.body == expected
        assert row.hash == content_hash(expected)
