"""Command line: ``rubato <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_import_radar(a: argparse.Namespace) -> int:
    from .importers.radar import import_radar, load_salt
    from .trace import dump

    src = Path(a.radar_dir)
    out = Path(a.out)
    salt = load_salt(out)
    res = import_radar(src / "google-ai.db", src / "radar.db", a.day_from, a.day_to, salt)
    stem = f"radar-{a.day_from}-{a.day_to}"
    dump(res.trace, out / f"{stem}.jsonl")
    (out / f"{stem}.report.json").write_text(
        json.dumps(res.report, indent=2, sort_keys=True), encoding="utf-8"
    )
    r = res.report
    print(
        f"read {r['read']}  included {r['included']}  discarded {sum(r['discarded'].values())} {r['discarded']}"
    )
    print(f"jobs {r['jobs']} {r['jobs_by_class']}  attempts {r['attempts']}  reconciled={r['reconciled']}")
    print(f"control {r['control']}  fingerprint {r['fingerprint']}")
    print(f"wrote {out / (stem + '.jsonl')}")
    return 0 if r["reconciled"] else 1


def cmd_synth(a: argparse.Namespace) -> int:
    from .synth import SynthConfig, generate
    from .trace import dump

    cfg = SynthConfig(seed=a.seed)
    tr = generate(cfg)
    dump(tr, a.out)
    print(f"wrote {a.out}: {len(tr.jobs)} synthetic jobs, seed {a.seed}, fingerprint {tr.fingerprint()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rubato", description="Shared-budget scheduler for AI workloads")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-radar", help="read-only import of Swiss Job Radar history (private output)")
    s.add_argument("--radar-dir", required=True, help="folder holding google-ai.db and radar.db")
    s.add_argument("--from", dest="day_from", required=True)
    s.add_argument("--to", dest="day_to", required=True)
    s.add_argument("--out", default="private")
    s.set_defaults(fn=cmd_import_radar)

    s = sub.add_parser("synth", help="generate a synthetic trace")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_synth)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
