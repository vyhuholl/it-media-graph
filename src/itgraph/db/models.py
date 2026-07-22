"""SQLAlchemy models.

Tables land here together with the Alembic revision that creates them —
never one without the other.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "BackfillState",
    "BackfillStatus",
    "Channel",
    "ChannelKind",
    "ChannelStatus",
    "DiscoverySource",
    "FailureKind",
    "RawChannel",
    "RawMessage",
    "RejectReason",
]

# Explicit constraint names, so autogenerate emits stable identifiers
# instead of whatever Postgres happened to assign, and downgrades can
# actually find what they drop.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is Alembic's target."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ChannelStatus(enum.StrEnum):
    """Where a channel stands in review."""

    CANDIDATE = "candidate"
    SEED = "seed"
    MAYBE = "maybe"
    REJECTED = "rejected"


class ChannelKind(enum.StrEnum):
    """What a channel *is* — not what it writes about.

    A recruiter writing in their own voice is ``PERSONAL``; ``VACANCIES``
    is a feed of listings with no authorial voice. ``MEDIA`` is separate
    from ``COMPANY`` because an outlet behaves as a high-degree hub in
    the graph and a corporate blog does not.
    """

    PERSONAL = "personal"
    AGGREGATOR = "aggregator"
    COMPANY = "company"
    VACANCIES = "vacancies"
    MEDIA = "media"
    COMMUNITY = "community"
    EVENT = "event"


class RejectReason(enum.StrEnum):
    """Why a channel is out of scope.

    Rejections are kept: they are the labelled data the later candidate
    classifier trains on, and they cannot be reconstructed afterwards.
    """

    NOT_IT = "not_it"
    ADJACENT = "adjacent"
    CRYPTO = "crypto"
    INFOBIZ = "infobiz"
    ADS = "ads"
    CONTENT_FARM = "content_farm"
    OTHER_SCENE = "other_scene"


class DiscoverySource(enum.StrEnum):
    """How a channel entered the inventory.

    Values no import path can produce yet are declared now, so later
    changes add rows rather than alter the type.
    """

    OWN_SUBSCRIPTIONS = "own_subscriptions"
    FORWARD = "forward"
    RECOMMENDATION = "recommendation"
    MENTION = "mention"
    MANUAL = "manual"
    LINKED_CHAT = "linked_chat"


class BackfillStatus(enum.StrEnum):
    """How far a channel got in the last backfill run.

    ``RUNNING`` left behind by a killed process is not a problem to
    repair: resumption reads the cursor, not the status. It is what
    distinguishes an interrupted channel from one never started.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    SKIPPED = "skipped"
    FAILED = "failed"


class FailureKind(enum.StrEnum):
    """Whether a later run should try this channel again.

    ``PERMANENT`` — private, deleted, or the username no longer
    resolves. ``TRANSIENT`` — network, timeout, unexpected server error.
    """

    PERMANENT = "permanent"
    TRANSIENT = "transient"


def _pg_enum(members: type[enum.Enum], name: str) -> Enum:
    """A Postgres enum storing member *values*, not member names."""
    return Enum(
        members,
        name=name,
        values_callable=lambda enum_class: [
            member.value for member in enum_class
        ],
    )


class Channel(Base):
    """One Telegram channel or chat, and the review it has had.

    Identity is the Telegram id in the bare form Telethon exposes as
    ``entity.id``, without the ``-100`` prefix some Bot API contexts use:
    the prefixed form is the same channel under a different
    representation, and mixing the two silently doubles the row.
    """

    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)",
            name="rejected_has_reason",
        ),
    )

    tg_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    username: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)

    # A fact about the entity, not a label: a community's discussion
    # group is both a chat and, say, company-run. The comments phase
    # reads this column, which is why chats are imported now.
    is_chat: Mapped[bool] = mapped_column(server_default=false())

    status: Mapped[ChannelStatus] = mapped_column(
        _pg_enum(ChannelStatus, "channel_status"),
        server_default=ChannelStatus.CANDIDATE.value,
    )
    reject_reason: Mapped[RejectReason | None] = mapped_column(
        _pg_enum(RejectReason, "reject_reason")
    )
    reject_note: Mapped[str | None] = mapped_column(Text)

    kind: Mapped[ChannelKind | None] = mapped_column(
        _pg_enum(ChannelKind, "channel_kind")
    )
    kind_note: Mapped[str | None] = mapped_column(Text)

    discovered_via: Mapped[DiscoverySource] = mapped_column(
        _pg_enum(DiscoverySource, "discovery_source")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Whether a channel still needs a decision is `reviewed_at IS NULL`,
    # never a combination of empty label columns.
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    linked_to: Mapped[int | None] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="SET NULL"), nullable=True
    )

    # Denormalized from the newest message a backfill saw. A convenience
    # for "is this channel still alive", not a source of truth: the
    # authority is the payload in `raw_messages`.
    last_post_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    def __repr__(self) -> str:
        return (
            f"Channel(tg_id={self.tg_id}, username={self.username!r}, "
            f"status={self.status.value!r})"
        )


class RawMessage(Base):
    """One message exactly as Telegram sent it.

    Immutable by construction: the first fetch of a message id wins, and
    nothing downstream may write here. Everything derived — forwards,
    mentions, links, language — is computed from ``payload`` and must
    stay re-runnable from it, because re-fetching history is the
    expensive operation this table exists to avoid.
    """

    __tablename__ = "raw_messages"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    # Message ids are per-channel, so neither half identifies a row alone.
    msg_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )

    # jsonb, not json: key order and whitespace are worth nothing here,
    # and containment queries over several hundred thousand rows are.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"RawMessage(channel_id={self.channel_id}, msg_id={self.msg_id})"
        )


class RawChannel(Base):
    """The latest ``GetFullChannelRequest`` payload for one channel.

    The freshest payload wins, unlike ``RawMessage``: a description and a
    linked discussion chat change over time, and what a reader wants is
    the current state. Keeping every version would be a time series, and
    that is a separate table on a separate cadence.
    """

    __tablename__ = "raw_channels"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"RawChannel(channel_id={self.channel_id})"


class BackfillState(Base):
    """How far history collection got on one channel, and why it stopped.

    ``oldest_fetched_id`` is what a resumed run reads; it advances in the
    same transaction as the batch it describes, so it is never ahead of
    the stored rows. ``newest_fetched_id`` is the high-water mark that
    incremental collection will read — recorded now to avoid migrating a
    per-channel table later.
    """

    __tablename__ = "backfill_state"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )

    oldest_fetched_id: Mapped[int | None] = mapped_column(BigInteger)
    newest_fetched_id: Mapped[int | None] = mapped_column(BigInteger)

    # The depth this channel is complete to. Stored rather than only
    # applied, so re-running with an earlier cutoff resumes instead of
    # silently deciding there is nothing left to do.
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[BackfillStatus] = mapped_column(
        _pg_enum(BackfillStatus, "backfill_status"),
        server_default=BackfillStatus.PENDING.value,
    )
    failure_kind: Mapped[FailureKind | None] = mapped_column(
        _pg_enum(FailureKind, "failure_kind")
    )
    failure_detail: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"BackfillState(channel_id={self.channel_id}, "
            f"status={self.status.value!r}, "
            f"oldest_fetched_id={self.oldest_fetched_id})"
        )
