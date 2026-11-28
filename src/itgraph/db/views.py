"""Views: schema objects that hold no data of their own.

Two, one per platform, and the same shape: which channels share an
owner, as the connected components of the confirmed affiliation pairs.
They live here rather than in ``models.py`` because a view is not a
model — nothing maps to it, and it is read by explicit select.

Attached to ``Base.metadata`` through a DDL event so that
``create_all`` builds them, which is what the test fixture uses. The
Alembic revision that introduced each carries its own copy of the same
SQL, deliberately: a revision is a historical snapshot and must keep
working whatever this file becomes later. Changing a view means editing
both, exactly as changing a column means editing a model and writing a
revision.
"""

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from itgraph.db.models import Base

# Which channels share an author, as the connected components of the
# confirmed pairs. One row per channel that is in a family of more than
# one; a channel absent from here is its own family of one, so the family
# of any channel is
#
#     COALESCE(family_key, tg_id)
#
# and an edge sits inside a family exactly when its endpoints' keys are
# equal. That is the expression the role metrics use to subtract an
# author's reposts of themselves.
#
# `UNION`, never `UNION ALL`. The pairs among one author's channels
# contain cycles by construction — A-B, B-C and A-C together are the
# shape that made the previous "canonical channel" model unworkable — and
# `UNION ALL` would recurse forever on one.
#
# `family_key` is the smallest id in the set: a deterministic *label* for
# the component and nothing else. No code treats that channel
# differently and it is never displayed. It is not a canonical channel
# under a new name — that distinction was removed on purpose, because a
# family is a set and none of an author's channels is the main one.
CHANNEL_FAMILIES_VIEW = """
CREATE VIEW tg_channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM tg_affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM tg_affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
)
SELECT channel_id, LEAST(MIN(reached), channel_id) AS family_key
  FROM reach
 GROUP BY channel_id
"""


# The same view over the YouTube pairs. Every comment above says the
# same thing about this one; `family_key` is the lexicographically
# smallest id in the set, which is a label and not a canonical channel
# for exactly the reasons the Telegram one is not.
#
# "Smallest" is said out loud as `COLLATE "C"`: ids are text, and the
# smallest under the database locale's collation is not the smallest by
# code points — the order every Python reader of the ids sorts by. The
# label would still be deterministic without it, but it would be a
# different channel than the one any in-memory sort would name.
YT_CHANNEL_FAMILIES_VIEW = """
CREATE VIEW yt_channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
)
SELECT channel_id,
       LEAST(MIN(reached COLLATE "C"), channel_id COLLATE "C") AS family_key
  FROM reach
 GROUP BY channel_id
"""


def _create_view(_target: Any, connection: Connection, **_kw: Any) -> None:
    connection.execute(text(CHANNEL_FAMILIES_VIEW))
    connection.execute(text(YT_CHANNEL_FAMILIES_VIEW))


# Plain functions rather than `DDL(...)`: SQLAlchemy's `DDL` is untyped,
# and silencing that would be a `# type: ignore` bought for nothing —
# these say the same thing and check.
event.listen(Base.metadata, "after_create", _create_view)
