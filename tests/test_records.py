#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx==0.28.1"]
# ///
"""Record-sharding tests. No network, no server; runs in well under a second.

A run's records are split across one file per rank band. Every bug this layer
can have is silent -- a record written to the wrong file still parses, a
record dropped on rewrite still leaves valid JSON behind, and derive would
report a slightly smaller web without complaining. So the invariant worth
testing is not "it writes a file" but "what comes back is what went in".

    uv run tests/test_records.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import observatory as obs  # noqa: E402

failures: list[str] = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n    got  {got!r}\n    want {want!r}")
    else:
        print(f"  ok  {name}")


def rec(host, rank, **kw):
    r = {"host": host, "rank_at_freeze": rank, "errors": [],
         "policy": {"file_state": "present"}}
    r.update(kw)
    return r


# ---------------------------------------------------------------- routing ---
print("shard routing")
check("rank 1 -> first band", obs.shard_name(1), "rank-00001-01000.jsonl")
check("band is inclusive at the top", obs.shard_name(1000), "rank-00001-01000.jsonl")
check("and exclusive just past it", obs.shard_name(1001), "rank-01001-02500.jsonl")
check("last band's upper bound", obs.shard_name(20000), "rank-10001-20000.jsonl")
check("past the last band", obs.shard_name(20001), "rank-beyond.jsonl")
check("no rank at all", obs.shard_name(None), "rank-unknown.jsonl")
check("rank zero is not a rank", obs.shard_name(0), "rank-unknown.jsonl")
# A bool is an int in Python; it must not be read as rank 1.
check("True is not rank 1", obs.shard_name(True), "rank-unknown.jsonl")

# Every band in RANK_BANDS must be reachable, or a shard silently never fills.
reached = {obs.shard_name(lo) for lo, _ in obs.RANK_BANDS}
check("every band is reachable", len(reached), len(obs.RANK_BANDS))

# --------------------------------------------------------------- round trip -
print("\nround trip")
with tempfile.TemporaryDirectory() as td:
    run = Path(td) / "2026-01-01"
    run.mkdir()
    recs = [rec("a.com", 1), rec("b.com", 1500), rec("c.com", 17000),
            rec("d.com", 99999), rec("e.com", None)]
    obs.write_records(run, recs)

    back = obs.load_records(run)
    check("every record comes back", len(back), len(recs))
    check("hosts survive", {r["host"] for r in back}, {r["host"] for r in recs})
    check("content is unchanged",
          sorted(json.dumps(r, sort_keys=True) for r in back),
          sorted(json.dumps(r, sort_keys=True) for r in recs))
    check("one file per occupied band", len(obs.shard_paths(run)), 5)
    check("the out-of-range host went to rank-beyond",
          (run / obs.RECORDS_DIR / "rank-beyond.jsonl").exists(), True)

    # A rewrite must not leave a copy behind in the band a record came from.
    moved = [rec("a.com", 12000)] + [r for r in recs if r["host"] != "a.com"]
    obs.write_records(run, moved)
    back = obs.load_records(run)
    check("a record that changes band is not duplicated",
          [r["host"] for r in back].count("a.com"), 1)
    check("and it moved", obs.shard_name(
        next(r for r in back if r["host"] == "a.com")["rank_at_freeze"]),
        "rank-10001-20000.jsonl")
    check("the band it left is gone, not left empty",
          (run / obs.RECORDS_DIR / "rank-00001-01000.jsonl").exists(), False)

# ------------------------------------------------------------------ legacy --
print("\nlegacy single-file runs")
with tempfile.TemporaryDirectory() as td:
    run = Path(td) / "2026-01-01"
    run.mkdir()
    legacy = run / "records.jsonl"
    legacy.write_text("".join(json.dumps(rec(f"h{i}.com", i * 100), sort_keys=True)
                              + "\n" for i in range(1, 6)), encoding="utf-8")
    check("a pre-shard run is still found", obs.has_records(run), True)
    check("and still reads", len(obs.load_records(run)), 5)

    obs.write_records(run, obs.load_records(run))
    check("resharding keeps every record", len(obs.load_records(run)), 5)
    check("and removes the single file", legacy.exists(), False)
    check("shards take over", len(obs.shard_paths(run)) >= 1, True)

# ------------------------------------------------------------ torn records --
print("\na killed run's torn final line")
with tempfile.TemporaryDirectory() as td:
    run = Path(td) / "2026-01-01"
    (run / obs.RECORDS_DIR).mkdir(parents=True)
    good = json.dumps(rec("a.com", 1), sort_keys=True)
    (run / obs.RECORDS_DIR / "rank-00001-01000.jsonl").write_text(
        good + "\n" + '{"host": "b.com", "rank_at_f', encoding="utf-8")

    check("tolerant reading skips it", len(obs.load_records(run, tolerant=True)), 1)
    # Strict reading must refuse: a short count is a wrong denominator, and
    # every share on the page is computed against it.
    try:
        obs.load_records(run)
        got = "returned"
    except SystemExit:
        got = "exited"
    check("strict reading refuses to continue", got, "exited")

# ------------------------------------------------------------------ writer --
print("\nShardWriter")
with tempfile.TemporaryDirectory() as td:
    run = Path(td) / "2026-01-01"
    run.mkdir()
    with obs.ShardWriter(run) as w:
        for i in range(1, 21):
            w.write(rec(f"h{i}.com", i * 900))
    check("every written record is readable", len(obs.load_records(run)), 20)
    check("spread across bands", len(obs.shard_paths(run)) > 1, True)

    # Resume appends; it must not truncate what a previous attempt wrote.
    with obs.ShardWriter(run) as w:
        w.write(rec("late.com", 5))
    back = obs.load_records(run)
    check("appending preserves earlier records", len(back), 21)
    check("including the new one", "late.com" in {r["host"] for r in back}, True)

print()
if failures:
    print(f"{len(failures)} FAILED\n")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("all passed")
