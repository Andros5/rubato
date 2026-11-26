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
    from importlib.resources import files

    from .runs import run_scenario

    names = [n.strip() for n in a.policies.split(",") if n.strip()]
    data = files("rubato.data")
    scenarios = (
        data.joinpath("scenarios.json")
        if a.scenarios == "examples/scenarios.json" and not Path(a.scenarios).exists()
        else a.scenarios
    )
    policies = (
        data.joinpath("policies.json")
        if a.policies_file == "examples/policies.json" and not Path(a.policies_file).exists()
        else a.policies_file
    )
    m = run_scenario(
        a.trace,
        a.scenario,
        scenarios,
        policies,
        names,
        a.out,
        grid=not a.no_grid,
        tariff_path=a.tariff,
    )
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


def cmd_explore(a: argparse.Namespace) -> int:
    from .explore import run_explore

    return run_explore(a.run, a.spec, a.out)


def cmd_bundle(a: argparse.Namespace) -> int:
    from .bundle import bundle

    result = bundle(a.run, a.out)
    print(f"{result['kind']} · {a.out}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    from zipfile import BadZipFile

    from .bundle import verify

    try:
        result = verify(a.archive, replay=a.replay)
    except BadZipFile as exc:
        raise ValueError(f"invalid bundle archive {a.archive}: {exc}") from exc
    print(f"{result['kind']} · {len(result['files'])} archivos verificados")
    return 0


def cmd_replay_incident(a: argparse.Namespace) -> int:
    from .policies import make_policy
    from .service.recording import replay_capture

    capture = json.loads(a.capture.read_text(encoding="utf-8"))
    if not isinstance(capture, dict) or capture.get("body", {}).get("schema") != "rubato.incident/v1":
        raise ValueError("expected a rubato.incident/v1 export from the live panel")
    candidate = None
    if a.policy is not None:
        config = json.loads(a.policy.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("name"), str):
            raise ValueError("--policy expects a JSON object with a policy name")
        try:
            candidate = make_policy(config.pop("name"), **config).config()
        except TypeError as exc:
            raise ValueError(f"invalid policy configuration: {exc}") from exc
    result = replay_capture(capture, candidate, a.job)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["matched_rounds"] != result["coverage"]["rounds"])


def cmd_import_csv(a: argparse.Namespace) -> int:
    from .importers.csv import convert

    print(json.dumps(convert(a.file, a.mapping, a.out), ensure_ascii=False, indent=2))
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    from collections import Counter

    from .trace import load

    try:
        tr = load(a.file)
    except (OSError, ValueError) as exc:
        if not a.json:
            raise
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    result = {
        "valid": True,
        "fingerprint": tr.fingerprint(),
        "jobs": len(tr.jobs),
        "resources": len(tr.resources),
        "arrival_provenance": dict(Counter(j.arrival_provenance for j in tr.jobs)),
        "jobs_with_deadline": sum(j.deadline is not None for j in tr.jobs),
    }
    print(json.dumps(result, indent=2 if a.json else None))
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    import platform

    from .local import LocalError, LocalStack, probe

    try:
        import tkinter

        tk = tkinter.TkVersion
    except ImportError:
        tk = None
    checks = {"environment": a.env, "python": platform.python_version(), "tk": tk, "simulator": True}
    status = 0
    if not a.offline:
        try:
            stack = LocalStack(a.root, demo=a.env == "entrevista").resolve()
            _, ports = stack.config()
            stack.check_ports(ports, stack.containers(verify=True))
            state, _ = stack.status()
            checks.update({"services": state, "ports": ports, "api": probe(ports["api"], "/readyz")})
        except LocalError as exc:
            checks["docker"] = str(exc)
            status = 1
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return status


def cmd_digest(a: argparse.Namespace) -> int:
    import os
    import re

    from .sdk import RubatoClient

    span = re.fullmatch(r"(\d{1,7})([smhd])", a.since)
    if not span:
        raise ValueError("--since expects a duration such as 24h")
    seconds = int(span[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[span[2]]
    client = RubatoClient(a.api, a.token or os.environ.get("RUBATO_ADMIN_TOKEN", "admin-local-token"))
    try:
        report = client._req("GET", f"/v1/admin/digest?seconds={seconds}")
    finally:
        client.http.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_risk_evaluate(a: argparse.Namespace) -> int:
    from .risk import calibrate
    from .runs import read_run

    design, test = (read_run(p) for p in (a.design, a.test))
    end = max(j["arrival"] for j in design["runs"][a.policy]["jobs"])
    start = min(j["arrival"] for j in test["runs"][a.policy]["jobs"])
    if end >= start:
        raise ValueError("the calibration period must precede the evaluation period")
    print(json.dumps(calibrate(design["runs"][a.policy], test["runs"][a.policy]), indent=2))
    return 0


def cmd_explain(a: argparse.Namespace) -> int:
    from .explain import fmt_t
    from .runs import read_run

    data = read_run(a.run)
    unit = data["manifest"]["trace"].get("time_unit", "s")
    label = a.policy or next(iter(data["runs"]))
    run = data["runs"][label]
    job = next((j for j in run["jobs"] if j["job"] == a.job), None)
    if job is None:
        print(f"job {a.job!r} not in run {label}", file=sys.stderr)
        return 2
    dl = f", deadline {fmt_t(job['deadline'], unit)}" if job["deadline"] is not None else ""
    print(
        f"{label}: {job['job']} ({job['client']}/{job['class']}) arrived {fmt_t(job['arrival'], unit)} "
        f"[{job['arrival_provenance']}]{dl}"
    )
    for d in run["decisions"]:
        if d["job"] == a.job:
            nxt = f"; reconsider {fmt_t(d['next_t'], unit)}" if d["action"] == "defer" else ""
            detail = f" {d['detail']}" if d["detail"] else ""
            print(
                f"  {fmt_t(d['t'], unit)} {d['step']}#{d['attempt']} {d['action'].upper()} [{d['reason']}]{detail}{nxt}"
            )
    end = f" at {fmt_t(job['finished_at'], unit)}" if job["finished_at"] is not None else ""
    state = job["status"] + (" on time" if job["on_time"] else (" late" if job["status"] == "done" else ""))
    print(f"  result: {state}{end}")
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


def cmd_shadow_report(a: argparse.Namespace) -> int:
    from .shadow import shadow_report, to_text

    template = json.loads(a.template.read_text(encoding="utf-8")) if a.template else None
    r = shadow_report(a.file, template)
    print(json.dumps(r, indent=2) if a.json else to_text(r))
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    """One-screen summary of a running service (the panel, in the terminal)."""
    import os
    from datetime import datetime

    import httpx

    token = a.token or os.environ.get("RUBATO_ADMIN_TOKEN", "admin-local-token")
    try:
        r = httpx.get(f"{a.api}/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except httpx.HTTPError as e:
        print(f"Rubato is not reachable at {a.api}: {e}. Is Docker running? (docker compose up -d)")
        return 2
    if r.status_code != 200:
        print(f"{r.status_code}: {r.text[:200]}")
        return 2
    d = r.json()

    def hhmm(t):
        return datetime.fromtimestamp(t).strftime("%H:%M") if t else "-"

    print(f"Rubato at {a.api} · policy {d['policy']['label']} · {hhmm(d['t'])}")
    print("Clients: " + ", ".join(f"{c['id']}={c['mode']}" for c in d["clients"]))
    for q in d["resources"]:
        tail = f", resets {hhmm(q['resets_at'])}" if q["resets_at"] else ""
        print(f"  {q['id']:<18} {q['used']:>7}/{q['capacity']:<7} left {q['available']}{tail}")
    for r_ in d["reasons"]:
        print(f"  waiting {r_['client_id']}: {r_['n']} × {r_['last_reason']} (next {hhmm(r_['next'])})")
    if d["unknown_attempts"]:
        print(f"  ! {len(d['unknown_attempts'])} uncertain attempt(s) to reconcile in {a.api}/panel")
    for p in d["pauses"]:
        print(f"  ! provider pause {p['scope_kind']} {p['scope_value']} until {hhmm(p['until'])}")
    return 1 if d["unknown_attempts"] else 0


def cmd_why(a: argparse.Namespace) -> int:
    """Explain a live job of the service: its steps, attempts and every decision with its reason."""
    import os
    from datetime import datetime

    import httpx

    token = a.token or os.environ.get("RUBATO_ADMIN_TOKEN", "admin-local-token")
    h = {"Authorization": f"Bearer {token}"}

    def hhmm(t):
        return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "-"

    try:
        job = httpx.get(f"{a.api}/v1/jobs/{a.job}", headers=h, timeout=10)
        if job.status_code != 200:
            print(f"{job.status_code}: {job.json().get('message', job.text[:200])}")
            return 2
        decs = httpx.get(
            f"{a.api}/v1/decisions", params={"job": a.job, "limit": 500}, headers=h, timeout=10
        ).json()
    except httpx.HTTPError as e:
        print(f"Rubato is not reachable at {a.api}: {e}")
        return 2
    j = job.json()
    print(
        f"{j['id']} ({j['cls']}{', urgent' if j['urgent'] else ''}) state {j['state']}, arrived {hhmm(j['arrival'])}"
        + (f", deadline {hhmm(j['deadline'])}" if j["deadline"] else "")
    )
    for s in j["steps"]:
        nxt = f", reconsider {hhmm(s['next_reconsider'])}" if s["next_reconsider"] else ""
        print(
            f"  step {s['step_id']}: {s['state']}, attempts {s['attempts']}, last reason {s['last_reason'] or '-'}{nxt}"
        )
    for d in decs:
        nxt = f"; reconsider {hhmm(d['next_t'])}" if d["action"] == "defer" and d["next_t"] else ""
        detail = f" {d['detail']}" if d["detail"] else ""
        print(
            f"  {hhmm(d['t'])} {d['step_id']}#{d['attempt']} {d['action'].upper()} [{d['reason']}]{detail}{nxt}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    p = argparse.ArgumentParser(prog="rubato", description="Shared-budget scheduler for AI workloads")
    p.add_argument("--version", action="version", version=f"rubato {__version__}")
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
    s.add_argument("--tariff", type=Path, help="versioned tariff profile")
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("compare", help="build the self-contained HTML comparison")
    s.add_argument("--run", required=True)
    s.add_argument("--report", required=True)
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("explore", help="try a bounded grid of changes from a saved decision")
    s.add_argument("--run", required=True, type=Path)
    s.add_argument("--spec", required=True, type=Path)
    s.add_argument("--out", required=True, type=Path)
    s.set_defaults(fn=cmd_explore)

    s = sub.add_parser("bundle", help="export a portable or aggregated comparison")
    s.add_argument("--run", required=True, type=Path)
    s.add_argument("--out", required=True, type=Path)
    s.set_defaults(fn=cmd_bundle)

    s = sub.add_parser("verify", help="verify a bundle's contents against its manifest")
    s.add_argument("archive", type=Path)
    s.add_argument("--replay", action="store_true", help="reproduce the synthetic results with this engine")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("import-csv", help="convert attempt rows using an explicit map")
    s.add_argument("file", type=Path)
    s.add_argument("--mapping", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.set_defaults(fn=cmd_import_csv)

    s = sub.add_parser("validate", help="validate a trace and show field coverage")
    s.add_argument("file", type=Path)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("doctor", help="check the simulator or the local Compose environment")
    s.add_argument("--env", choices=("entrevista", "habitual"), default="entrevista")
    s.add_argument("--root", type=Path, default=Path.cwd())
    s.add_argument("--offline", action="store_true")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("digest", help="incidents and policy revisions during a time window")
    s.add_argument("--since", default="24h")
    s.add_argument("--api", default="http://127.0.0.1:8000")
    s.add_argument("--token")
    s.set_defaults(fn=cmd_digest)

    s = sub.add_parser("risk-evaluate", help="calibrate deadline alerts on one period and score on another")
    s.add_argument("--design", required=True, type=Path)
    s.add_argument("--test", required=True, type=Path)
    s.add_argument("--policy", default="radar")
    s.set_defaults(fn=cmd_risk_evaluate)

    s = sub.add_parser(
        "replay-incident", help="replay the live rounds in an exported incident without a server"
    )
    s.add_argument("capture", type=Path)
    s.add_argument("--job", help="filter the timeline by the fully qualified job id")
    s.add_argument("--policy", type=Path, help="JSON policy configuration to compare on each observed round")
    s.set_defaults(fn=cmd_replay_incident)

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

    s = sub.add_parser("status", help="summary of a running service: modes, quota, waiting, alerts")
    s.add_argument("--api", default="http://127.0.0.1:8000")
    s.add_argument("--token", help="admin token (default: RUBATO_ADMIN_TOKEN or the local Compose token)")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("why", help="explain a live job of the service (steps, attempts, decisions)")
    s.add_argument("job", help="job id as the client registered it, prefixed with the client: radar:<id>")
    s.add_argument("--api", default="http://127.0.0.1:8000")
    s.add_argument("--token", help="admin token (default: RUBATO_ADMIN_TOKEN or the local Compose token)")
    s.set_defaults(fn=cmd_why)

    s = sub.add_parser("shadow-report", help="agreement, divergences and added latency of a shadow phase")
    s.add_argument("file", help="JSONL written by an adapter in shadow mode")
    s.add_argument("--json", action="store_true")
    s.add_argument("--template", type=Path, help="override the illustrative shadow trial thresholds")
    s.set_defaults(fn=cmd_shadow_report)

    s = sub.add_parser("init-demo", help="copy the synthetic example files into DIR/examples")
    s.add_argument("dir", nargs="?", default=".")
    s.set_defaults(fn=cmd_init_demo)

    s = sub.add_parser("demo-service", help="two clients share one quota on a running service (synthetic)")
    s.add_argument("--api", default="http://127.0.0.1:8000")
    s.add_argument("--provider", default="http://127.0.0.1:8100")
    s.add_argument("--admin-token", default="admin-local-token")
    s.add_argument("--radar-token", default="radar-local-token")
    s.add_argument("--citalab-token", default="citalab-local-token")
    s.set_defaults(fn=cmd_demo_service)
    return p


def cmd_init_demo(a: argparse.Namespace) -> int:
    """Copy the packaged synthetic example (trace, scenarios, frozen policies) into DIR/examples."""
    from importlib import resources as ires

    dest = Path(a.dir) / "examples"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("demo.jsonl", "scenarios.json", "policies.json"):
        (dest / name).write_bytes(ires.files("rubato.data").joinpath(name).read_bytes())
    examples = dest / "entrevista"
    examples.mkdir(exist_ok=True)
    for name in ("reservas.json", "intentos.csv", "mapa.json", "tarifa.json"):
        (examples / name).write_bytes(ires.files("rubato.data").joinpath("workbench", name).read_bytes())
    print(f"wrote {dest} (synthetic demo trace, scenarios and frozen policies)")
    return 0


def cmd_demo_service(a: argparse.Namespace) -> int:
    import time

    from .demo_service import run_demo

    tokens = {"admin": a.admin_token, "radar": a.radar_token, "citalab": a.citalab_token}
    print(json.dumps(run_demo(a.api, a.provider, tokens, f"demo{int(time.time())}"), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
