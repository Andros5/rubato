"""Windows dashboard that runs independently of Docker."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .local import LocalStack
from .report import build_report
from .runs import read_run, run_scenario

SCENARIOS = {
    "Candidatura tardía": "urgencia-tardia",
    "Jornada sin urgencia": "sin-urgencia",
    "Cuota reducida": "cuota-reducida",
}


def open_workday(root: Path, scenario: str) -> Path:
    data = Path(__file__).resolve().parent / "data"
    output = root / "runs" / "entrevista" / "jornadas" / scenario
    run_scenario(
        data / "demo.jsonl",
        scenario,
        data / "scenarios.json",
        data / "policies.json",
        ["radar", "fifo", "reserva"],
        output,
    )
    return build_report(read_run(output), output / "informe.html")


def main(root: Path) -> None:
    window = tk.Tk()
    window.title("Rubato")
    window.minsize(500, 330)
    body = ttk.Frame(window, padding=18)
    body.pack(fill="both", expand=True)
    environment = tk.StringVar(value="Entrevista")
    scenario = tk.StringVar(value=next(iter(SCENARIOS)))
    message = tk.StringVar(value="Comprobando Docker…")
    services = {
        name: tk.StringVar(value="Por comprobar")
        for name in ("Docker", "API", "PostgreSQL", "Planificador", "Proveedor simulado")
    }
    replies: queue.Queue = queue.Queue()
    busy = False
    status_check = False

    def stack() -> LocalStack:
        return LocalStack(root, demo=environment.get() == "Entrevista")

    def submit(label, target, success, *, background=False):
        nonlocal busy, status_check
        if busy or (background and status_check):
            return
        if not background:
            busy = True
            message.set(label)
            for button in actions:
                button.state(["disabled"])
            selector.state(["disabled"])
        else:
            status_check = True

        def work():
            try:
                replies.put((success, target(), None, background))
            except Exception as exc:
                replies.put((success, None, str(exc), background))

        threading.Thread(target=work, daemon=True).start()

    def status_done(result):
        for name, value in result[0].items():
            services[name].set(value)
        message.set("Comprobado a las " + datetime.now().astimezone().strftime("%H:%M:%S %Z"))

    def refresh():
        selected = stack()
        submit(
            "",
            lambda: selected.resolve().status(),
            lambda result: status_done(result) if stack().demo == selected.demo else refresh(),
            background=True,
        )

    def drain():
        nonlocal busy, status_check
        while not replies.empty():
            done, value, error, background = replies.get_nowait()
            if background:
                status_check = False
                if busy:
                    continue
            else:
                busy = False
                for button in actions:
                    button.state(["!disabled"])
                if not stack().demo:
                    live.state(["disabled"])
                selector.state(["!disabled"])
            if error:
                message.set(error)
                if background:
                    for service in services.values():
                        service.set("Sin comprobar")
                continue
            done(value)
        window.after(100, drain)

    def toggle(start):
        selected = stack()
        if (
            not start
            and not selected.demo
            and not messagebox.askokcancel(
                "Detener Rubato habitual",
                "Radar perderá la conexión con Rubato habitual hasta el siguiente inicio. "
                "Se conservarán los volúmenes. ¿Detener el servicio?",
                parent=window,
            )
        ):
            return

        def change():
            resolved = selected.resolve()
            resolved.change(start)
            return resolved.status()

        submit(("Iniciando " if start else "Deteniendo ") + environment.get() + "…", change, status_done)

    def open_panel():
        selected = stack()

        def url():
            status, ports = selected.resolve().status()
            if status["API"] != "Listo":
                raise RuntimeError("La API todavía no está lista. Inicia el entorno seleccionado.")
            return f"http://127.0.0.1:{ports['api']}/panel"

        def opened(address):
            webbrowser.open(address)
            message.set("Panel abierto · " + environment.get())

        submit("Abriendo panel…", url, opened)

    def show_logs(logs):
        popup = tk.Toplevel(window)
        popup.title("Registros · " + environment.get())
        view = tk.Text(popup, wrap="word", width=92, height=26)
        view.pack(fill="both", expand=True)
        view.insert("1.0", logs or "No hay registros.")
        view.configure(state="disabled")
        message.set("Registros consultados")

    def read_logs():
        selected = stack()

        def get():
            resolved = selected.resolve()
            config, _ = resolved.config()
            resolved.containers(verify=True)
            logs = resolved.compose("logs", "--no-color", "--tail", "100")
            for entry in config["api"]["environment"].get("RUBATO_CLIENTS", "").split(","):
                fields = entry.split(":")
                if len(fields) >= 2 and fields[1]:
                    logs = logs.replace(fields[1], "[oculto]")
            return logs

        submit("Consultando registros…", get, show_logs)

    def explore():
        selected = SCENARIOS[scenario.get()]

        def opened(path):
            webbrowser.open(path.resolve().as_uri())
            message.set("Jornada preparada · " + scenario.get())

        submit("Calculando la jornada…", lambda: open_workday(root, selected), opened)

    def probe_changes():
        from .explore import explore as run

        name = filedialog.askopenfilename(
            parent=window, title="Abre una especificación de cambios", filetypes=[("Cambios JSON", "*.json")]
        )
        if not name:
            return
        selected = SCENARIOS[scenario.get()]
        base = root / "runs" / "entrevista" / "jornadas" / selected
        destination = base / "alternativas"

        def rerun():
            if not (base / "manifest.json").exists():
                open_workday(root, selected)
            run(base, json.loads(Path(name).read_text(encoding="utf-8-sig")), destination)
            return destination / "informe.html"

        def opened(path):
            webbrowser.open(path.resolve().as_uri())
            message.set("Alternativas calculadas · " + scenario.get())

        submit("Calculando alternativas…", rerun, opened)

    def docker_desktop():
        try:
            subprocess.Popen(
                ["docker", "desktop", "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            message.set("Abre Docker Desktop desde el menú Inicio e inténtalo de nuevo.")
        else:
            message.set("Se ha solicitado el inicio de Docker Desktop.")

    def connect():
        popup = tk.Toplevel(window)
        popup.title("Conectar una aplicación")
        ttk.Label(popup, text="Elige qué datos tienes para preparar la primera simulación.").pack(
            padx=14, pady=10
        )

        def trace():
            filename = filedialog.askopenfilename(parent=popup, filetypes=[("Traza Rubato", "*.jsonl")])
            if filename:
                from .trace import load

                try:
                    loaded = load(filename)
                    messagebox.showinfo(
                        "Traza válida", f"{len(loaded.jobs)} trabajos · {loaded.fingerprint()}", parent=popup
                    )
                except (ValueError, OSError) as exc:
                    messagebox.showerror("Revisa la traza", str(exc), parent=popup)

        def guide():
            webbrowser.open((root / "docs" / "integraciones.md").resolve().as_uri())

        def csv():
            from .importers.csv import import_csv

            source = filedialog.askopenfilename(parent=popup, filetypes=[("CSV", "*.csv")])
            if not source:
                return
            mapping = filedialog.askopenfilename(
                parent=popup,
                initialdir=root / "examples" / "entrevista",
                title="Selecciona el mapa de columnas",
                filetypes=[("Mapa JSON", "*.json")],
            )
            if not mapping:
                return
            try:
                trace_data, summary = import_csv(
                    Path(source), json.loads(Path(mapping).read_text(encoding="utf-8-sig"))
                )
            except (OSError, ValueError, KeyError) as exc:
                messagebox.showerror("Revisa el CSV y su mapa", str(exc), parent=popup)
                return
            preview = tk.Toplevel(popup)
            preview.title("Vista previa de importación")
            view = tk.Text(preview, width=72, height=22)
            view.pack(fill="both", expand=True)
            view.insert("1.0", json.dumps(summary, indent=2, ensure_ascii=False))
            view.configure(state="disabled")

            def save():
                from .trace import dump

                name = filedialog.asksaveasfilename(parent=preview, defaultextension=".jsonl")
                if name:
                    dump(trace_data, name)
                    preview.destroy()

            ttk.Button(preview, text="Guardar traza validada", command=save).pack(pady=8)

        ttk.Button(popup, text="Tengo un CSV", command=csv).pack(fill="x", padx=14, pady=4)
        ttk.Button(popup, text="Tengo una traza Rubato", command=trace).pack(fill="x", padx=14, pady=4)
        ttk.Button(popup, text="Uso Python", command=guide).pack(fill="x", padx=14, pady=(4, 12))

    drill_output = root / "runs" / "entrevista" / "ensayo.json"

    def drill():
        from .drill import run_drill

        if not stack().demo:
            message.set("Selecciona Entrevista para ejecutar el ensayo en su propio proyecto.")
            return
        submit(
            "Ejecutando el ensayo en el proyecto desechable…",
            lambda: run_drill(root, drill_output),
            lambda result: show_logs(json.dumps(result, ensure_ascii=False, indent=2)),
        )

    def archive():
        try:
            evidence = drill_output.read_text(encoding="utf-8")
        except FileNotFoundError:
            message.set("Todavía no hay un ensayo archivado.")
            return
        show_logs("Ejecución archivada\n\n" + evidence)

    ttk.Label(body, text="Rubato", font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w")
    selector = ttk.Combobox(
        body, textvariable=environment, state="readonly", values=("Entrevista", "Rubato habitual")
    )
    selector.grid(row=0, column=1, sticky="e")

    def choose_environment(_):
        live.state(["!disabled"] if stack().demo else ["disabled"])
        refresh()

    selector.bind("<<ComboboxSelected>>", choose_environment)
    for row, (name, value) in enumerate(services.items(), 1):
        ttk.Label(body, text=name).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Label(body, textvariable=value).grid(row=row, column=1, sticky="w", padx=(12, 0))
    bar = ttk.Frame(body)
    bar.grid(row=6, columnspan=2, sticky="ew", pady=(12, 10))
    actions = []
    for title, callback in (
        ("Iniciar", lambda: toggle(True)),
        ("Detener", lambda: toggle(False)),
        ("Abrir panel", open_panel),
        ("Ver registros", read_logs),
        ("Docker Desktop", docker_desktop),
    ):
        button = ttk.Button(bar, text=title, command=callback)
        button.pack(side="left", padx=(0, 4))
        actions.append(button)
    choices = ttk.Frame(body)
    choices.grid(row=7, columnspan=2, sticky="w", pady=(6, 10))
    ttk.Combobox(choices, textvariable=scenario, values=list(SCENARIOS), state="readonly", width=23).pack(
        side="left"
    )
    launch = ttk.Button(choices, text="Explorar una jornada", command=explore)
    launch.pack(side="left", padx=6)
    actions.append(launch)
    ttk.Button(choices, text="Conectar una aplicación", command=connect).pack(side="left")
    trials = ttk.Frame(body)
    trials.grid(row=8, columnspan=2, sticky="w", pady=(0, 10))
    live = ttk.Button(trials, text="Ejecutar ensayo", command=drill)
    live.pack(side="left", padx=(0, 6))
    actions.append(live)
    ttk.Button(trials, text="Ver último ensayo", command=archive).pack(side="left")
    change = ttk.Button(trials, text="Abrir cambios.json", command=probe_changes)
    change.pack(side="left", padx=(6, 0))
    actions.append(change)
    ttk.Label(body, textvariable=message, wraplength=480).grid(row=9, columnspan=2, sticky="w")
    body.columnconfigure(1, weight=1)
    window.protocol("WM_DELETE_WINDOW", lambda: window.destroy() if not busy else None)

    def heartbeat():
        refresh()
        window.after(5000, heartbeat)

    window.after(0, heartbeat)
    window.after(100, drain)
    window.mainloop()
