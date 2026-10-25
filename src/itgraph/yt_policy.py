"""What the YouTube API Services Developer Policies fix, as constants.

Not settings: a setting invites someone to raise it, and these are not
ours to raise. Pure — no settings, no database — so the configuration,
the retention pass and the review file can all import the same number.
"""

from datetime import timedelta

__all__ = ["RETENTION"]

# How long anything read with the API key may be kept without being
# fetched again: titles, handles and descriptions as Non-Authorized Data
# (III.E.4.d), statistics read without authorization (III.E.4.b). The
# derived-metrics amendment extends statistics to 36 months and leaves
# titles and descriptions here, so this holds whatever happens to the
# application.
RETENTION = timedelta(days=30)
