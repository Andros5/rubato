"""Export a portable synthetic demo or a privacy-preserving aggregate."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from . import __version__
from .report import build_report
from .runs import engine_fingerprint, input_trace, json_file, read_run
from .trace import dumps, loads

SCHEMA = "rubato.bundle/v1"
RESULT_FILES = ("decisions", "jobs", "usage", "attempts", "risk")
METRICS = (
    "received",
    "started",
    "done",
    "done_on_time",
    "pending",
    "failed",
    "wait_p95",
    "oldest_pending_age",
)
SAFE_NAME = re.compile(r"(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_.-]+")


def _bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _aggregate(data: dict) -> dict:
    def block(value):
        return {key: value[key] for key in METRICS if type(value.get(key)) in (int, float, type(None))}

    return {
        "kind": "aggregate",
        "rubato": __version__,
        "runs": [
            {
                "urgent": block(run["summary"]["urgent"]),
                "background": block(run["summary"]["background"]),
                "clients": [
                    {"urgent": block(client["urgent"]), "background": block(client["background"])}
                    for client in run["summary"].get("by_client", {}).values()
                ],
            }
            for run in data["runs"].values()
        ],
    }


def _deterministic_summary(summary):
    result = dict(summary)
    result["planner"] = {k: v for k, v in summary["planner"].items() if not k.startswith("latency_")}
    return result


def bundle(run: Path, archive: Path) -> dict:
    data = read_run(run)
    manifest = data["manifest"]
    canonical = loads(files("rubato.data").joinpath("demo.jsonl").read_text(encoding="utf-8"))
    export = {"resumen.json": _bytes(_aggregate(data))}
    # Only the distributed trace and scenarios can be shared at row level.
    scenarios = json.loads(files("rubato.data").joinpath("scenarios.json").read_text(encoding="utf-8"))[
        "scenarios"
    ]
    scenario = manifest["scenario"]
    duplicated = scenario == {"name": scenario["name"], **scenarios.get(scenario["name"], {})}
    tr = (
        input_trace(run, manifest)
        if manifest["trace"]["fingerprint"] == canonical.fingerprint() and duplicated
        else None
    )
    public = tr is not None and dumps(tr) == dumps(canonical)
    if public:
        safe = dict(manifest)
        safe["trace"] = {**safe["trace"], "path": "inputs.trace.jsonl"}
        safe["policies_file"] = {"frozen_at": safe["policies_file"].get("frozen_at")}
        # Configurations can contain customer-defined identifiers. Ship raw rows
        # only when all configurations use the distributed synthetic customers.
        public = (
            all(
                set(item["config"])
                <= {"name", "version", "reserve", "release_at", "release_offset", "aging_limit", "impossible"}
                and item["config"]["name"] in {"radar", "fifo", "reserva"}
                and set(item["config"].get("reserve", {})) <= {r.id for r in canonical.resources}
                for item in safe["runs"].values()
            )
            and safe.get("tariff") is None
        )
        if public and (x := safe.get("exploration")):
            goals = x["spec"].get("goals", {})
            known_clients = {j.client for j in canonical.jobs}
            public = set(goals.get("clients", {})) <= known_clients and set(x["spec"]) <= {
                "schema",
                "reference",
                "candidate",
                "at_offset_s",
                "vary",
                "goals",
            }
    if public:
        data["manifest"] = safe
        with TemporaryDirectory() as work:
            html = build_report(data, Path(work) / "informe.html").read_bytes()
        export["informe.html"] = html
        export["manifest.json"] = _bytes(safe)
        export["inputs.trace.jsonl"] = dumps(canonical).encode("utf-8")
        for label, info in data["runs"].items():
            summary = _deterministic_summary(info["summary"])
            export[f"{label}/summary.json"] = _bytes(summary)
            for filename in RESULT_FILES:
                export[f"{label}/{filename}.jsonl"] = b"".join(
                    json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
                    for row in info[filename]
                )
        export["LEEME.txt"] = (
            "Abre informe.html desde esta carpeta. Todas las alternativas están incluidas.\n"
            f"Motor: Rubato {safe['rubato']}. El manifiesto contiene la configuración efectiva.\n"
            "Ejecuta rubato verify ARCHIVO --replay para comprobar la integridad y reproducir el resultado.\n"
        ).encode()
    hashes = {
        "schema": SCHEMA,
        "kind": "synthetic" if public else "aggregate",
        "files": {name: hashlib.sha256(blob).hexdigest() for name, blob in sorted(export.items())},
    }
    archive.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=archive.parent) as work:
        temp = Path(work) / "bundle.zip"
        with ZipFile(temp, "w", compression=ZIP_DEFLATED) as z:
            z.writestr("bundle.json", _bytes(hashes))
            for name, blob in sorted(export.items()):
                z.writestr(name, blob)
        temp.replace(archive)
    return hashes


def verify(archive: Path, replay=False) -> dict:
    with ZipFile(archive) as z:
        records = z.infolist()
        if len(records) > 1000 or sum(i.file_size for i in records) > 200_000_000:
            raise ValueError("bundle exceeds verification limits")
        names = [i.filename for i in records]
        if len(set(names)) != len(names) or any(
            not SAFE_NAME.fullmatch(name) or {"..", "."} & set(name.split("/")) for name in names
        ):
            raise ValueError("bundle contains duplicate or invalid paths")
        if "bundle.json" not in names:
            raise ValueError("missing bundle.json")
        manifest = json.loads(z.read("bundle.json"))
        if manifest.get("schema") != SCHEMA or manifest.get("kind") not in {"synthetic", "aggregate"}:
            raise ValueError(f"expected {SCHEMA}")
        hashes = manifest["files"]
        if set(hashes) != set(names) - {"bundle.json"}:
            difference = set(hashes) ^ (set(names) - {"bundle.json"})
            raise ValueError("missing or unexpected bundle files: " + ", ".join(sorted(difference)))
        for name, expected in hashes.items():
            try:
                actual = hashlib.sha256(z.read(name)).hexdigest()
            except BadZipFile as exc:
                raise ValueError(f"integrity check failed: {name}") from exc
            if actual != expected:
                raise ValueError(f"integrity check failed: {name}")
        if replay:
            if manifest["kind"] != "synthetic":
                raise ValueError("an aggregated bundle only supports integrity verification")
            _replay(z)
    return manifest


def _replay(archive: ZipFile) -> None:
    from .explore import explore
    from .runs import run_scenario

    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("engine_fingerprint") != engine_fingerprint():
        raise ValueError("the bundle requires its original Rubato engine")
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        name = manifest["scenario"]["name"]
        scenario = {k: v for k, v in manifest["scenario"].items() if k != "name"}
        trace, scenarios, policies = (
            work / f for f in ("inputs.trace.jsonl", "scenarios.json", "policies.json")
        )
        trace.write_bytes(archive.read("inputs.trace.jsonl"))
        json_file(scenarios, {"version": "rubato.scenarios/v1", "scenarios": {name: scenario}})
        variants = manifest.get("exploration")
        if variants:
            spec = variants["spec"]
            configs = {
                spec["reference"]: variants["reference_config"],
                spec["candidate"]: variants["candidate_config"],
            }
        else:
            configs = {label: manifest["runs"][label]["config"] for label in manifest["order"]}
        json_file(policies, {"version": "rubato.policies/v1", "policies": configs})
        output = work / "base"
        run_scenario(trace, name, scenarios, policies, list(configs), output, grid=False)
        if variants:
            out = work / "alternatives"
            if not variants["complete"]:
                spec = {
                    **spec,
                    "vary": {
                        **spec["vary"],
                        "values": [v["value"] for v in variants["alternatives"].values()],
                    },
                }
                if not spec["vary"]["values"]:
                    spec = None
            if spec is not None:
                explore(output, spec, out)
                output = out
        simulated = read_run(output)
        for label in manifest["order"]:
            res = simulated["runs"][label]
            expected = json.loads(archive.read(f"{label}/summary.json"))
            if _deterministic_summary(res["summary"]) != expected:
                raise ValueError(f"replay differs: {label}/summary.json")
            for filename in RESULT_FILES:
                path = f"{label}/{filename}.jsonl"
                expected_rows = [json.loads(row) for row in archive.read(path).splitlines() if row]
                if res[filename] != expected_rows:
                    raise ValueError(f"replay differs: {path}")
