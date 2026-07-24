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
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
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
    "Edge",
    "EdgeKind",
    "FailureKind",
    "PendingMention",
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


class EdgeKind(enum.StrEnum):
    """What kind of reference an edge records.

    ``FORWARD`` is a repost carrying the origin channel's id; ``MENTION``
    is a reference by ``@username`` or ``t.me`` link. Both point from the
    referencing channel to the referenced one. Values a later derivation
    might add — a reply, a comment — are not declared here: unlike
    discovery sources, a new edge kind is a new derivation, and it can
    afford the enum migration it comes with.
    """

    FORWARD = "forward"
    MENTION = "mention"


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

    # Resolution state. A channel discovered by forward carries only its
    # id; `itgraph resolve` fills in username and title from Telegram and
    # stamps `resolved_at`. `resolved_at IS NULL` is the whole "still
    # needs resolving" predicate — the same shape `reviewed_at` uses for
    # review. Attempts are counted so a cache miss (see the resolve pass)
    # is retried on demand rather than treated as a permanent verdict.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolve_attempts: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    resolve_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolve_last_error: Mapped[str | None] = mapped_column(Text)

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


class Edge(Base):
    """One observed reference from one channel to another.

    An observation, not an aggregate: a channel that reposts another ten
    times is ten rows, each carrying the message it came from and when it
    was published. Weights, decay and clustering are computed from these
    rows and never stored here — that is the analysis this table feeds,
    not part of it.

    The natural key is ``(src, msg_id, kind, dst)``: one message can
    forward from a channel and also mention it, and both are real, but the
    same message mentioning the same channel twice is one edge. Rows are
    inserted with ``ON CONFLICT DO NOTHING``, so re-deriving over
    unchanged raw data writes nothing.

    Only channel ids appear here. User ids from ``fwd_from`` and signed
    posts are dropped at the derivation boundary and stay in the raw
    layer, which is never exported — that is what keeps this table safe to
    visualize and share.
    """

    __tablename__ = "edges"
    __table_args__ = (
        UniqueConstraint(
            "src_channel_id",
            "msg_id",
            "kind",
            "dst_channel_id",
            name="observed_reference",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    src_channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"), index=True
    )
    dst_channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EdgeKind] = mapped_column(_pg_enum(EdgeKind, "edge_kind"))

    # The referencing message, in the raw layer's per-channel numbering,
    # and its publication date — every time-decayed analysis reads this.
    msg_id: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    derived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"Edge(src={self.src_channel_id}, dst={self.dst_channel_id}, "
            f"kind={self.kind.value!r}, msg_id={self.msg_id})"
        )


class PendingMention(Base):
    """A username mentioned somewhere, not yet a channel in the inventory.

    A forward names an id, which is a channel's primary key, so its row is
    created in the same pass. A mention names only a ``@username``, which
    resolves to no id without a Telegram lookup — so it waits here,
    keyed by the normalized username, until ``itgraph resolve`` turns it
    into a channel. The *next* derivation run then writes the edge.

    This keeps unresolved state out of ``edges``: the table analysis reads
    never carries a half-known endpoint that every consumer would have to
    filter out.
    """

    __tablename__ = "pending_mentions"

    # Lowercased, without the leading `@` — the same normalization the
    # channel lookup uses, so a username here and a username there agree.
    username: Mapped[str] = mapped_column(Text, primary_key=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"PendingMention(username={self.username!r})"
