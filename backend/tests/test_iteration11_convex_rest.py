"""
Iteration 11: Full MongoDB -> Convex cutover regression.

Scope:
- Convex REST surface at .site domain under /api/*
- FastAPI stub at :8001 has NO Mongo
- Auth / role gates / 401 / 403 / 404 / CORS
- Core reads across all documented endpoints
- Write transactions and persistence (assert on created ids)
"""
import os
import uuid
import json as _json
import pytest
import requests

CONVEX_SITE = "https://fearless-ostrich-878.eu-west-1.convex.site"
CONVEX_CLOUD = "https://fearless-ostrich-878.eu-west-1.convex.cloud"
LOCAL = "http://localhost:8001"

TOKENS = {
    "owner": "test_token_owner",
    "agent": "test_token_agent",
    "agent2": "test_token_agent2",
    "acct": "test_token_acct",
    "dev": "test_token_dev",
}


def H(role: str):
    return {"Authorization": f"Bearer {TOKENS[role]}", "Content-Type": "application/json"}


def U(path: str) -> str:
    return f"{CONVEX_SITE}{path}"


@pytest.fixture(scope="session")
def s():
    return requests.Session()


# ---------- FastAPI stub has NO Mongo ----------

class TestFastApiStub:
    def test_root_says_convex_no_mongo(self, s):
        r = s.get(f"{LOCAL}/api/")
        assert r.status_code == 200
        j = r.json()
        assert j.get("backend") == "convex"
        assert j.get("mongodb") is False

    def test_health_ok(self, s):
        r = s.get(f"{LOCAL}/api/health")
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_no_motor_or_mongo_import(self):
        src = open("/app/backend/server.py").read()
        import re
        # ignore comment lines; look for actual imports
        for line in src.splitlines():
            if line.strip().startswith("#") or '"""' in line:
                continue
            assert "import motor" not in line
            assert "from motor" not in line
            assert "import pymongo" not in line
            assert "from pymongo" not in line
            assert "AsyncIOMotorClient" not in line
            assert "MongoClient" not in line


# ---------- Auth / role gates ----------

class TestAuth:
    def test_me_owner(self, s):
        r = s.get(U("/api/auth/me"), headers=H("owner"))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["role"] == "OWNER"
        assert j["user_id"] == "user_test_owner"
        assert j["org_id"] == "org_test_1"

    def test_me_agent(self, s):
        r = s.get(U("/api/auth/me"), headers=H("agent"))
        assert r.status_code == 200
        j = r.json()
        # Role model: role=EMPLOYEE, employee_type=FIELD_AGENT
        assert j["role"] in ("EMPLOYEE", "FIELD_AGENT")
        assert j.get("employee_type") == "FIELD_AGENT"
        assert j["user_id"] == "user_test_agent"

    def test_me_dev(self, s):
        r = s.get(U("/api/auth/me"), headers=H("dev"))
        assert r.status_code == 200
        assert r.json()["role"] in ("DEVELOPER", "DEV")

    def test_bad_token_401_arabic(self, s):
        r = s.get(U("/api/auth/me"), headers={"Authorization": "Bearer bogus_xyz"})
        assert r.status_code == 401
        j = r.json()
        assert "detail" in j
        # Must be Arabic
        assert any("\u0600" <= ch <= "\u06FF" for ch in j["detail"]), j

    def test_no_token_401(self, s):
        r = s.get(U("/api/auth/me"))
        assert r.status_code == 401
        assert "detail" in r.json()

    def test_agent_calling_dev_only_403(self, s):
        r = s.get(U("/api/dev/stats"), headers=H("agent"))
        assert r.status_code == 403, r.text
        assert "detail" in r.json()

    def test_owner_calling_dev_only_403(self, s):
        r = s.get(U("/api/dev/stats"), headers=H("owner"))
        assert r.status_code == 403

    def test_owner_calling_agent_only_sales_create_403(self, s):
        # POST /api/sales is agent-only
        r = s.post(U("/api/sales"), headers=H("owner"), json={"customer_id": "x", "items": []})
        assert r.status_code in (400, 403), r.text  # 403 role gate OR 400 validation
        if r.status_code == 403:
            assert "detail" in r.json()

    def test_agent_calling_staff_only_stats_alerts_403(self, s):
        # stats/alerts, stats/finance, stats/reports are STAFF-only
        r = s.get(U("/api/stats/alerts"), headers=H("agent"))
        assert r.status_code == 403, r.text

    def test_agent_calling_staff_only_stats_finance_403(self, s):
        r = s.get(U("/api/stats/finance"), headers=H("agent"))
        assert r.status_code == 403, r.text

    def test_agent_calling_staff_only_stats_reports_403(self, s):
        r = s.get(U("/api/stats/reports?period=day"), headers=H("agent"))
        assert r.status_code == 403, r.text

    def test_agent_calling_staff_only_employees_403(self, s):
        r = s.get(U("/api/employees"), headers=H("agent"))
        assert r.status_code == 403, r.text

    def test_unknown_route_404(self, s):
        r = s.get(U("/api/nonexistent-xyz-404"), headers=H("owner"))
        assert r.status_code == 404
        assert "detail" in r.json()


# ---------- CORS ----------

class TestCORS:
    def test_options_preflight_204(self, s):
        r = s.options(
            U("/api/products"),
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert r.status_code == 204, r.text
        assert r.headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in r.headers.get("Access-Control-Allow-Methods", "")

    def test_options_on_dynamic_route(self, s):
        r = s.options(U("/api/sales/some-id/void"))
        assert r.status_code == 204
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


# ---------- Core read endpoints (owner) ----------

class TestOwnerReads:
    @pytest.mark.parametrize("path", [
        "/api/products",
        "/api/customers",
        "/api/sales",
        "/api/collections",
        "/api/stats/overview",
        "/api/stats/alerts",
        "/api/stats/finance",
        "/api/stats/reports?period=day",
        "/api/stats/leaderboard",
        "/api/notifications",
        "/api/org/profile",
        "/api/plans",
        "/api/settings/payment",
        "/api/routes",
        "/api/tracking/agents",
        "/api/employees",
        "/api/deliveries",
        "/api/inventory/distributors",
        "/api/purchases",
        "/api/payment-vouchers",
        "/api/stock-movements",
        "/api/price-history",
    ])
    def test_owner_reads_200(self, s, path):
        r = s.get(U(path), headers=H("owner"))
        assert r.status_code == 200, f"{path} => {r.status_code} {r.text}"
        # Must be JSON
        j = r.json()
        # Shape: list, dict, or something non-null
        assert j is not None


class TestPublicEndpoints:
    def test_app_version_latest_no_token(self, s):
        r = s.get(U("/api/app-version/latest"))
        assert r.status_code == 200, r.text
        # may be dict or null (if no versions)
        j = r.json()
        assert j is None or isinstance(j, (dict, list))

    def test_app_version_latest_with_platform(self, s):
        r = s.get(U("/api/app-version/latest?platform=android"))
        assert r.status_code == 200


# ---------- Notifications shape ----------

class TestNotificationsShape:
    def test_shape(self, s):
        r = s.get(U("/api/notifications"), headers=H("owner"))
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j, dict)
        assert "items" in j
        assert "unread" in j
        assert isinstance(j["items"], list)
        assert isinstance(j["unread"], int)


# ---------- Org profile shape ----------

class TestOrgProfile:
    def test_shape(self, s):
        r = s.get(U("/api/org/profile"), headers=H("owner"))
        assert r.status_code == 200
        j = r.json()
        # org/profile returns the profile dict (no id, since caller's org is implicit)
        assert "name" in j and "currency" in j
        assert "has_logo" in j


# ---------- Write transactions (owner) ----------

STATE = {}


class TestOwnerWrites:
    def test_create_product(self, s):
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"TEST_منتج_{suffix}",
            "category": "TEST",
            "sale_price": 1000,
            "cost_price": 500,
            "stock": 0,
            "unit": "قطعة",
            "min_stock": 1,
        }
        r = s.post(U("/api/products"), headers=H("owner"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["name"] == payload["name"]
        pid = j.get("id")
        assert pid, j
        STATE["product_id"] = pid

    def test_product_appears_in_list(self, s):
        assert "product_id" in STATE
        r = s.get(U("/api/products"), headers=H("owner"))
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        ids = {p.get("id") for p in items}
        assert STATE["product_id"] in ids

    def test_create_purchase_updates_stock(self, s):
        pid = STATE["product_id"]
        payload = {
            "product_id": pid,
            "quantity": 10,
            "unit_cost": 600,
            "supplier": "TEST_Supplier",
        }
        r = s.post(U("/api/purchases"), headers=H("owner"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        STATE["purchase_id"] = j.get("id")
        assert j.get("quantity") == 10
        # Now verify stock increased
        r2 = s.get(U("/api/products"), headers=H("owner"))
        prod = next((p for p in r2.json() if p.get("id") == pid), None)
        assert prod is not None
        assert prod.get("stock", 0) >= 10
        # cost_price updated
        assert prod.get("cost_price") == 600

    def test_purchase_appears_in_list(self, s):
        assert "purchase_id" in STATE
        r = s.get(U("/api/purchases"), headers=H("owner"))
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        ids = {p.get("id") for p in items}
        assert STATE["purchase_id"] in ids

    def test_create_delivery_to_agent(self, s):
        pid = STATE["product_id"]
        payload = {
            "distributor_id": "user_test_agent",
            "items": [{"product_id": pid, "quantity": 3}],
            "notes": "TEST delivery",
        }
        r = s.post(U("/api/deliveries"), headers=H("owner"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        STATE["delivery_id"] = j.get("id")
        assert STATE["delivery_id"]

    def test_delivery_appears(self, s):
        assert "delivery_id" in STATE
        r = s.get(U("/api/deliveries"), headers=H("owner"))
        assert r.status_code == 200
        items = r.json()
        ids = {d.get("id") for d in items}
        assert STATE["delivery_id"] in ids

    def test_create_route(self, s):
        # Owner cannot create customers (agent-only). Use an existing customer from the org.
        rc = s.get(U("/api/customers"), headers=H("owner"))
        assert rc.status_code == 200
        custs = rc.json()
        assert isinstance(custs, list) and len(custs) > 0, "need at least 1 customer"
        cid = custs[0].get("id")
        assert cid

        payload = {
            "distributor_id": "user_test_agent",
            "date": "2026-01-15",
            "customer_ids": [cid],
        }
        r = s.post(U("/api/routes"), headers=H("owner"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        STATE["route_id"] = j.get("id")
        assert STATE["route_id"]

    def test_route_appears_in_list(self, s):
        assert "route_id" in STATE
        r = s.get(U("/api/routes?date=2026-01-15"), headers=H("owner"))
        assert r.status_code == 200
        items = r.json()
        ids = {rr.get("id") for rr in items}
        assert STATE["route_id"] in ids

    def test_employee_invite_returns_emp_code(self, s):
        payload = {"name": f"TEST_emp_{uuid.uuid4().hex[:4]}", "employee_type": "ACCOUNTANT"}
        r = s.post(U("/api/employees/invite"), headers=H("owner"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        code = j.get("code") or j.get("invite_code") or j.get("invitation_code")
        assert code, j
        assert str(code).startswith("EMP-"), code
        STATE["invite_id"] = j.get("id")


# ---------- Agent flows ----------

class TestAgentWrites:
    def test_agent_create_customer(self, s):
        payload = {
            "name": f"TEST_agent_cust_{uuid.uuid4().hex[:6]}",
            "phone": "+964 7701000002",
            "address": "Baghdad",
            "lat": 33.31,
            "lng": 44.36,
        }
        r = s.post(U("/api/customers"), headers=H("agent"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("id")
        STATE["agent_customer_id"] = j["id"]

    def test_agent_sees_own_inventory(self, s):
        r = s.get(U("/api/my/inventory"), headers=H("agent"))
        assert r.status_code == 200
        inv = r.json()
        assert isinstance(inv, list)

    def test_confirm_delivery_then_sell(self, s):
        """Confirm the delivery so agent has inventory, then make a credit sale."""
        if not STATE.get("delivery_id"):
            pytest.skip("no delivery to confirm")
        r = s.post(U(f"/api/deliveries/{STATE['delivery_id']}/confirm"), headers=H("agent"))
        assert r.status_code == 200, r.text

    def test_agent_create_sale_decrements_inventory(self, s):
        assert "agent_customer_id" in STATE
        inv_r = s.get(U("/api/my/inventory"), headers=H("agent"))
        inv = inv_r.json()
        if not inv:
            pytest.skip("Agent has no inventory to sell")
        pick = next((it for it in inv if (it.get("quantity") or it.get("qty") or it.get("stock") or 0) > 0), None)
        if not pick:
            pytest.skip("No stocked item in agent inventory")
        pid = pick.get("product_id") or pick.get("id")
        before_qty = pick.get("quantity") or pick.get("qty") or pick.get("stock") or 0

        payload = {
            "customer_id": STATE["agent_customer_id"],
            "items": [{"product_id": pid, "quantity": 1}],
            "paid_amount": 0,  # fully on credit so customer balance increases
        }
        r = s.post(U("/api/sales"), headers=H("agent"), json=payload)
        assert r.status_code == 200, r.text
        sale = r.json()
        STATE["sale_id"] = sale.get("id")
        STATE["sale_remaining"] = sale.get("remaining") or sale.get("total") or 0
        assert STATE["sale_id"]

        # Inventory decremented
        inv2 = s.get(U("/api/my/inventory"), headers=H("agent")).json()
        after = next((it for it in inv2 if (it.get("product_id") or it.get("id")) == pid), None)
        if after:
            after_qty = after.get("quantity") or after.get("qty") or after.get("stock") or 0
            assert after_qty == before_qty - 1, f"before={before_qty} after={after_qty}"

    def test_agent_create_collection(self, s):
        assert "agent_customer_id" in STATE
        remaining = STATE.get("sale_remaining", 0) or 0
        if remaining <= 0:
            pytest.skip("customer has no debt to collect")
        amount = min(remaining, 100)
        payload = {"customer_id": STATE["agent_customer_id"], "amount": amount, "notes": "TEST"}
        r = s.post(U("/api/collections"), headers=H("agent"), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("id")


# ---------- Dev endpoints ----------

class TestDevConsole:
    def test_dev_stats(self, s):
        r = s.get(U("/api/dev/stats"), headers=H("dev"))
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), dict)

    def test_dev_orgs(self, s):
        r = s.get(U("/api/dev/orgs"), headers=H("dev"))
        assert r.status_code == 200
        orgs = r.json()
        assert isinstance(orgs, list)
        ids = {o.get("id") for o in orgs}
        assert "org_test_1" in ids

    def test_dev_monitoring(self, s):
        r = s.get(U("/api/dev/monitoring"), headers=H("dev"))
        assert r.status_code == 200
        # may be dict or list
        j = r.json()
        assert isinstance(j, (dict, list))

    def test_dev_licenses(self, s):
        r = s.get(U("/api/dev/licenses"), headers=H("dev"))
        assert r.status_code == 200

    def test_dev_versions(self, s):
        r = s.get(U("/api/dev/versions"), headers=H("dev"))
        assert r.status_code == 200


# ---------- Cleanup (best effort) ----------

@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    s = requests.Session()
    if STATE.get("route_id"):
        try:
            s.delete(U(f"/api/routes/{STATE['route_id']}"), headers=H("owner"))
        except Exception:
            pass
    if STATE.get("invite_id"):
        try:
            s.delete(U(f"/api/employees/invite/{STATE['invite_id']}"), headers=H("owner"))
        except Exception:
            pass
    if STATE.get("product_id"):
        try:
            s.delete(U(f"/api/products/{STATE['product_id']}"), headers=H("owner"))
        except Exception:
            pass
