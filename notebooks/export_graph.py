"""Export the seed-only subgraph to GEXF for Gephi.

Exploratory tooling: not part of the package, not spec'd, not tested. Look at
the picture, change the constants, look again, throw it away.

    uv sync --group data
    uv run notebooks/export_graph.py

Only seed -> seed edges are exported. Channels discovered by reference have no
outgoing edges yet — not because they have none, but because their history has
not been collected. Including them makes every centrality measure meaningless.

Edges inside one family of affiliated channels are dropped. A network of
channels run by the same author reposts itself constantly, and that traffic
says nothing about who influences whom — left in, it hands the largest family
the top of every centrality ranking.
"""

import os
import math
from datetime import UTC, datetime
from pathlib import Path

import networkx as nx
import psycopg
from dotenv import load_dotenv()

load_dotenv()

DSN = os.environ("DATABASE_URL")
OUT = Path(__file__).resolve().parent.parent / "data" / "seed_graph.gexf"

HALF_LIFE_DAYS = 90.0

NODES = """
    SELECT tg_id, username, title, kind::text
    FROM channels
    WHERE status = 'seed'
"""

EDGES = """
    SELECT e.src_channel_id, e.dst_channel_id, e.published_at
    FROM edges e
    JOIN channels s ON s.tg_id = e.src_channel_id AND s.status = 'seed'
    JOIN channels d ON d.tg_id = e.dst_channel_id AND d.status = 'seed'
"""

# Connected components of the confirmed affiliation pairs. A channel
# missing from the view is a family of one, hence the COALESCE below.
FAMILIES = """
    SELECT channel_id, family_key
    FROM channel_families
"""


def decay(published_at: datetime, now: datetime) -> float:
    days = (now - published_at).total_seconds() / 86400.0
    return math.pow(0.5, days / HALF_LIFE_DAYS)


def main() -> None:
    now = datetime.now(UTC)
    g = nx.DiGraph()
    intra_family = 0

    with psycopg.connect(DSN) as conn:
        families = {
            channel_id: key for channel_id, key in conn.execute(FAMILIES)
        }

        for tg_id, username, title, kind in conn.execute(NODES):
            g.add_node(
                str(tg_id),
                label=title or username or str(tg_id),
                username=username or "",
                kind=kind or "",
                family=str(families.get(tg_id, tg_id)),
            )

        for src, dst, published_at in conn.execute(EDGES):
            if families.get(src, src) == families.get(dst, dst):
                intra_family += 1
                continue
            u, v = str(src), str(dst)
            w = decay(published_at, now)
            if g.has_edge(u, v):
                g[u][v]["weight"] += w
                g[u][v]["count"] += 1
            else:
                g.add_edge(u, v, weight=w, count=1)

    isolated = [n for n, d in g.degree() if d == 0]
    g.remove_nodes_from(isolated)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    nx.write_gexf(g, OUT)
    print(f"{g.number_of_nodes()} nodes, {g.number_of_edges()} edges -> {OUT}")
    print(f"{intra_family} intra-family edges dropped")
    print(f"{len(isolated)} isolated channels dropped")


if __name__ == "__main__":
    main()
