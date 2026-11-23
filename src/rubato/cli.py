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

    cfg = SynthConfig(seed=a.seed, urgent_applications=a.urgent)
    tr = generate(cfg)
    dump(tr, a.out)
    print(f"wrote {a.out}: {len(tr.jobs)} synthetic jobs, seed {a.seed}, fingerprint {tr.fingerprint()}")
    return 0


def cmd_simulate(a: argparse.Namespace) -> int:
    from .runs import run_scenario

    names = [n.strip() for n in a.policies.split(",") if n.strip()]
    m = run_scenario(a.trace, a.scenario, a.scenarios, a.policies_file, names, a.out, grid=not a.no_grid)
    for label in m["runs"]:
        s = json.loads((Path(a.out) / label / "summary.json").read_text(encoding="utf-8"))
        u, b = s["urgent"], s["background"]
        print(
            f"{label:<14} urgent on time {u['done_on_time']}/{u['received']}  "
            f"background on time {b['done_on_time']}/{b['received']} done {b['done']} pending {b['pending']}  "
            f"wait p95 {b['wait_p95']}"
        )
    print(f"wrote {a.out}")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    from .report import build_report
    from .runs import read_run

    p = build_report(read_run(a.run), a.report)
    print(f"wrote {p}")
    return 0


def cmd_explain(a: argparse.Namespace) -> int:
    from .runs import read_run

    data = read_run(a.run)
    label = a.policy or next(iter(data["runs"]))
    run = data["runs"][label]
    job = next((j for j in run["jobs"] if j["job"] == a.job), None)
    if job is None:
        print(f"job {a.job!r} not in run {label}", file=sys.stderr)
        return 2
    print(
        f"{label}: {job['job']} ({job['client']}/{job['class']}) arrival {job['arrival']} "
        f"[{job['arrival_provenance']}] deadline {job['deadline']}"
    )
    for d in run["decisions"]:
        if d["job"] == a.job:
            nxt = f" reconsider {d['next_t']}" if d["action"] == "defer" else ""
            print(
                f"  t={d['t']} {d['step']}#{d['attempt']} {d['action'].upper()} [{d['reason']}] {d['detail']}{nxt}"
            )
    print(f"  result: {job['status']}{' on time' if job['on_time'] else ''} finished {job['finished_at']}")
    return 0


def cmd_evaluate(a: argparse.Namespace) -> int:
    from .evaluate import default_blocks, evaluate, to_markdown

    pilot = json.loads(Path(a.pilot).read_text(encoding="utf-8")) if a.pilot else None
    r = evaluate(a.trace, a.profile, a.policies_file, default_blocks(), a.deadline_minutes, pilot=pilot)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluacion.json").write_text(json.dumps(r, indent=2, sort_keys=True), encoding="utf-8")
    (out / "evaluacion.md").write_text(to_markdown(r), encoding="utf-8")
    print(to_markdown(r))
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
    s.add_argument("--urgent", type=int, default=0, help="urgent applications already in the trace")
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("simulate", help="simulate a scenario under several policies")
    s.add_argument("--trace", required=True)
    s.add_argument("--scenario", required=True)
    s.add_argument("--policies", default="radar,reserva")
    s.add_argument("--scenarios", default="examples/scenarios.json")
    s.add_argument("--policies-file", default="examples/policies.json")
    s.add_argument("--out", required=True)
    s.add_argument("--no-grid", action="store_true", help="skip the precomputed reserve variants")
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("compare", help="build the self-contained HTML comparison")
    s.add_argument("--run", required=True)
    s.add_argument("--report", required=True)
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("explain", help="explain every decision about one job")
    s.add_argument("--run", required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--policy")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("evaluate", help="design/tuning/test evaluation on a (private) trace")
    s.add_argument("--trace", required=True)
    s.add_argument("--profile", required=True)
    s.add_argument("--policies-file", default="examples/policies-radar.json")
    s.add_argument("--pilot", default="config/piloto.json")
    s.add_argument("--deadline-minutes", type=int, default=60)
    s.add_argument("--out", default="private/eval")
    s.set_defaults(fn=cmd_evaluate)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
