#!/usr/bin/env python3
"""A stand-in for `k3s kubectl`, for running the role with no cluster.

Records every call, its standard input included, and answers from rules a
test wrote: the first whose pattern matches the joined arguments. A rule with
`times` stops matching after that many, which is how a test says what changes
between two reads. No rule matching is an empty success.
"""

import json
import os
import re
import sys
from pathlib import Path

state = Path(os.environ["FAKE_KUBECTL_DIR"])
arguments = sys.argv[1:]
joined = " ".join(arguments)
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()

with (state / "calls.jsonl").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps({"args": arguments, "stdin": stdin}) + "\n")

rules = json.loads((state / "rules.json").read_text(encoding="utf-8"))
counts_path = state / "counts.json"
counts = (
    json.loads(counts_path.read_text(encoding="utf-8"))
    if counts_path.exists()
    else {}
)

for index, rule in enumerate(rules):
    if not re.search(rule["match"], joined):
        continue
    used = counts.get(str(index), 0)
    if "times" in rule and used >= rule["times"]:
        continue
    counts[str(index)] = used + 1
    counts_path.write_text(json.dumps(counts), encoding="utf-8")
    sys.stdout.write(rule.get("stdout", ""))
    sys.stderr.write(rule.get("stderr", ""))
    sys.exit(rule.get("rc", 0))
