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
    "BackfillState",
    "BackfillStatus",
    "Base",
    "CandidateOrigin",
    "Channel",
    "ChannelKind",
    "ChannelStatus",
    "CollectionCommand",
    "DiscoverySource",
    "Edge",
    "EdgeKind",
    "FailureKind",
    "FloodEvent",
    "PendingMention",
    "PendingMentionSource",
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
    """

    BACKFILL = "backfill"
    RESOLVE = "resolve"
    METADATA = "metadata"
    ADD = "add"


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
