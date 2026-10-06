"""
Iteration 14 — Backend tests for the 3 new features:
  1) Yearly Pro subscription (GET /plans + POST /upgrade-requests billing=yearly)
  2) Trial expiry countdown — code review (owner org_test_1 expires 2027)
  3) Sales targets (/targets GET+PUT, owner-only mutate, 0 deletes, invalid rejected)
  4) Regression: owner/acct/agent/dev /stats/overview + /notifications

Convex deployment: fearless-ostrich-878. Tokens come from /app/memory/test_credentials.md.
Target 5000 for user_test_agent / 2026-10 is restored at teardown.
Pending upgrade request created during tests is rejected via /dev/upgrade-requests at teardown.
"""
import pytest
import requests

BASE = "https://fearless-ostrich-878.eu-west-1.convex.site/api"

OWNER = "test_token_owner"
ACCT = "test_token_acct"
AGENT = "test_token_agent"
AGENT2 = "test_token_agent2"
DEV = "test_token_dev"


def H(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ---------------- yearly Pro ----------------
class TestYearlyPro:
    """Pro plan has yearly_price; owner can submit a yearly upgrade request."""

    def test_plans_contain_pro_with_yearly_price(self):
        r = requests.get(f"{BASE}/plans", headers=H(OWNER))
        assert r.status_code == 200, r.text
        plans = r.json()
        pro = next((p for p in plans if p.get("name") == "Pro"), None)
        assert pro is not None, f"Pro plan missing: {plans}"
        assert pro["price"] == 25, pro
        assert pro["yearly_price"] == 250, pro
        assert pro["currency"] == "USD"
        assert pro["max_employees"] == 15
        # Save-pct math (frontend savePct): 1 - (yearly / (price * 365/days))
        # = 1 - 250 / (25 * 365/30) = 1 - 250/304.17 ≈ 0.178 -> 18%
        assert pro["days"] == 30
        self.__class__._pro_id = pro["id"]

    def test_upgrade_requests_initial_state(self):
        """Make sure there is no pending request before we create one."""
        r = requests.get(f"{BASE}/upgrade-requests", headers=H(OWNER))
        assert r.status_code == 200, r.text
        pending = [x for x in r.json() if x.get("status") == "PENDING"]
        assert not pending, f"pre-existing pending upgrade requests: {pending}"

    def test_create_yearly_upgrade_request(self):
        """POST /upgrade-requests body {plan_id, billing:'yearly', payment_ref:'TEST_REF'} — stays PENDING."""
        pro_id = getattr(self.__class__, "_pro_id", None)
        assert pro_id, "run test_plans_contain_pro_with_yearly_price first"
        r = requests.post(
            f"{BASE}/upgrade-requests",
            headers=H(OWNER),
            json={"plan_id": pro_id, "billing": "yearly", "payment_ref": "TEST_REF_YEARLY_14"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Response is the created upgrade_request record (contains billing/days/id).
        req_id = body.get("id")
        assert req_id, f"no id in response: {body}"
        assert body.get("billing") == "yearly", body
        assert body.get("days") == 365, body
        assert body.get("price") == 250, body
        assert body.get("payment_ref") == "TEST_REF_YEARLY_14"
        self.__class__._req_id = req_id

        # Verify persistence via list
        lst = requests.get(f"{BASE}/upgrade-requests", headers=H(OWNER)).json()
        mine = next((x for x in lst if x.get("id") == req_id), None)
        assert mine, f"created req not visible: {lst}"
        assert mine["status"] == "PENDING"
        assert mine["payment_ref"] == "TEST_REF_YEARLY_14"
        assert mine.get("days") == 365, mine
        assert mine.get("price") == 250, mine
        assert mine.get("billing") == "yearly", mine

    def test_cannot_create_second_pending_request(self):
        """When one PENDING exists, a second POST must be rejected."""
        pro_id = getattr(self.__class__, "_pro_id", None)
        if not pro_id:
            pytest.skip("Pro plan missing")
        r = requests.post(
            f"{BASE}/upgrade-requests",
            headers=H(OWNER),
            json={"plan_id": pro_id, "billing": "yearly", "payment_ref": "TEST_REF_DUP"},
        )
        assert r.status_code in (400, 403, 409), r.text

    @classmethod
    def teardown_class(cls):
        """Reject the PENDING yearly request as developer so the org isn't left dirty.
        Do NOT approve — the review request forbids approving on real orgs.
        """
        req_id = getattr(cls, "_req_id", None)
        if not req_id:
            return
        try:
            requests.patch(
                f"{BASE}/dev/upgrade-requests/{req_id}",
                headers=H(DEV),
                json={"action": "reject", "note": "TEST cleanup iter14"},
                timeout=15,
            )
        except Exception:
            pass


# ---------------- trial countdown (code review) ----------------
class TestTrialCountdown:
    """owner org_test_1 is LICENSE+expires 2027 → banner must NOT render. Verified via /auth/me."""

    def test_owner_plan_is_license_and_far_future(self):
        r = requests.get(f"{BASE}/auth/me", headers=H(OWNER))
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["role"] == "OWNER"
        org = me["org"]
        assert org["plan"] == "LICENSE", org
        # Banner thresholds: trial<=5 days, license<=7 days.
        # expires_at is 2027-10-ish → more than 7 days from now → banner suppressed.
        assert org["expires_at"].startswith("2027"), org


# ---------------- sales targets ----------------
class TestSalesTargets:
    """GET as owner (all agents) and agent (self only); PUT owner-only; 0 deletes; invalid rejected."""

    @classmethod
    def setup_class(cls):
        cls.agent_id = "user_test_agent"
        cls.agent2_id = "user_test_agent2"

    def test_owner_sees_all_agents(self):
        r = requests.get(f"{BASE}/targets", headers=H(OWNER))
        assert r.status_code == 200, r.text
        d = r.json()
        uids = {a["user_id"] for a in d["agents"]}
        assert self.agent_id in uids and self.agent2_id in uids
        assert d["month"] and d["days_in_month"] and "days_left" in d

    def test_agent_sees_only_self(self):
        r = requests.get(f"{BASE}/targets", headers=H(AGENT))
        assert r.status_code == 200, r.text
        d = r.json()
        uids = [a["user_id"] for a in d["agents"]]
        assert uids == [self.agent_id], uids

    def test_agent_cannot_set_target(self):
        r = requests.put(
            f"{BASE}/targets",
            headers=H(AGENT),
            json={"distributor_id": self.agent_id, "amount": 9999},
        )
        assert r.status_code in (401, 403), r.text

    def test_invalid_distributor_rejected(self):
        r = requests.put(
            f"{BASE}/targets",
            headers=H(OWNER),
            json={"distributor_id": "user_does_not_exist", "amount": 1000},
        )
        assert r.status_code in (400, 404), r.text

    def test_owner_set_update_and_notify_agent(self):
        # set a fresh target for agent2 (currently 0) → expect notification type=target
        r = requests.put(
            f"{BASE}/targets",
            headers=H(OWNER),
            json={"distributor_id": self.agent2_id, "amount": 1234},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("amount") == 1234

        # GET verifies persistence
        d = requests.get(f"{BASE}/targets", headers=H(OWNER)).json()
        a2 = next(x for x in d["agents"] if x["user_id"] == self.agent2_id)
        assert a2["target"] == 1234

        # Notification: agent2's notifications list should include a 'target' type notification
        notifs = requests.get(f"{BASE}/notifications", headers=H(AGENT2)).json()
        items = notifs.get("items") if isinstance(notifs, dict) else notifs
        types = [n.get("type") for n in items]
        assert "target" in types, f"no target notification: {types[:5]}"

    def test_amount_zero_deletes(self):
        # delete agent2's target (set in previous test)
        r = requests.put(
            f"{BASE}/targets",
            headers=H(OWNER),
            json={"distributor_id": self.agent2_id, "amount": 0},
        )
        assert r.status_code == 200, r.text
        d = requests.get(f"{BASE}/targets", headers=H(OWNER)).json()
        a2 = next(x for x in d["agents"] if x["user_id"] == self.agent2_id)
        assert a2["target"] == 0
        assert a2["pct"] is None

    def test_agent1_target_preserved_at_5000(self):
        """Review context: user_test_agent target is 5000 for 2026-10 — must stay 5000 after suite."""
        d = requests.get(f"{BASE}/targets", headers=H(OWNER)).json()
        a = next(x for x in d["agents"] if x["user_id"] == self.agent_id)
        assert a["target"] == 5000, f"agent1 target drifted: {a}"

    @classmethod
    def teardown_class(cls):
        """Belt-and-braces: ensure agent1=5000 and agent2=0 after suite."""
        try:
            requests.put(f"{BASE}/targets", headers=H(OWNER),
                         json={"distributor_id": cls.agent_id, "amount": 5000}, timeout=15)
            requests.put(f"{BASE}/targets", headers=H(OWNER),
                         json={"distributor_id": cls.agent2_id, "amount": 0}, timeout=15)
        except Exception:
            pass


# ---------------- regression ----------------
class TestRegression:
    """Overview + notifications load for every role (sync-status-badge consumes these)."""

    @pytest.mark.parametrize("tok,role", [(OWNER, "OWNER"), (ACCT, "ACCT"), (AGENT, "AGENT"), (DEV, "DEV")])
    def test_notifications_200(self, tok, role):
        r = requests.get(f"{BASE}/notifications", headers=H(tok))
        assert r.status_code == 200, f"{role} notifications: {r.status_code} {r.text}"
        d = r.json()
        # New shape: {items: [...], unread: n}
        assert isinstance(d, dict) and "items" in d and "unread" in d, d
        assert isinstance(d["items"], list)

    @pytest.mark.parametrize("tok,role", [(OWNER, "OWNER"), (ACCT, "ACCT"), (AGENT, "AGENT")])
    def test_stats_overview_200(self, tok, role):
        r = requests.get(f"{BASE}/stats/overview", headers=H(tok))
        assert r.status_code == 200, f"{role} /stats/overview: {r.status_code} {r.text}"
        d = r.json()
        assert "today_sales" in d
        assert "sales_total" in d

    def test_dev_stats_200(self):
        r = requests.get(f"{BASE}/dev/stats", headers=H(DEV))
        assert r.status_code == 200, r.text
