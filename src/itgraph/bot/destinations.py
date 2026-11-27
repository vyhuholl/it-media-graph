"""Where an alert goes, and the account each place keeps. Pure.

A **destination** is a topic of the one alert chat: Telegram's alerts
have the chat's Telegram topic, YouTube's have one per language that has
a topic. Each destination keeps its own account — its daily cap, its
quiet window, its digest — so that a burst on one platform, or in one
language, cannot spend another's.

The one chat and its three topics come from settings together or not at
all; the account values are per platform, both YouTube topics taking
YouTube's. Their readers are one group in one zone, and a setting per
topic would be configuration nobody sets.

Destinations are built from settings and from nothing else, which is
what makes "the bot sends only where it is configured to" a property of
this module rather than a convention of its callers.
"""

from dataclasses import dataclass
from datetime import datetime

from itgraph.config import Settings
from itgraph.db.models import Platform, YtAlertRoute
from itgraph.schedule import in_quiet_window, quiet_until

__all__ = [
    "Destination",
    "Platform",
    "telegram_destination",
    "youtube_destinations",
]


@dataclass(frozen=True, slots=True)
class Destination:
    """One place alerts are sent to, and the rules it is sent under.

    ``key`` names it in logs and in the bot's own memory of when each
    digest went out; it is a constant, never an id.
    """

    key: str
    platform: Platform
    chat_id: int
    topic_id: int | None
    daily_cap: int
    quiet_from: int
    quiet_to: int
    digest_hour: int
    zone: str
    # The YouTube route this topic takes; none for Telegram's destination.
    route: YtAlertRoute | None = None

    def is_quiet(self, moment: datetime) -> bool:
        """Whether nothing may be sent here directly at this moment."""
        return in_quiet_window(
            moment, start=self.quiet_from, end=self.quiet_to, zone=self.zone
        )

    def quiet_until(self, moment: datetime) -> datetime | None:
        """When this destination's quiet window lets go, if it holds now."""
        return quiet_until(
            moment, start=self.quiet_from, end=self.quiet_to, zone=self.zone
        )


def telegram_destination(settings: Settings) -> Destination | None:
    """The chat's Telegram topic, or ``None`` where no chat is configured."""
    if settings.alert_chat_id is None:
        return None
    return Destination(
        key="telegram",
        platform=Platform.TELEGRAM,
        chat_id=settings.alert_chat_id,
        topic_id=settings.telegram_alert_topic,
        daily_cap=settings.alert_daily_cap,
        quiet_from=settings.alert_quiet_from_hour,
        quiet_to=settings.alert_quiet_to_hour,
        digest_hour=settings.alert_digest_hour,
        zone=settings.watch_timezone,
    )


def youtube_destinations(
    settings: Settings,
) -> dict[YtAlertRoute, Destination]:
    """The chat's YouTube topics by route, or none where it is not
    configured.

    Settings refuse the chat without all its topics when they load, so
    this is all or nothing.
    """
    chat = settings.alert_chat_id
    topics = {
        YtAlertRoute.EN: settings.youtube_alert_topic_en,
        YtAlertRoute.RU: settings.youtube_alert_topic_ru,
    }
    if chat is None:
        return {}
    return {
        route: Destination(
            key=f"youtube_{route.value}",
            platform=Platform.YOUTUBE,
            chat_id=chat,
            topic_id=topic,
            daily_cap=settings.youtube_alert_daily_cap,
            quiet_from=settings.youtube_alert_quiet_from_hour,
            quiet_to=settings.youtube_alert_quiet_to_hour,
            digest_hour=settings.youtube_alert_digest_hour,
            zone=settings.youtube_alert_timezone,
            route=route,
        )
        for route, topic in topics.items()
        if topic is not None
    }
