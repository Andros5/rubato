"""F6: two clients share one quota through the real service (simulated provider, synthetic load)."""

import time

import pytest
from service_helpers import API, PROVIDER, TOKENS, needs_service

from rubato.demo_service import QUOTA, RESERVE, run_demo

pytestmark = [pytest.mark.postgres, needs_service]


def test_urgent_application_gets_reserved_steps_and_the_lot_delay_is_visible():
    r = run_demo(API, PROVIDER, TOKENS, f"t{int(time.time() * 1000)}")
    res, ref = r["reserva"], r["radar"]
    # reserve policy: the urgent CV + letter run, the background gave up exactly the reserve
    assert res["urgent_done"] is True
    assert sum(res["background_requests_done"].values()) == QUOTA - RESERVE
    assert res["deferrals_by_reason"].get("deadline_reserve", 0) >= 1
    # reference policy: the background used everything, the urgent job finds no quota
    assert ref["urgent_done"] is False
    assert sum(ref["background_requests_done"].values()) == QUOTA
    assert ref["urgent"][0]["reason"] == "capacity_exhausted"
    # the delay of the CitaLab lot is reported, not hidden
    assert r["lot_delay"]["background_requests_given_up_for_the_urgent_job"] == RESERVE
    assert (
        r["lot_delay"]["citalab_episodes_pending_with_reserve"]
        >= r["lot_delay"]["citalab_episodes_pending_with_reference"]
    )
    # back to off: later work goes through the usual path without touching Rubato
    assert r["off"] == {"mode": "off", "ran": 3, "provider_calls": 3, "new_rubato_decisions": 0}
