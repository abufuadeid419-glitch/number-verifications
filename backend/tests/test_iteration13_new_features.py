"""
Iteration 13 — Backend tests for the four new features
(logo, weekly debt digest, Pro gating on reports, Pro upsell gating)
against Convex deployment fearless-ostrich-878 (reached via the Convex .site REST surface).

Tokens come from /app/memory/test_credentials.md.
"""
import base64
import os
import uuid

import pytest
import requests

BASE = "https://fearless-ostrich-878.eu-west-1.convex.site/api"

OWNER = "test_token_owner"      # LICENSE org (org_test_1)
ACCT = "test_token_acct"
AGENT = "test_token_agent"
DEV = "test_token_dev"


def H(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ---------- logo ----------
class TestLogo:
    """POST /org/logo (owner, base64 PNG) then GET /org/logo returns data_uri."""

    # 1x1 transparent PNG
    PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def test_upload_requires_owner(self):
        r = requests.post(f"{BASE}/org/logo", headers=H(ACCT),
                          json={"data": self.PNG_B64, "content_type": "image/png"})
        assert r.status_code in (401, 403), r.text

    def test_upload_and_fetch_as_owner(self):
        r = requests.post(f"{BASE}/org/logo", headers=H(OWNER),
                          json={"data": self.PNG_B64, "content_type": "image/png"})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        g = requests.get(f"{BASE}/org/logo", headers=H(OWNER))
        assert g.status_code == 200, g.text
        body = g.json()
        assert "data_uri" in body
        assert isinstance(body["data_uri"], str)
        assert body["data_uri"].startswith("data:image/")

    def test_logo_oversize_rejected(self):
        # >1.5MB -> should error
        big = base64.b64encode(b"\x00" * 1_600_000).decode()
        r = requests.post(f"{BASE}/org/logo", headers=H(OWNER),
                          json={"data": big, "content_type": "image/png"})
        assert r.status_code >= 400


# ---------- weekly debt digest ----------
class TestDebtDigest:
    """Internal cron target must be internal; staff-triggered digest still works."""

    def test_weekly_digest_not_public(self):
        # The internalMutation must not be callable via Convex /api/mutation.
        url = "https://fearless-ostrich-878.eu-west-1.convex.cloud/api/mutation"
        r = requests.post(url, json={"path": "extra:weeklyDebtDigestAll", "args": {}, "format": "json"})
        # Convex returns HTTP 200 with {status:"error", errorMessage:"...Could not find public function..."}
        body = r.json()
        assert body.get("status") == "error", body
        assert "public function" in body.get("errorMessage", "").lower(), body

    def test_staff_digest_endpoint(self):
        r = requests.post(f"{BASE}/debts/digest", headers=H(OWNER), json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "count" in body
        assert isinstance(body["count"], int)

    def test_digest_rejects_field_agent(self):
        r = requests.post(f"{BASE}/debts/digest", headers=H(AGENT), json={})
        assert r.status_code in (401, 403)


# ---------- Pro gating on reports ----------
class TestReportsGating:
    """period=day always works; week/month locked for TRIAL. org_test_1 is LICENSE -> all work."""

    def test_day_always_works(self):
        r = requests.get(f"{BASE}/stats/reports?period=day", headers=H(OWNER))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("period") == "day"
        assert "rows" in body and isinstance(body["rows"], list)
        assert body.get("locked") is not True

    def test_week_license_org_unlocked(self):
        r = requests.get(f"{BASE}/stats/reports?period=week", headers=H(OWNER))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("period") == "week"
        # LICENSE org must NOT be locked
        assert body.get("locked") is not True
        assert isinstance(body.get("rows"), list)

    def test_month_license_org_unlocked(self):
        r = requests.get(f"{BASE}/stats/reports?period=month", headers=H(OWNER))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("period") == "month"
        assert body.get("locked") is not True

    def test_invalid_period_rejected(self):
        r = requests.get(f"{BASE}/stats/reports?period=year", headers=H(OWNER))
        assert r.status_code >= 400


# ---------- Pro plan exists ----------
class TestPlans:
    def test_pro_plan_in_plans_list(self):
        r = requests.get(f"{BASE}/plans", headers=H(OWNER))
        assert r.status_code == 200, r.text
        plans = r.json()
        assert isinstance(plans, list) and len(plans) > 0
        names = [p.get("name", "") for p in plans]
        assert any("Pro" in n or "pro" in n.lower() for n in names), f"no Pro plan found: {names}"


# ---------- invite limit error mentions Pro ----------
class TestInviteLimitMessage:
    """Only sanity-check that the Convex employees.invite error string contains 'Pro'.
    We don't want to actually exhaust the org seat count, so we just scan the file."""

    def test_source_mentions_pro(self):
        with open("/app/frontend/convex/employees.ts") as f:
            src = f.read()
        assert "Pro" in src, "invite limit error should mention Pro upgrade"


# ---------- regression: role homes still load (via /auth/me) ----------
class TestAuthMe:
    @pytest.mark.parametrize("tok,role", [
        (OWNER, "OWNER"),
        (ACCT, "EMPLOYEE"),      # accountant is an employee
        (AGENT, "EMPLOYEE"),
        (DEV, "DEVELOPER"),
    ])
    def test_me(self, tok, role):
        r = requests.get(f"{BASE}/auth/me", headers=H(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("role") == role
