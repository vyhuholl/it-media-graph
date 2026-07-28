"""Export the seed-only subgraph to GEXF for Gephi.

Exploratory tooling: not part of the package, not spec'd, not tested. Look at
the picture, change the constants, look again, throw it away.

    uv run notebooks/export_graph.py

Only seed -> seed edges are exported. Channels discovered by reference have no
outgoing edges yet — not because they have none, but because their history has
not been collected. Including them makes every centrality measure meaningless.
"""

import math
from datetime import UTC, datetime

import networkx as nx
import psycopg

DSN = "postgresql://itgraph:itgraph@localhost:5433/itgraph"
OUT = "seed_graph.gexf"

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


def decay(published_at: datetime, now: datetime) -> float:
    days = (now - published_at).total_seconds() / 86400.0
    return math.pow(0.5, days / HALF_LIFE_DAYS)


def main() -> None:
    now = datetime.now(UTC)
    g = nx.DiGraph()

    with psycopg.connect(DSN) as conn:
        for tg_id, username, title, kind in conn.execute(NODES):
            g.add_node(
                str(tg_id),
                label=title or username or str(tg_id),
                username=username or "",
                kind=kind or "",
            )

        for src, dst, published_at in conn.execute(EDGES):
            u, v = str(src), str(dst)
            w = decay(published_at, now)
            if g.has_edge(u, v):
                g[u][v]["weight"] += w
                g[u][v]["count"] += 1
            else:
                g.add_edge(u, v, weight=w, count=1)

    isolated = [n for n, d in g.degree() if d == 0]
    g.remove_nodes_from(isolated)

    nx.write_gexf(g, OUT)
    print(f"{g.number_of_nodes()} nodes, {g.number_of_edges()} edges -> {OUT}")
    print(f"{len(isolated)} isolated channels dropped")


if __name__ == "__main__":
    main()
