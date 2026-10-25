"""What the YouTube API Services Developer Policies fix, as constants.

Not settings: a setting invites someone to raise it, and these are not
ours to raise. Pure — no settings, no database — so the configuration,
the retention pass and the review file can all import the same number.
"""

from datetime import date, timedelta

__all__ = [
    "DERIVED_METRICS_ACCEPTED_ON",
    "RETENTION",
    "STATISTICS_RETENTION_MONTHS",
]

# How long anything read with the API key may be kept without being
# fetched again: titles, handles and descriptions as Non-Authorized Data
# (III.E.4.d), statistics read without authorization (III.E.4.b). The
# derived-metrics amendment extends statistics to 36 months and leaves
# titles and descriptions here, so this holds whatever happens to the
# application.
RETENTION = timedelta(days=30)

# The day YouTube accepted itgraph's analytics use case under the
# Additional policies on derived metrics and data storage, or None while
# it has not. A recorded fact, not a limit: set by a commit that also
# changes docs/privacy.html and records the acceptance in docs/PLAN.md,
# never by a setting, which could be flipped without either. From that
# date, readings taken on or after it may be kept up to the months below
# and their videos are refreshed rather than left to expire. Readings
# taken before it keep 30 days — the policy says nothing about them, and
# the application promised 30 days "until it is accepted".
DERIVED_METRICS_ACCEPTED_ON: date | None = None

# What the amendment allows statistics, in calendar months.
STATISTICS_RETENTION_MONTHS = 36
