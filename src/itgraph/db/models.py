"""SQLAlchemy models.

Tables land here together with the Alembic revision that creates them —
never one without the other.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
    "AboutDirection",
    "AffiliationCandidate",
    "AffiliationDecision",
    "AffiliationRun",
    "Alert",
    "AlertDelivery",
    "AlertFeedback",
    "AlertKind",
    "AlertVerdict",
    "BackfillState",
    "BackfillStatus",
    "Base",
    "BaselineRun",
    "CandidateOrigin",
    "Channel",
    "ChannelBaseline",
    "ChannelKind",
    "ChannelStatus",
    "CollectionCommand",
    "CurvePoint",
    "DiscoverySource",
    "Edge",
    "EdgeKind",
    "FailureKind",
    "FloodEvent",
    "MessageMetric",
    "Metric",
    "MetricBaseline",
    "PendingMention",
    "PendingMentionSource",
    "PollState",
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


class CollectionCommand(enum.StrEnum):
    """Which networked command a recorded rate limit came from.

    Each command owns the quota-bearing method it spends:
    ``contacts.resolveUsername`` belongs to resolution,
    ``channels.getFullChannel`` to the metadata pass, and a history walk
    is meant to spend neither. That mapping is exactly why the column is
    still worth its width — it is what makes the mapping *checkable*. A
    `ResolveUsernameRequest` filed under ``BACKFILL`` is not a curiosity,
    it is the regression this table exists to catch, and the method name
    alone could never report it.

    ``ADD`` is the one legitimate overlap: it spends
    ``contacts.resolveUsername`` too, because resolving a username is the
    whole of what it does. So that method now reads as expected under two
    commands and as a regression under the other two, and the question
    the column answers changes from "which command spends this method" to
    "which of the two spent today's quota" — which is the question worth
    asking once both are running against the same daily ceiling.

    ``WATCH`` owns no quota-bearing method at all, which is the strongest
    version of the same claim. The poll loop asks for history and nothing
    else, and unlike a backfill it does so indefinitely — so a
    ``ResolveUsernameRequest`` or a ``GetFullChannelRequest`` filed under
    it is not one run's mistake but a leak that spends the day's quota
    every day until someone notices. This is the command whose attribution
    is worth the most.
    """

    BACKFILL = "backfill"
    RESOLVE = "resolve"
    METADATA = "metadata"
    ADD = "add"
    WATCH = "watch"


class AlertKind(enum.StrEnum):
    """What an alert is about.

    Only what something can produce. The four spike kinds arrived with
    the scoring pass rather than being declared ahead of it, for the
    reason the cascade change gave: a value nothing can raise is a
    promise made in a type, and a reader cannot tell it from a feature
    that quietly stopped working.

    The four are separate rather than one ``SPIKE`` with a metric column
    because they mean different things — reach, approval, an endorsement
    strong enough to republish, and an argument — and the kind is what a
    later query groups by when asking which of those the operator
    actually found useful.
    """

    REPOST_CASCADE = "repost_cascade"
    VIEWS_SPIKE = "views_spike"
    REACTION_SPIKE = "reaction_spike"
    FORWARD_SPIKE = "forward_spike"
    COMMENT_SPIKE = "comment_spike"


class Metric(enum.StrEnum):
    """The four counters a post is scored on.

    Deliberately not collapsed into one number. Views are reach,
    reactions approval, forwards an endorsement strong enough to
    republish, and comments as often an argument as an interest; each is
    measured against its own baseline because a combined score would
    average away the only distinction worth having.

    The values match the columns of ``message_metrics`` and the fields of
    ``derive.metrics.Counters``, so the three never need translating.
    """

    VIEWS = "views"
    REACTIONS = "reactions"
    FORWARDS = "forwards"
    COMMENTS = "comments"

    def alert_kind(self) -> AlertKind:
        """The alert this metric raises when it is the highest score."""
        return {
            Metric.VIEWS: AlertKind.VIEWS_SPIKE,
            Metric.REACTIONS: AlertKind.REACTION_SPIKE,
            Metric.FORWARDS: AlertKind.FORWARD_SPIKE,
            Metric.COMMENTS: AlertKind.COMMENT_SPIKE,
        }[self]


class AlertDelivery(enum.StrEnum):
    """How an alert reached the operator.

    Distinguished because the two are different products. ``DIRECT`` is a
    message about one post, sent when it happened; ``DIGEST`` is a line
    in a summary, and a reader treats it as a record rather than as news.
    Which one an alert got is also how the cap is accounted for, so it is
    a fact about delivery rather than a display preference.
    """

    DIRECT = "direct"
    DIGEST = "digest"


class AlertVerdict(enum.StrEnum):
    """What the operator thought of an alert.

    The only labelled data any later threshold work will have, which is
    why the buttons exist from the first message rather than from the
    first complaint about volume. An alert with no row here is
    unanswered, which is deliberately not the same as ``BORING``.
    """

    USEFUL = "useful"
    BORING = "boring"


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


class AffiliationDecision(enum.StrEnum):
    """What the operator decided about a proposed pair.

    ``PENDING`` is the state detection leaves a pair in, and the only one
    it may write. The other two are reachable solely through the
    confirmation command — the whole point of splitting the two.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class CandidateOrigin(enum.StrEnum):
    """Whether a signal proposed this pair or the operator asserted it.

    A pair no signal fired on is still recordable: the operator may
    simply know. Keeping the two apart is what stops such a row from
    reading as evidence the detection found — and what makes "how much
    did the signals actually catch" answerable later.
    """

    SIGNAL = "signal"
    OPERATOR = "operator"


class AboutDirection(enum.StrEnum):
    """Which way a description reference was found, between two channels.

    Named against the pair's stored order — ``A_TO_B`` means the channel
    in ``channel_a`` names the one in ``channel_b`` — because the pair
    itself is unordered and sorted by id, so "source" and "target" would
    mean nothing without the anchor.
    """

    A_TO_B = "a_to_b"
    B_TO_A = "b_to_a"
    MUTUAL = "mutual"


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

    # No family column. Which channels share an author is the transitive
    # closure of the confirmed pairs in `affiliation_candidates`, read
    # through the `channel_families` view — see `db/affiliation.py`. It
    # was a column once, pointing at a "canonical" channel every member
    # named, and that shape could not hold what the data turned out to
    # be: the pairs among one author's channels form an arbitrary graph,
    # not a star, so which family came out depended on the order the
    # pairs were confirmed in and some groups could not be assembled at
    # all. Deriving it also removes the drift a stored summary of the
    # pairs invited, and with it the one invariant this project had to
    # enforce in application code because a CHECK could not see it.

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


class MessageMetric(Base):
    """What one message's counters were at one moment.

    The raw layer for numbers that move. ``RawMessage`` cannot hold them:
    it is immutable by construction and the first fetch of a message id
    wins, which is right for a message body and useless for a counter.
    So engagement lives here, as observations — append-only, one row per
    message per reading, and never rewritten. A snapshot is a fact about
    a moment; correcting it later would be inventing a different moment.

    The samples are **irregular by design**. Suspend, quiet hours and
    rate limits all cost readings, and a missed one is dropped rather
    than taken late — a sample due at post-age 30 minutes and read at
    post-age 8 hours is not a late reading of the early curve, it is a
    different measurement wearing its name. So a consumer must read the
    age from ``observed_at`` minus the post's publication date, and never
    from which sample in the schedule this was supposed to be. That is
    the one assumption a scoring pass could make silently and be wrong
    about everywhere.

    **NULL is not zero.** A channel with reactions switched off publishes
    no reactions object at all, which is a different fact from a post
    nobody reacted to, and dividing by a baseline built from the two
    conflated is how a vacancy feed ends up looking like the most-loved
    channel in the inventory. ``notebooks/anomalous_posts.py`` has to
    rebuild that distinction per channel because the single snapshot it
    reads lost it; here it survives from the source.

    **Reactions stay per emoji and no total is stored.** A sum is a
    derived measure, and this is an observation table — the same trade
    ``Edge`` refuses when it carries two dates and declines to store the
    interval between them. The breakdown is also worth more than the sum:
    a post accumulating 🤡 and one accumulating ❤️ are opposite events
    that a total reports identically.

    The foreign key onto ``raw_messages`` is load-bearing rather than
    decorative: it makes a snapshot of a message the raw layer does not
    hold impossible, which pins the write order — payload first, snapshot
    second, one transaction. It references that table's primary key, so
    it costs no index of its own.
    """

    __tablename__ = "message_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "msg_id"],
            ["raw_messages.channel_id", "raw_messages.msg_id"],
            ondelete="CASCADE",
        ),
        # The alert pass reads "every snapshot since I last ran", and the
        # primary key's leftmost column is the channel, so no prefix of it
        # can serve that query.
        Index("ix_message_metrics_observed_at", "observed_at"),
    )

    channel_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    msg_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )

    views: Mapped[int | None] = mapped_column(Integer)
    forwards: Mapped[int | None] = mapped_column(Integer)
    # One entry per reaction, keyed by the emoji or custom-emoji id the
    # payload names. Absent means the channel publishes no reactions;
    # empty means it does and nobody has.
    reactions: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    comments: Mapped[int | None] = mapped_column(Integer)

    def __repr__(self) -> str:
        return (
            f"MessageMetric(channel_id={self.channel_id}, "
            f"msg_id={self.msg_id}, observed_at={self.observed_at!r})"
        )


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


class PollState(Base):
    """When one channel is next due to be polled, and why then.

    Timing only. The *position* — how far collection has got in this
    channel — stays on ``BackfillState.newest_fetched_id``, which has
    been recorded since the backfill change as the high-water mark that
    incremental collection would read. The poll loop is that reader, and
    it reads it in place: one fact, one table, no copy to drift.

    This is deliberately not a column on ``BackfillState``, even though
    both tables are keyed by channel and both describe collection.
    ``BackfillStatus`` has terminal values — ``COMPLETE``, and a channel
    capped for good — and polling has no terminal state at all. A single
    status column meaning both "this walk is over" and "this channel is
    checked forever" is where the confusion would start, and it would
    start silently.

    A channel with no row here is due immediately, which is what makes
    the table seed itself: the first pass over the inventory is the
    seeding pass, and nothing has to backfill it.
    """

    __tablename__ = "poll_state"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )

    # The loop's one access path: what is due, oldest first.
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Cached, not measured per tick: it is an input to a schedule, not a
    # number anyone reads, and recomputing it every time the loop looked
    # at a channel would cost more in queries than the polling costs in
    # requests.
    posts_per_day: Mapped[float | None] = mapped_column(Float)
    posts_per_day_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # How many consecutive polls found nothing new, and the last error if
    # the poll failed outright. Both lengthen the interval; a poll that
    # succeeds resets them. A channel that has gone quiet and a channel
    # that is broken are backed off the same way and recorded
    # differently, because only one of them is worth reporting.
    consecutive_empty: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"PollState(channel_id={self.channel_id}, due_at={self.due_at!r})"
        )


class Edge(Base):
    """One observed reference from one channel to another.

    An observation, not an aggregate: a channel that reposts another ten
    times is ten rows, each carrying the message it came from and when it
    was published. Weights, decay and clustering are computed from these
    rows and never stored here — that is the analysis this table feeds,
    not part of it.

    Where the payload names the referenced *post* — a forward's original
    message id, a ``t.me/name/123`` link — it is carried too, alongside
    the referenced post's own publication date. The interval between the
    two dates (how fast the post travelled) is a subtraction over two
    columns and is deliberately not stored: it would be the first derived
    measure to live in the observation table.

    The natural key is ``(src, msg_id, kind, dst, dst_msg_id)``: one
    message can forward from a channel and also mention it, and two links
    to different posts of the same channel are two references, but the
    same message referencing the same post the same way twice is one edge.
    ``dst_msg_id`` is nullable — a plain mention or a forward that names no
    original post has nothing to put there — so the constraint declares
    ``NULLS NOT DISTINCT``: without it Postgres would treat two such edges
    as distinct and every re-run would insert the mention again, since
    ``ON CONFLICT DO NOTHING`` never fires on a conflict Postgres does not
    consider one. Rows are inserted with ``ON CONFLICT DO NOTHING``, so
    re-deriving over unchanged raw data writes nothing.

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
            "dst_msg_id",
            name="uq_edges_reference",
            postgresql_nulls_not_distinct=True,
        ),
        # Every post-level question filters on the referenced post — which
        # channel, which message. A composite index serves that pair; its
        # leftmost prefix also covers `dst_channel_id` alone, so it is
        # explicitly named to sit alongside the single-column index the
        # foreign key already needs rather than collide with it.
        Index(
            "ix_edges_dst_channel_id_dst_msg_id",
            "dst_channel_id",
            "dst_msg_id",
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

    # The referenced post, where the payload names one: its per-channel id
    # and its original publication date. Both nullable — a plain mention
    # and a forward naming no original post leave them empty.
    dst_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    dst_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # The album group of the *referencing* message, carried so a forwarded
    # album can later be counted as one event. Only meaningful together
    # with the channel — group on `(src_channel_id, grouped_id)`, never on
    # `grouped_id` alone. Stored, never applied: collapsing an album is a
    # counting decision that belongs to analysis, not to derivation.
    grouped_id: Mapped[int | None] = mapped_column(BigInteger)

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


class PendingMentionSource(Base):
    """One channel that mentioned one pending username.

    A set of pairs rather than a counter on ``PendingMention``, and the
    reason is idempotence. Derivation must be re-runnable — a second pass
    over unchanged raw messages writes nothing — and an increment always
    writes. A pair inserted ``ON CONFLICT DO NOTHING`` cannot double-count
    however many times the pass repeats, and the count falls out of a
    ``COUNT(*)``: the composite primary key already makes it distinct, so
    nothing has to ask for ``DISTINCT``.

    What it buys is an ordering. Every pending username costs one
    ``contacts.resolveUsername``, the scarcest request in the project, and
    87% of them are mentioned by exactly one channel — degree-one vertices
    in a graph about who talks to whom. Resolving by weight of evidence
    spends a two-week queue's worth of quota on the two days that matter.

    No foreign key onto ``channels``: those rows are never deleted, so the
    constraint could never fire, and it would make truncation
    order-dependent for nothing. The one onto ``pending_mentions`` earns
    its place — that table *is* truncated, and its rows *are* deleted the
    moment a username resolves.
    """

    __tablename__ = "pending_mention_sources"

    username: Mapped[str] = mapped_column(
        ForeignKey("pending_mentions.username", ondelete="CASCADE"),
        primary_key=True,
    )
    # The channel whose message carried the mention. Not a foreign key —
    # see the class docstring.
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    def __repr__(self) -> str:
        return (
            f"PendingMentionSource(username={self.username!r}, "
            f"channel_id={self.channel_id})"
        )


class AffiliationRun(Base):
    """One affiliation detection run, and the parameters it used.

    Every threshold and weight is a column here rather than on each
    candidate, because a run produces on the order of a hundred pairs and
    copying eleven numbers onto each of them stores the same fact a
    hundred times. A candidate points back at the run it was last
    measured by, which is what makes "under which thresholds was this
    proposed" answerable without re-running detection — and what
    distinguishes a proposal from before a threshold changed from one
    after.

    The coverage columns are here for the same reason they are printed: a
    signal that could speak about 40% of the inventory and found little
    is not the same result as one that looked everywhere and found
    little, and only the denominator tells them apart.
    """

    __tablename__ = "affiliation_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Thresholds, in the order the signals are described in the spec.
    min_out_edges: Mapped[int] = mapped_column(Integer)
    max_share_min: Mapped[float] = mapped_column(Float)
    min_token_length: Mapped[int] = mapped_column(Integer)
    max_token_channels: Mapped[int] = mapped_column(Integer)
    min_mutual_edges: Mapped[int] = mapped_column(Integer)

    # Which edge kinds the two edge-based signals counted. Text rather
    # than the enum: an array of a Postgres enum is awkward to migrate,
    # and this column is read by a human, not joined on.
    edge_kinds: Mapped[list[str]] = mapped_column(ARRAY(Text))

    weight_about: Mapped[float] = mapped_column(Float)
    weight_token: Mapped[float] = mapped_column(Float)
    weight_share: Mapped[float] = mapped_column(Float)
    weight_mutual: Mapped[float] = mapped_column(Float)

    # What the run could see. `refs_outside_inventory` counts handles
    # parsed out of descriptions that name no channel the inventory
    # holds — mostly an author's uncollected channels and personal
    # accounts, and a discovery lead rather than a failure.
    channels_scored: Mapped[int] = mapped_column(Integer)
    with_description: Mapped[int] = mapped_column(Integer)
    refs_outside_inventory: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"AffiliationRun(id={self.id}, ran_at={self.ran_at!r})"


class AffiliationCandidate(Base):
    """Two channels that may share an author, and why anyone thinks so.

    A proposal, never a conclusion — until the operator confirms it, at
    which point **this table is the family**. Detection writes rows here
    and may write nothing else; only the confirmation command sets
    ``decision``. That split is the whole design: no threshold on this
    data separates an author's second channel from a close collaborator,
    so the ranking exists to order a human's attention, not to replace
    it.

    The confirmed rows are also the sole record of who shares an author.
    A family is a connected component of them, read through the
    ``channel_families`` view; nothing stores membership separately, so
    nothing can disagree with the pairs. Merging two families is
    therefore one confirmed row, and splitting one is the removal of a
    row — neither is an operation anything has to implement.

    The pair is unordered and stored once, with ``channel_a <
    channel_b``. Sorting by id rather than by whichever signal fired
    first is what makes the primary key do the deduplication: two signals
    reaching the same pair from opposite directions produce one row
    whatever order they run in.

    Evidence gets one nullable column per signal rather than a JSONB
    document. The raw layer is where documents belong; everything derived
    here has explicit columns, as ``edges`` does. A fifth signal costs a
    migration, which is the same trade ``EdgeKind`` makes and for the
    same reason — a new signal is a deliberate change and can afford one.

    Re-running detection refreshes ``score``, every evidence column and
    ``run_id``, and touches none of the decision columns. That is what
    lets a threshold be re-tried as often as the operator likes without
    ever costing a review already done.
    """

    __tablename__ = "affiliation_candidates"
    __table_args__ = (
        # The unordered pair, made canonical. Without this a pair could
        # be stored twice, once each way round, and the primary key
        # would deduplicate neither.
        CheckConstraint("channel_a < channel_b", name="pair_is_ordered"),
        # A decision is exactly as timestamped as it is made: pending
        # rows carry no date, decided ones must.
        CheckConstraint(
            "(decision = 'pending') = (decided_at IS NULL)",
            name="decided_has_timestamp",
        ),
        # The ranking is the table's one access path.
        Index("ix_affiliation_candidates_score", "score"),
    )

    channel_a: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    channel_b: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )

    score: Mapped[float] = mapped_column(Float)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("affiliation_runs.id", ondelete="SET NULL")
    )

    # --- evidence, one group per signal; null means it did not fire ---

    # Which channel's description named which. `MUTUAL` is the strongest
    # single piece of evidence in the whole design and also the rarest —
    # a reference found one way is not weakened by a return link that
    # could not be checked, because most targets have no stored
    # description to check.
    about_direction: Mapped[AboutDirection | None] = mapped_column(
        _pg_enum(AboutDirection, "about_direction")
    )

    # The shared username token and how many channels carry it. The count
    # travels with the token because it is what decides the strength: a
    # token on two channels is an author, on eleven it is a subject.
    shared_token: Mapped[str | None] = mapped_column(Text)
    shared_token_channels: Mapped[int | None] = mapped_column(Integer)

    # The concentration signal: what share of one channel's outgoing
    # edges went to the other, the denominator that share was taken over,
    # and which of the two was the concentrated one — the signal is
    # directional even though the pair is not.
    out_share: Mapped[float | None] = mapped_column(Float)
    out_share_edges: Mapped[int | None] = mapped_column(Integer)
    out_share_src: Mapped[int | None] = mapped_column(BigInteger)

    # Edge counts each way, against the pair's stored order.
    edges_a_to_b: Mapped[int | None] = mapped_column(Integer)
    edges_b_to_a: Mapped[int | None] = mapped_column(Integer)

    # --- the review ---

    decision: Mapped[AffiliationDecision] = mapped_column(
        _pg_enum(AffiliationDecision, "affiliation_decision"),
        server_default=AffiliationDecision.PENDING.value,
    )
    origin: Mapped[CandidateOrigin] = mapped_column(
        _pg_enum(CandidateOrigin, "candidate_origin"),
        server_default=CandidateOrigin.SIGNAL.value,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    decision_note: Mapped[str | None] = mapped_column(Text)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"AffiliationCandidate(a={self.channel_a}, b={self.channel_b}, "
            f"score={self.score:.3f}, decision={self.decision.value!r})"
        )


class Alert(Base):
    """One thing worth telling the operator, and whether they were told.

    The interface between detection and delivery, and deliberately the
    *only* one: a pass writes rows here and a bot reads them, so either
    can move to another machine without the other changing. They will not
    always be on one machine — the collector needs a residential IP and a
    Bot API token does not — and a function call would have to become a
    table later, at a moment when something else is also changing.

    **The unique constraint is the escalation logic.** A post reaching
    two families raises one row; the same post reaching three raises a
    second, because the tuple differs; a post standing still raises
    nothing more, because it does not. There is therefore no "have I
    already said this" flag and no counter to get wrong, and a re-run
    cannot double-send. Anything added beside this constraint to track
    what was already raised is duplicating it.

    **What is stored is what it took to decide, not what will be shown.**
    No rendered message, no copy of which channels carried the post —
    that is a query over ``edges`` at rendering time, and storing it
    would put a derived measure in an observation table, the trade
    ``Edge`` already refuses when it carries two dates and declines to
    store the interval between them. ``value`` is the exception and
    earns it: it is the number that crossed the threshold, and the
    feedback record has to be about something that does not move.

    The visible consequence is that a digest read in the morning shows
    fresher numbers than the moment the alert was raised. That is
    intended. The question a reader has is how far a post went, not how
    far it had gone when a scheduled job noticed.

    Evidence as nullable per-kind columns was the alternative, following
    ``AffiliationCandidate``, and it is right there and wrong here: the
    scoring change brings three rate-based kinds with four numbers each,
    and a table with fourteen mostly-null columns would describe the
    union of its producers rather than what an alert is.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        # An alert about a post nothing collected should not be
        # representable. Same composite key `MessageMetric` references,
        # so it costs no index of its own.
        ForeignKeyConstraint(
            ["channel_id", "msg_id"],
            ["raw_messages.channel_id", "raw_messages.msg_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "kind",
            "channel_id",
            "msg_id",
            "band",
            name="uq_alerts_post_band",
        ),
        # The bot's one access path, asked on every tick forever: what is
        # still outstanding. Partial, because the answer is almost always
        # a handful of rows out of everything ever raised.
        Index(
            "ix_alerts_undelivered",
            "raised_at",
            postgresql_where=text("delivered_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[AlertKind] = mapped_column(_pg_enum(AlertKind, "alert_kind"))

    channel_id: Mapped[int] = mapped_column(BigInteger)
    msg_id: Mapped[int] = mapped_column(BigInteger)

    # Which configured threshold tier this crossed, and what the measure
    # actually was. The band is what makes escalation a row rather than a
    # decision; the value is what the operator's verdict was about.
    band: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)

    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Null until it has actually been sent. Never set in the same
    # transaction as the send is attempted: see `db/alerts.py` for why
    # at-least-once is the honest guarantee here.
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivery: Mapped[AlertDelivery | None] = mapped_column(
        _pg_enum(AlertDelivery, "alert_delivery")
    )

    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"Alert(kind={self.kind.value!r}, channel_id={self.channel_id}, "
            f"msg_id={self.msg_id}, band={self.band})"
        )


class AlertFeedback(Base):
    """What the operator thought of one alert.

    Keyed by the alert rather than appended, so a changed mind replaces
    an earlier verdict instead of accumulating beside it — the question
    this table answers is "what do they think", not "what have they
    thought".

    An alert with no row here is **unanswered**, which is not a neutral
    verdict and must not be read as one. Most alerts will have no row;
    treating that as mild approval would make the labelled data say the
    opposite of what happened.
    """

    __tablename__ = "alert_feedback"

    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    verdict: Mapped[AlertVerdict] = mapped_column(
        _pg_enum(AlertVerdict, "alert_verdict")
    )
    given_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"AlertFeedback(alert_id={self.alert_id}, "
            f"verdict={self.verdict.value!r})"
        )


class BaselineRun(Base):
    """One refresh of the baselines, and the parameters it used.

    The same arrangement ``AffiliationRun`` uses, for the same reason: a
    refresh produces hundreds of channel medians and a few dozen curve
    points, and copying the parameters onto each would store one fact a
    thousand times. Everything a refresh writes points back here, so
    "under which parameters was this scored" is answerable without
    recomputing anything.

    It is also what makes a refresh *replace* rather than accumulate.
    Reading baselines means reading the newest completed run; the older
    rows stay, so a threshold argued about next month can be compared
    against the baselines it was actually arguing with rather than
    against whatever has been recomputed since.

    ``completed_at`` is null until the refresh finishes. A run that died
    half way leaves an incomplete set that nothing will read, rather than
    a set of medians for some channels and curves fitted without them.
    """

    __tablename__ = "baseline_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # How old a post must be for its counters to count as settled, and
    # how many such posts a channel needs before it has a median worth
    # dividing by. At 30 posts, 465 of 544 channels qualify.
    mature_days: Mapped[int] = mapped_column(Integer)
    min_channel_posts: Mapped[int] = mapped_column(Integer)
    # Below this a curve band or a spread is left unfitted rather than
    # fitted thinly: an absent band is a gap, a band fitted on four
    # observations is a wrong number.
    min_band_samples: Mapped[int] = mapped_column(Integer)

    # What the refresh could see. The denominator matters for the same
    # reason it does in `AffiliationRun`: a channel with no baseline and
    # a channel with a quiet week are different facts.
    channels_in_scope: Mapped[int] = mapped_column(Integer)
    channels_with_baseline: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"BaselineRun(id={self.id}, started_at={self.started_at!r})"


class ChannelBaseline(Base):
    """What one channel's settled post normally reaches, per metric.

    The median rather than the mean, and for the reason every baseline in
    this project is a median: one viral post moves a mean, and the viral
    post is the thing being measured — a baseline the outlier drags
    upward hides the next outlier.

    ``samples`` travels with it because a median over thirty posts and
    one over three hundred are not the same claim, and the run that
    scored a post has to be able to say which it had.
    """

    __tablename__ = "channel_baselines"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_runs.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    metric: Mapped[Metric] = mapped_column(
        _pg_enum(Metric, "metric"), primary_key=True
    )

    median: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return (
            f"ChannelBaseline(channel_id={self.channel_id}, "
            f"metric={self.metric.value!r}, median={self.median})"
        )


class MetricBaseline(Base):
    """How a metric behaves on a kind of channel: the join, and the ruler.

    ``factor`` is what relates a curve normalised to the eight-hour
    reading to a median computed over thirty days. Without it the two
    halves of the estimate are in different units and their product means
    nothing. Measured per metric and never shared: 0.44 for views against
    1.00 for comments, because views keep trickling for weeks while a
    comment happens when somebody reads the post.

    ``spread`` is the dispersion of ``log(actual / expected)`` once the
    curve and the factor have done their work — the number that decides
    what a threshold means. Stored rather than written into the code
    because it differs by more than it looks: 0.38 for views against 1.01
    for comments, and again by kind, from 0.26 on aggregators to 0.37 on
    personal channels. A constant in the source would apply one metric's
    shape to another and quietly outlive the measurement it came from.
    """

    __tablename__ = "metric_baselines"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_runs.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[ChannelKind] = mapped_column(
        _pg_enum(ChannelKind, "channel_kind"), primary_key=True
    )
    metric: Mapped[Metric] = mapped_column(
        _pg_enum(Metric, "metric"), primary_key=True
    )

    factor: Mapped[float] = mapped_column(Float)
    spread: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return (
            f"MetricBaseline(kind={self.kind.value!r}, "
            f"metric={self.metric.value!r}, spread={self.spread})"
        )


class CurvePoint(Base):
    """One point of a growth curve: the fraction reached by an age band.

    A fraction of the post's own eight-hour reading, which is what takes
    channel size out of the shape — a channel reaching two hundred
    readers and one reaching two hundred thousand say the same thing
    about "half way by two hours".

    Bands rather than exact ages because samples are irregular by design:
    quiet hours, a suspended laptop and a rate limit all move a reading
    away from its scheduled offset, and a curve fitted on exact ages
    would have almost no data.
    """

    __tablename__ = "curve_points"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_runs.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[ChannelKind] = mapped_column(
        _pg_enum(ChannelKind, "channel_kind"), primary_key=True
    )
    metric: Mapped[Metric] = mapped_column(
        _pg_enum(Metric, "metric"), primary_key=True
    )
    band: Mapped[str] = mapped_column(Text, primary_key=True)

    fraction: Mapped[float] = mapped_column(Float)
    samples: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return (
            f"CurvePoint(kind={self.kind.value!r}, "
            f"metric={self.metric.value!r}, band={self.band!r}, "
            f"fraction={self.fraction})"
        )


class FloodEvent(Base):
    """One rate limit the collector saw, and what caused it.

    Written because the question this answers is always asked after the
    fact — usually a day later, often about a run nobody was watching. A
    log line answers it only for someone who was already reading the log.

    A row is **not** proof that a request reached Telegram. Telethon keeps
    its own per-method ledger of outstanding waits and refuses a method
    that is still under one, and that refusal arrives as the same
    ``FloodWaitError`` as a real limit. Nothing on the exception marks
    which is which, so this table does not pretend to know; a reader who
    assumes every row cost a request will overcount what a run spent.

    Only long waits land here. Telethon sleeps through anything under
    ``flood_sleep_threshold`` itself, so a rising rate of *short* waits —
    the early warning that throttling has begun — is invisible from this
    table.
    """

    __tablename__ = "flood_events"
    __table_args__ = (
        # The two questions asked of this table: what happened lately, and
        # how often this particular method.
        Index("ix_flood_events_occurred_at", "occurred_at"),
        Index("ix_flood_events_method", "method"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # The innermost request class name, unwrapped from Telegram's
    # invocation wrappers, or `unknown` for a limit that named no request.
    method: Mapped[str] = mapped_column(Text)
    seconds: Mapped[int] = mapped_column(Integer)

    command: Mapped[CollectionCommand] = mapped_column(
        _pg_enum(CollectionCommand, "collection_command")
    )

    # Nullable: a limit hit during resolution, or before a walk starts,
    # belongs to no channel.
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.tg_id", ondelete="SET NULL")
    )

    halted: Mapped[bool] = mapped_column(server_default=false())

    def __repr__(self) -> str:
        return f"FloodEvent(method={self.method!r}, seconds={self.seconds})"
