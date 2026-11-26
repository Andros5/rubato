"""Local Compose targets shared by the Windows launcher and integration tools."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

SERVICES = {"db": 5432, "api": 8000, "api2": 8000, "provider": 8100}
# The simulated provider is in the Compose "demo" profile: only the separate interview and
# trial projects start it, never the everyday stack.
MAIN_SERVICES = {s: p for s, p in SERVICES.items() if s != "provider"}
DEMO_PORTS = {"db": 56432, "api": 18700, "api2": 18701, "provider": 18710}
DRILL_PORTS = {"db": 56532, "api": 18800, "api2": 18801, "provider": 18810}


class LocalError(RuntimeError):
    pass


def probe(port: int, path: str) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            return {}
        result = json.loads(response.read(65536))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, http.client.HTTPException):
        return {}
    finally:
        connection.close()


@dataclass(frozen=True)
class LocalStack:
    root: Path
    demo: bool = True
    context: str | None = None
    trial: bool = False

    @property
    def project(self) -> str:
        if self.trial:
            return "rubato-entrevista-ensayo"
        return "rubato-entrevista" if self.demo else "rubato"

    @property
    def services(self) -> dict[str, int]:
        return SERVICES if self.demo or self.trial else MAIN_SERVICES

    def _docker(self, args: list[str], *, context=True, timeout=20) -> str:
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("COMPOSE_")
            and k not in {"DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
            and (not self.demo or not k.startswith("RUBATO_"))
        }
        if self.trial:
            environment.update(
                {f"RUBATO_{service.upper()}_PORT": str(port) for service, port in DRILL_PORTS.items()}
            )
            environment["RUBATO_ENVIRONMENT"] = "entrevista-ensayo"
        prefix = ["docker", "--context", self.context] if context and self.context else ["docker"]
        try:
            p = subprocess.run(
                [*prefix, *args],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise LocalError("Docker no está instalado o no está en PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalError("Docker tardó demasiado en responder.") from exc
        if p.returncode:
            # Compose diagnostics can contain interpolated settings.
            raise LocalError(
                f"Docker no pudo completar la operación ({p.returncode}). Revisa Docker Desktop."
            )
        return p.stdout

    def resolve(self) -> LocalStack:
        if not self.context:
            context = self._docker(["context", "show"], context=False).strip()
            return LocalStack(self.root, self.demo, context, self.trial).resolve()
        endpoint = self._docker(
            ["context", "inspect", self.context, "--format", "{{.Endpoints.docker.Host}}"]
        )
        if not endpoint.strip().startswith(("npipe:////./pipe/", "unix:///")):
            raise LocalError(f"El contexto Docker {self.context} debe usar el motor local.")
        try:
            self._docker(["info", "--format", "{{.ServerVersion}}"])
        except LocalError as exc:
            raise LocalError("Motor Docker apagado o inaccesible. Inicia Docker Desktop.") from exc
        return self

    def compose(self, *args: str, timeout=20) -> str:
        options = [
            "compose",
            "--project-directory",
            str(self.root),
            "-f",
            str(self.root / "compose.yaml"),
            "-p",
            self.project,
            "--profile",
            "ha",
        ]
        if self.demo or self.trial:
            options.extend(("--profile", "demo"))
        if self.demo:
            directory = self.root / "config"
            local = directory / "demo-compose.vars"
            env_file = local if local.is_file() else directory / "entrevista-compose.example.vars"
            options.extend(("--env-file", str(env_file)))
        return self._docker([*options, *args], timeout=timeout)

    def config(self) -> tuple[dict, dict[str, int]]:
        config = json.loads(self.compose("config", "--format", "json"))
        services = config["services"]
        if config["name"] != self.project or set(services) != {*self.services, "scheduler"}:
            raise LocalError("El archivo Compose no coincide con el proyecto seleccionado.")
        ports = {}
        for service, internal in self.services.items():
            bindings = services[service].get("ports", [])
            if (
                len(bindings) != 1
                or bindings[0]["host_ip"] != "127.0.0.1"
                or bindings[0]["target"] != internal
            ):
                raise LocalError(f"Enlace local inesperado para {service}.")
            ports[service] = int(bindings[0]["published"])
        if len(set(ports.values())) != len(ports):
            raise LocalError("El proyecto tiene puertos repetidos.")
        if self.demo and (
            ports != (DRILL_PORTS if self.trial else DEMO_PORTS)
            or any(services[s]["image"] != "rubato:demo" for s in services if s != "db")
        ):
            raise LocalError("La imagen o los puertos de la demo no coinciden con su configuración.")
        if any(
            config["volumes"][v]["name"] != f"{self.project}_{v}"
            for v in ("dbdata", "providerdata")
            if v in config["volumes"]
        ):
            raise LocalError("Los volúmenes no pertenecen al proyecto seleccionado.")
        return services, ports

    def containers(self, *, verify=False) -> dict[str, dict]:
        raw = self.compose("ps", "--all", "--format", "json").strip()
        rows = json.loads(raw) if raw.startswith("[") else [json.loads(s) for s in raw.splitlines()]
        if any(r["Project"] != self.project or r["Service"] not in {*SERVICES, "scheduler"} for r in rows):
            raise LocalError("Compose devolvió contenedores ajenos al proyecto.")
        if len({r["Service"] for r in rows}) != len(rows):
            raise LocalError("El proyecto tiene varias instancias del mismo servicio.")
        if verify and rows:
            expected_file = (self.root / "compose.yaml").resolve()
            inspected = json.loads(self._docker(["inspect", *(r["ID"] for r in rows)]))
            for item in inspected:
                labels = item["Config"]["Labels"]
                service = labels.get("com.docker.compose.service")
                if (
                    labels.get("com.docker.compose.project") != self.project
                    or Path(labels.get("com.docker.compose.project.config_files", "")).resolve()
                    != expected_file
                ):
                    raise LocalError("El contenedor pertenece a otra configuración.")
                if self.demo and service != "db" and item["Config"]["Image"] != "rubato:demo":
                    raise LocalError(f"Imagen inesperada en {service}.")
                volume = {"db": "dbdata", "provider": "providerdata"}.get(service)
                if volume and not any(
                    mount.get("Name") == f"{self.project}_{volume}" for mount in item["Mounts"]
                ):
                    raise LocalError(f"Volumen inesperado en {service}.")
        return {r["Service"]: r for r in rows}

    def check_ports(self, ports: dict[str, int], rows: dict[str, dict]) -> None:
        for service, port in ports.items():
            bindings = (rows.get(service) or {}).get("Publishers") or []
            if any(b["PublishedPort"] == port and b["URL"] == "127.0.0.1" for b in bindings):
                continue
            with socket.socket() as check:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    check.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise LocalError(f"El puerto local {port} de {service} está ocupado.") from exc

    def verify_bindings(self, ports: dict[str, int], rows: dict[str, dict]) -> None:
        for service, port in ports.items():
            row = rows.get(service)
            if (
                row
                and row["State"] == "running"
                and not any(
                    b["PublishedPort"] == port
                    and b["URL"] == "127.0.0.1"
                    and b["TargetPort"] == SERVICES[service]
                    for b in row.get("Publishers") or []
                )
            ):
                raise LocalError(f"{service} no publica el puerto local {port} configurado.")

    def status(self) -> tuple[dict[str, str], dict[str, int]]:
        _, ports = self.config()
        rows = self.containers()
        self.verify_bindings(ports, rows)
        status = {"Docker": "Disponible"}
        readiness = probe(ports["api"], "/readyz") if rows.get("api", {}).get("State") == "running" else {}
        provider = (
            probe(ports["provider"], "/healthz") if rows.get("provider", {}).get("State") == "running" else {}
        )
        marker = readiness.get("scheduler") or {}
        for service, title, ok in (
            ("api", "API", readiness.get("ok")),
            ("db", "PostgreSQL", rows.get("db", {}).get("Health") == "healthy"),
            ("scheduler", "Planificador", marker.get("recent")),
            ("provider", "Proveedor simulado", provider.get("ok")),
        ):
            if service == "provider" and service not in ports:
                status[title] = "Fuera de la pila habitual"
                continue
            state = rows.get(service, {}).get("State")
            status[title] = (
                "Listo"
                if ok and state == "running"
                else "Sin iniciar"
                if not state
                else (
                    "Planificador sin actividad reciente"
                    if service == "scheduler" and state == "running"
                    else "En marcha · sonda pendiente"
                    if state == "running"
                    else "Detenido"
                )
            )
        if marker.get("completed_at") and rows.get("scheduler", {}).get("State") == "running":
            from datetime import datetime

            status["Planificador"] += " · " + datetime.fromtimestamp(
                marker["completed_at"]
            ).astimezone().strftime("%H:%M:%S %Z")
        return status, ports

    def change(self, start: bool) -> None:
        _, ports = self.config()
        rows = self.containers(verify=True)
        self.verify_bindings(ports, rows)
        if start:
            names = ["db", "api", "scheduler", *(["provider"] if "provider" in ports else [])]
            if "api2" in rows or self.trial:
                names.append("api2")
            self.check_ports({s: p for s, p in ports.items() if s in names}, rows)
            # Reuse the existing image on subsequent starts. Build only the demo on a first installation.
            if self.demo and "api" not in rows:
                self.compose("build", "api", timeout=600)
            if not self.demo and all(s in rows for s in names):
                self.compose("start", "--wait", "--wait-timeout", "90", *names, timeout=120)
            else:
                self.compose("up", "-d", "--wait", "--wait-timeout", "90", *names, timeout=120)
        else:
            self.compose("stop", timeout=60)
            if any(r["State"] == "running" for r in self.containers().values()):
                raise LocalError("Quedan contenedores en marcha.")
