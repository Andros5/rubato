"""Small Python client for the Rubato service, plus the ``Gate`` used by adapters.

Modes (per client application):
* ``off``      Rubato is not contacted. The application behaves exactly as before.
* ``shadow``   The application keeps deciding by itself. Rubato registers the work,
               says what it *would* decide, and books the real usage reported after
               each call. Any Rubato failure is logged and ignored.
* ``enforce``  A provider call needs a permit. If Rubato is unreachable the step
               stays pending and visible (``RubatoUnavailable``), it is never run blind.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("rubato.sdk")
MODES = ("off", "shadow", "enforce")


class RubatoError(RuntimeError):
    def __init__(self, status: int, body: dict):
        super().__init__(f"{status}: {body.get('error')}: {body.get('message')}")
        self.status, self.body = status, body


class RubatoUnavailable(RuntimeError):
    """Coordination is unavailable in enforce mode: keep the work pending and visible."""


class PermitExpired(RuntimeError):
    pass


class RubatoClient:
    def __init__(
        self, base_url: str, token: str, timeout: float = 10.0, transport: httpx.BaseTransport | None = None
    ):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def _req(self, method: str, path: str, json=None, headers=None) -> dict:
        r = self.http.request(
            method, path, json=json, headers={"X-Correlation-Id": str(uuid.uuid4()), **(headers or {})}
        )
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RubatoError(r.status_code, body if isinstance(body, dict) else {"error": str(body)})
        return body

    # jobs
    def register_job(self, job: dict, key: str | None = None) -> dict:
        try:
            return self._req("POST", "/v1/jobs", job, {"Idempotency-Key": key or f"job:{job['id']}"})
        except RubatoError as e:
            if e.status == 409 and e.body.get("error") == "exists":
                return {"job": e.body.get("job"), "existing": True}
            raise

    def job(self, job_id: str) -> dict:
        return self._req("GET", f"/v1/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict:
        return self._req("POST", f"/v1/jobs/{job_id}/cancel")

    # permits
    def claim(
        self, job: str | None = None, step: str | None = None, slots: int | None = None, shadow: bool = False
    ) -> dict:
        body: dict = {"shadow": shadow}
        if job:
            body["job"] = job
        if step:
            body["step"] = step
        if slots is not None:
            body["slots"] = slots
        return self._req("POST", "/v1/claims", body)

    def start(self, attempt: str) -> dict:
        try:
            return self._req("POST", f"/v1/attempts/{attempt}/start")
        except RubatoError as e:
            if e.status == 409 and e.body.get("error") == "permit_expired":
                raise PermitExpired(attempt) from e
            raise

    def finish(
        self,
        attempt: str,
        status: str = "ok",
        usage: dict | None = None,
        receipt: str | None = None,
        retry_after: int | None = None,
    ) -> dict:
        body = {"status": status, "usage": usage or {}, "receipt": receipt}
        if retry_after is not None:
            body["retry_after"] = retry_after
        return self._req("POST", f"/v1/attempts/{attempt}/finish", body)

    def reconcile(
        self,
        attempt: str,
        consumed: bool,
        usage: dict | None = None,
        receipt: str | None = None,
        status: str = "ok",
    ) -> dict:
        return self._req(
            "POST",
            f"/v1/attempts/{attempt}/reconcile",
            {"consumed": consumed, "usage": usage, "receipt": receipt, "status": status},
        )

    def observe(self, job: str, step: str, status: str, usage: dict, receipt: str | None = None) -> dict:
        return self._req(
            "POST",
            "/v1/observations",
            {"job": job, "step": step, "status": status, "usage": usage, "receipt": receipt},
        )

    def decisions(self, job: str | None = None) -> list[dict]:
        return self._req("GET", "/v1/decisions" + (f"?job={job}" if job else ""))

    def usage(self) -> dict:
        return self._req("GET", "/v1/usage")

    def mode(self) -> str:
        """Mode chosen by the administrator for this client (``off`` | ``shadow`` | ``enforce``)."""
        return self._req("GET", "/v1/mode")["mode"]


@dataclass
class Outcome:
    status: str = "ok"  # ok | invalid | rate_limited | error
    usage: dict = field(default_factory=dict)
    receipt: str | None = None
    retry_after: int | None = None


@dataclass
class GateResult:
    ran: bool
    mode: str
    value: object = None
    attempt: str | None = None
    shadow: dict | None = None
    waited_s: float = 0.0
    reason: str | None = None


class Gate:
    """Wrap one provider call of an application step.

    ``call()`` must perform the provider request and return ``(value, Outcome)``.
    """

    def __init__(
        self,
        client: RubatoClient | None,
        mode: str = "off",
        wait_timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        mode_ttl: float = 30.0,
    ):
        if mode not in (*MODES, "auto"):
            raise ValueError(f"mode must be one of {MODES} or 'auto'")
        self.client, self.wait_timeout, self.sleep = client, wait_timeout, sleep
        self.requested = mode
        self.mode = "off" if mode == "auto" else mode
        self.mode_ttl = mode_ttl
        self._mode_checked = -1e18
        self.shadow_log: list[dict] = []

    def current_mode(self) -> str:
        """``auto`` follows the mode the administrator sets in the panel (cached ``mode_ttl`` s).

        If the service cannot be asked, the last known mode is kept (``off`` before the first
        answer), so an unreachable Rubato never turns a working application into a blocked one.
        """
        if self.requested != "auto" or self.client is None:
            return self.mode
        now = time.monotonic()
        if now - self._mode_checked >= self.mode_ttl:
            self._mode_checked = now
            try:
                m = self.client.mode()
                if m in MODES:
                    self.mode = m
            except Exception as e:
                log.warning("rubato: mode unavailable, keeping %s: %s", self.mode, e)
        return self.mode

    def register(self, job: dict) -> None:
        mode = self.current_mode()
        if mode == "off" or self.client is None:
            return
        try:
            self.client.register_job(job)
        except Exception as e:
            if mode == "enforce":
                raise RubatoUnavailable(str(e)) from e
            log.warning("rubato shadow: register failed: %s", e)

    def run(self, job: str, step: str, call: Callable[[], tuple[object, Outcome]]) -> GateResult:
        mode = self.current_mode()
        if mode == "off" or self.client is None:
            value, _ = call()
            return GateResult(True, "off", value)
        if mode == "shadow":
            return self._shadow(job, step, call)
        return self._enforce(job, step, call)

    def _shadow(self, job, step, call) -> GateResult:
        would = None
        t0 = time.perf_counter()
        try:
            would = self.client.claim(job, step, shadow=True).get("shadow")
        except Exception as e:
            log.warning("rubato shadow: claim failed: %s", e)
        latency = time.perf_counter() - t0
        value, out = call()
        try:
            self.client.observe(job, step, out.status, out.usage, out.receipt)
        except Exception as e:
            log.warning("rubato shadow: observe failed: %s", e)
        rec = {"job": job, "step": step, "would": would, "latency_s": round(latency, 4)}
        self.shadow_log.append(rec)
        return GateResult(True, "shadow", value, shadow=would)

    def _enforce(self, job, step, call) -> GateResult:
        deadline = time.monotonic() + self.wait_timeout
        waited = 0.0
        reason = None
        while True:
            try:
                r = self.client.claim(job, step)
            except Exception as e:
                raise RubatoUnavailable(f"cannot reach Rubato: {e}") from e
            permit = r.get("permit")
            if permit:
                break
            reason = (r.get("step") or {}).get("last_reason")
            if time.monotonic() >= deadline:
                return GateResult(False, "enforce", waited_s=waited, reason=reason or "no permit")
            pause = min(float(r.get("retry_after") or 1), max(0.0, deadline - time.monotonic()))
            self.sleep(pause)
            waited += pause
        aid = permit["attempt"]
        try:
            self.client.start(aid)
        except PermitExpired:
            return GateResult(False, "enforce", attempt=aid, waited_s=waited, reason="permit_expired")
        except Exception as e:
            # we do not know whether the start was recorded: never call the provider blind
            raise RubatoUnavailable(f"start not confirmed: {e}") from e
        try:
            value, out = call()
        except Exception:
            try:  # conservative: the request may have reached the provider
                self.client.finish(aid, "error", {"requests": 1})
            except Exception as settle_error:  # keep the application's error; Rubato marks it unknown later
                log.warning("rubato: could not settle failed attempt %s: %s", aid, settle_error)
            raise
        self.client.finish(aid, out.status, out.usage, out.receipt, out.retry_after)
        return GateResult(True, "enforce", value, attempt=aid, waited_s=waited)
