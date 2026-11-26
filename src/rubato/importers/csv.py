"""Convert attempt rows into a trace with explicit field provenance."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..model import Job, Outcome, Status, Step, find_cycle
from ..resources import ResourceSpec
from ..trace import SCHEMA, Trace, dumps, loads

REQUIRED = {"client", "job", "step", "attempt"}
TIMES = ("arrival", "start", "end", "deadline")
USAGE = ("requests", "tokens_in", "tokens_out")
OPTIONAL = {*TIMES, *USAGE, "status", "model", "depends_on", "class"}


def _time(value, zone):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        choices = set()
        for fold in (0, 1):
            aware = dt.replace(tzinfo=zone, fold=fold)
            if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == dt:
                choices.add(aware.timestamp())
        if len(choices) != 1:
            raise ValueError(
                "hora ambigua o inexistente; indica el desplazamiento, por ejemplo 2026-10-25T02:30:00+02:00"
            )
        return int(choices.pop())
    return int(dt.timestamp())


def import_csv(path: Path, mapping: dict) -> tuple[Trace, dict]:
    fields = mapping["columns"]
    if not REQUIRED <= fields.keys() or fields.keys() - REQUIRED - OPTIONAL:
        raise ValueError("el mapa necesita client, job, step y attempt y solo admite campos documentados")
    if not all(isinstance(s, str) and s for s in fields.values()) or len(set(fields.values())) != len(fields):
        raise ValueError("cada campo necesita una columna distinta")
    delimiter = mapping.get("delimiter", ",")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("el delimitador debe ser coma, punto y coma o tabulación")
    try:
        zone = ZoneInfo(mapping["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"zona IANA desconocida: {mapping['timezone']}") from exc
    coverage = Counter()
    duplicate = 0
    attempts = {}
    groups = defaultdict(list)
    preview = []
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=delimiter, strict=True)
        if (
            not reader.fieldnames
            or set(fields.values()) - set(reader.fieldnames)
            or len(set(reader.fieldnames)) != len(reader.fieldnames)
        ):
            raise ValueError("fila 1: cabecera incompleta o repetida para el mapa indicado")
        for row in reader:
            n = reader.line_num
            if None in row or None in row.values():
                raise ValueError(f"fila {n}: número de columnas incorrecto")
            raw = {name: row[col].strip() for name, col in fields.items()}
            for key in REQUIRED:
                if not raw[key] or len(raw[key]) > 160 or any(ord(c) < 32 for c in raw[key]):
                    raise ValueError(
                        f"fila {n}, {key}: identificador vacío, demasiado largo o con caracteres de control"
                    )
            identity = (raw["client"], raw["attempt"])
            if identity in attempts:
                if attempts[identity] != raw:
                    raise ValueError(f"fila {n}, attempt: identificador de intento contradictorio")
                duplicate += 1
                continue
            attempts[identity] = raw
            coverage.update(name for name, value in raw.items() if value)
            parsed = dict(raw)
            for key in TIMES:
                try:
                    parsed[key] = _time(raw.get(key), zone)
                except (ValueError, OverflowError, OSError) as exc:
                    raise ValueError(f"fila {n}, {key}: {exc}") from exc
            if parsed["start"] is not None and parsed["end"] is not None and parsed["end"] < parsed["start"]:
                raise ValueError(f"fila {n}, end: anterior a start")
            for key in USAGE:
                value = raw.get(key)
                if value:
                    if (
                        not value.isascii()
                        or not value.isdecimal()
                        or len(value) > 10
                        or int(value) > 1_000_000_000
                    ):
                        raise ValueError(f"fila {n}, {key}: entero fuera de rango (0–1000000000)")
                    parsed[key] = int(value)
                else:
                    parsed[key] = None
            try:
                parsed["status"] = Status(raw["status"]) if raw.get("status") else None
            except ValueError as exc:
                raise ValueError(f"fila {n}, status: estado Rubato desconocido") from exc
            parsed["depends_on"] = tuple(d.strip() for d in raw.get("depends_on", "").split("|") if d.strip())
            if len(preview) < 5:
                preview.append(raw)
            groups[(raw["client"], raw["job"])].append((n, parsed))
    if not groups:
        raise ValueError("el CSV no contiene intentos")
    jobs = []
    fallback = mapping.get("duration_estimate_s", 1)
    if type(fallback) is not int or not 1 <= fallback <= 86400:
        raise ValueError("duration_estimate_s debe estar entre 1 y 86400")
    for (client, jid), entries in sorted(groups.items()):
        first_row = entries[0][0]
        steps_by_id = defaultdict(list)
        distinct = {}
        for key in ("arrival", "deadline", "class"):
            known = {row[key] for _, row in entries if row.get(key) is not None and row.get(key) != ""}
            if len(known) > 1:
                raise ValueError(f"fila {first_row}, {key}: valores contradictorios para el mismo trabajo")
            distinct[key] = next(iter(known), None)
        arrival = distinct["arrival"]
        origin = "observed"
        if arrival is None:
            starts = [row["start"] for _, row in entries if row["start"] is not None]
            if not mapping.get("arrival_from_start") or not starts:
                raise ValueError(
                    f"fila {first_row}, arrival: indica llegada o permite derivarla de start en el mapa"
                )
            arrival, origin = min(starts), "derived"
        if distinct["deadline"] is not None and distinct["deadline"] < arrival:
            raise ValueError(f"fila {first_row}, deadline: anterior a la llegada")
        for n, row in entries:
            if row["start"] is not None and row["start"] < arrival:
                raise ValueError(f"fila {n}, start: anterior a la llegada")
            steps_by_id[row["step"]].append((n, row))
        steps = []
        provenance = {
            "arrival": origin,
            "deadline": "observed" if distinct["deadline"] is not None else "missing",
        }
        deps = {}
        for sid, batch in sorted(steps_by_id.items()):
            n, first = batch[0]
            deps[sid] = first["depends_on"]
            if any(dep not in steps_by_id for dep in deps[sid]):
                raise ValueError(f"fila {n}, depends_on: paso inexistente")
            if any(
                row.get("model") != first.get("model") or row["depends_on"] != deps[sid] for _, row in batch
            ):
                raise ValueError(f"fila {n}, model/depends_on: valores contradictorios para el mismo paso")
            ordered = sorted(
                batch, key=lambda entry: (entry[1]["start"] is None, entry[1]["start"] or 0, entry[0])
            )
            outcomes = []
            for _, row in ordered:
                if row["start"] is None or row["end"] is None or row["status"] is None:
                    break
                outcomes.append(
                    Outcome(
                        row["end"] - row["start"],
                        {k: row[k] for k in USAGE if row[k] is not None},
                        row["status"],
                    )
                )
            complete = len(outcomes) == len(batch)
            provenance[f"steps.{sid}.attempts"] = "observed" if complete else "missing"
            estimate = {"requests": 1, **mapping.get("estimate", {})}
            steps.append(
                Step(
                    sid,
                    estimate,
                    deps[sid],
                    first.get("model") or None,
                    duration_estimate=fallback,
                    max_attempts=len(batch),
                    outcomes=tuple(outcomes) if complete else (),
                )
            )
        if cycle := find_cycle(deps):
            raise ValueError(f"fila {first_row}, depends_on: ciclo {' -> '.join(cycle)}")
        if ":" in client:
            raise ValueError(f"fila {first_row}, client: el identificador no admite dos puntos")
        jobs.append(
            Job(
                f"{client}:{jid}",
                client,
                distinct["class"] or "import",
                arrival,
                tuple(steps),
                deadline=distinct["deadline"],
                provenance=provenance,
            )
        )
    tr = Trace(
        {
            "schema": SCHEMA,
            "time_unit": "s",
            "created_by": "rubato import-csv",
            "source_timezone": zone.key,
            "assumptions": [
                "CSV: fallback duration and admission estimates come from the mapping; "
                "steps with incomplete attempts use simulated outcomes rather than observed attempts."
            ],
        },
        [ResourceSpec(**s) for s in mapping.get("resources", [])],
        jobs,
    )
    # Use the same validator as every simulation before touching the output.
    tr = loads(dumps(tr))
    return tr, {
        "jobs": len(jobs),
        "rows": len(attempts),
        "duplicates": duplicate,
        "timezone": zone.key,
        "units": {"time": "s", "tokens": "tokens"},
        "coverage": {
            k: {"known": coverage[k], "unknown": len(attempts) - coverage[k]}
            for k in sorted(REQUIRED | OPTIONAL)
        },
        "fingerprint": tr.fingerprint(),
        "preview": preview,
    }


def convert(path: Path, mapping_path: Path, dest: Path) -> dict:
    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    trace, summary = import_csv(path, mapping)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=dest.parent, mode="w", encoding="utf-8", newline="\n", delete=False) as file:
        name = Path(file.name)
        try:
            file.write(dumps(trace))
        except BaseException:
            file.close()
            name.unlink(missing_ok=True)
            raise
    try:
        name.replace(dest)
    finally:
        name.unlink(missing_ok=True)
    return summary
