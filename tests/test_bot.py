"""Rendering and delivery.

No aiogram, no network, no token. Everything worth asserting — what a
message says, when an alert is held, what happens when a send fails —
lives behind the `Sender` seam, which is the reason that seam exists.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from test_alerts_db import PUBLISHER, seed

from itgraph.alerts.cascade import Cascade
from itgraph.bot.app import BotStats, Sender, deliver_once
from itgraph.bot.render import (
    Carrier,
    RenderedAlert,
    digest,
    render_cascade,
    render_spike,
)
from itgraph.config import settings
from itgraph.db.alerts import claim_undelivered, raise_cascades
from itgraph.db.models import AlertKind
from itgraph.db.session import Database

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
POST = 500


class Recorder(Sender):
    """A sender that remembers instead of sending."""

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[str, int | None]] = []
        self.fail = fail

    async def send(self, text: str, *, alert_id: int | None = None) -> None:
        if self.fail:
            raise RuntimeError("telegram is unreachable")
        self.messages.append((text, alert_id))


@pytest.fixture(autouse=True)
def no_quiet_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off by default, so a test's outcome does not depend on the clock."""
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 0)
    # Far from any NOW a test uses, so the digest does not fire by
    # accident in tests that are about direct delivery.
    monkeypatch.setattr(settings, "alert_digest_hour", 23)


# --- rendering ------------------------------------------------------


def rendered(**overrides: object) -> RenderedAlert:
    kwargs: dict[str, object] = {
        "alert_id": 1,
        "channel_title": "Example Channel",
        "channel_username": "example",
        "msg_id": 500,
        "published_at": NOW - timedelta(hours=3),
        "now": NOW,
        "families": 3,
        "carriers": [
            Carrier(title="One", username="one"),
            Carrier(title="Two", username="two"),
        ],
        "text": "смотрите, что нашли в опенсорсе",
    }
    kwargs.update(overrides)
    return render_cascade(**kwargs)  # type: ignore[arg-type]


def test_the_message_links_to_the_post() -> None:
    assert "https://t.me/example/500" in rendered().text


def test_a_channel_without_a_username_gets_no_link() -> None:
    """No link beats a broken one for a channel nobody can reach."""
    assert "t.me" not in rendered(channel_username=None).text


def test_the_message_states_the_posts_age() -> None:
    """The number a reader would otherwise assume wrongly.

    A cascade takes hours to form; someone expecting minutes concludes
    the system is broken.
    """
    assert "3 ч 00 мин назад" in rendered().text


def test_the_message_names_the_carriers_once() -> None:
    text = rendered().text
    assert "@one" in text
    assert "@two" in text
    # One message about the post, not one per carrier.
    assert text.count("https://t.me/example/500") == 1


def test_a_long_post_is_excerpted() -> None:
    long = "слово " * 200
    assert len(rendered(text=long).text) < 600


def test_a_post_with_no_text_still_renders() -> None:
    assert "3" in rendered(text=None).text


def spike(**overrides: object) -> RenderedAlert:
    kwargs: dict[str, object] = {
        "alert_id": 1,
        "kind": AlertKind.VIEWS_SPIKE,
        "channel_title": "Example Channel",
        "channel_username": "example",
        "msg_id": 500,
        "published_at": NOW - timedelta(minutes=40),
        "now": NOW,
        "z": 3.4,
        "text": "смотрите, что нашли в опенсорсе",
    }
    kwargs.update(overrides)
    return render_spike(**kwargs)  # type: ignore[arg-type]


def test_a_spike_says_which_metric_it_is_about() -> None:
    """The four kinds mean different things and a reader decides on that.

    Reach, approval, an endorsement strong enough to republish, and an
    argument — one wording covering all four would waste the distinction
    the kinds exist to carry.
    """
    assert "Просмотры" in spike().text
    assert "Реакции" in spike(kind=AlertKind.REACTION_SPIKE).text
    assert "Пересылки" in spike(kind=AlertKind.FORWARD_SPIKE).text


def test_a_spike_is_not_worded_as_a_cascade() -> None:
    """The failure this dispatch exists to prevent.

    Before the scoring pass there was one renderer, so a view spike would
    have been delivered as "3 независимых источника репостят" — a
    sentence that is not merely awkward but false.
    """
    assert "репостят" not in spike().text


def test_a_spike_states_the_posts_age() -> None:
    """It matters more here than on a cascade.

    A spike can fire fifteen minutes after publication, and a reader who
    has learned from cascades to expect hours misreads how early it is.
    """
    assert "40 мин назад" in spike().text


def test_a_bigger_spike_reads_as_bigger() -> None:
    """Words, plus the z for whoever is calibrating rather than reading."""
    assert spike(z=3.2).text != spike(z=8.0).text
    assert "z 8.0" in spike(z=8.0).text


def test_a_spike_links_to_the_post() -> None:
    assert "https://t.me/example/500" in spike().text
    assert "t.me" not in spike(channel_username=None).text


def test_the_digest_says_how_many_it_covers() -> None:
    """What separates a digest from a silent drop."""
    body = digest([rendered()], held=7)
    assert "7" in body


def test_an_empty_digest_is_still_labelled() -> None:
    assert "0" in digest([], held=0)


# --- delivery -------------------------------------------------------


async def prepare(database: Database, bands: tuple[int, ...] = (2,)) -> None:
    await seed(database, reposters=len(bands) + 1)
    async with database.session() as session:
        await raise_cascades(
            session,
            [Cascade(post_key=(PUBLISHER, POST), band=bands[0], value=2)],
        )


async def test_an_alert_is_sent_and_marked(database: Database) -> None:
    await prepare(database)
    sender = Recorder()

    await deliver_once(database, sender, BotStats(), now=NOW)

    assert len(sender.messages) == 1
    async with database.session() as session:
        assert await claim_undelivered(session, limit=10) == []


async def test_a_failed_send_leaves_the_alert_outstanding(
    database: Database,
) -> None:
    """A send failing must not lose the alert, and must not stop the loop."""
    await prepare(database)
    stats = BotStats()

    await deliver_once(database, Recorder(fail=True), stats, now=NOW)

    assert stats.failed == 1
    async with database.session() as session:
        still = await claim_undelivered(session, limit=10)
    assert len(still) == 1
    assert still[0].attempts == 1


async def test_quiet_hours_hold_rather_than_drop(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(database)
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 7)
    sender = Recorder()

    await deliver_once(database, sender, BotStats(), now=NOW.replace(hour=3))

    assert sender.messages == []
    # Held, not dropped — still outstanding for the digest.
    async with database.session() as session:
        assert len(await claim_undelivered(session, limit=10)) == 1


async def test_the_cap_holds_rather_than_drops(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(database)
    monkeypatch.setattr(settings, "alert_daily_cap", 1)
    # One already delivered today, so the cap is reached.
    async with database.session() as session:
        await session.execute(
            text("UPDATE alerts SET delivered_at = :at, delivery = 'direct'"),
            {"at": NOW - timedelta(hours=1)},
        )
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=3, value=3)]
        )
    sender = Recorder()

    await deliver_once(database, sender, BotStats(), now=NOW)

    assert sender.messages == []
    async with database.session() as session:
        assert len(await claim_undelivered(session, limit=10)) == 1


async def test_the_digest_delivers_what_quiet_hours_held(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason quiet hours are a delay rather than a drop.

    The realistic configuration: quiet 02:00–07:00, digest at 09:00 —
    which the settings validator requires to be outside the window, so
    the summary can actually be sent.
    """
    await prepare(database)
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 7)
    monkeypatch.setattr(settings, "alert_digest_hour", 9)
    sender = Recorder()
    stats = BotStats()

    night = NOW.replace(hour=3)
    # `raised_at` defaults to the database clock, so a test about an
    # alert raised overnight has to say when overnight was.
    async with database.session() as session:
        await session.execute(
            text("UPDATE alerts SET raised_at = :at"), {"at": night}
        )

    await deliver_once(database, sender, stats, now=night)
    assert sender.messages == []
    async with database.session() as session:
        assert len(await claim_undelivered(session, limit=10)) == 1

    morning = NOW.replace(hour=9, minute=30)
    await deliver_once(database, sender, stats, now=morning)

    assert stats.digested == 1
    assert "оповещение" in sender.messages[0][0]
    async with database.session() as session:
        assert await claim_undelivered(session, limit=10) == []


async def test_a_digest_covering_nothing_is_not_sent(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daily "nothing happened" trains the reader to ignore the channel."""
    monkeypatch.setattr(settings, "alert_digest_hour", 0)
    sender = Recorder()

    await deliver_once(database, sender, BotStats(), now=NOW)

    assert sender.messages == []


async def test_an_alert_whose_post_vanished_is_skipped(
    database: Database,
) -> None:
    """Should not happen; not worth taking a delivery loop down for."""
    await prepare(database)
    async with database.session() as session:
        # Cascades to the alert, so this leaves an empty queue rather
        # than a dangling one — which is itself the guarantee.
        await session.execute(text("DELETE FROM raw_messages"))

    sender = Recorder()
    await deliver_once(database, sender, BotStats(), now=NOW)

    assert sender.messages == []


async def test_the_alert_id_travels_with_the_message(
    database: Database,
) -> None:
    """The feedback buttons need it; without it a verdict has no subject."""
    await prepare(database)
    sender = Recorder()

    await deliver_once(database, sender, BotStats(), now=NOW)

    assert sender.messages[0][1] is not None
