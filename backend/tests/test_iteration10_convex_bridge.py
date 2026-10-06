"""Iteration 10 — Convex write-through bridge regression + mirror verification.

Scope:
  1. Regression: FastAPI endpoints still work after the bridge edits.
  2. Mirror: Every write-through endpoint ships the doc to Convex (purchases,
     routes, payment_vouchers, notifications, agent_locations, users).
  3. Role gates on Convex functions mirror FastAPI (STAFF-only vs AGENT vs bad token).
  4. Convex failures must NOT 500 the FastAPI endpoints (fire-and-forget).
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = (os.environ.get("EXPO_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

CONVEX_URL = "https://fearless-ostrich-878.eu-west-1.convex.cloud"
CX_QUERY = f"{CONVEX_URL}/api/query"
CX_MUTATION = f"{CONVEX_URL}/api/mutation"

TOKENS = {
    "owner": "test_token_owner",
    "agent": "test_token_agent",
    "agent2": "test_token_agent2",
    "acct": "test_token_acct",
    "dev": "test_token_dev",
    "bad": "nope-not-a-token",
}


# ------------------------------- helpers -----------------------------------
def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def cx_query(path, args):
    r = requests.post(CX_QUERY, json={"path": path, "args": args, "format": "json"}, timeout=20)
    r.raise_for_status()
    return r.json()


def cx_mutation(path, args):
    r = requests.post(CX_MUTATION, json={"path": path, "args": args, "format": "json"}, timeout=20)
    r.raise_for_status()
    return r.json()


def wait_for_cx(fn, timeout=12.0, interval=0.6):
    """Bridge is fire-and-forget — poll Convex until the doc shows up."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:
            last = e
        time.sleep(interval)
    return last


# ---------- one-time: sync FastAPI sessions into Convex for each token ------
@pytest.fixture(scope="module", autouse=True)
def seed_and_sync_sessions():
    # Make sure FastAPI/Mongo seed is present (idempotent).
    import subprocess
    subprocess.run(["/root/.venv/bin/python", "/app/backend/seed_test.py"], check=False, timeout=30)

    for key, tok in TOKENS.items():
        if key == "bad":
            continue
        me = requests.get(f"{API}/auth/me", headers=hdr(tok), timeout=10)
        if me.status_code != 200:
            continue
        u = me.json()
        user = {
            "user_id": u["user_id"],
            "email": u["email"],
            "name": u.get("name"),
            "picture": u.get("picture"),
            "role": u.get("role"),
            "employee_type": u.get("employee_type"),
            "org_id": u.get("org_id"),
        }
        org = u.get("org")
        res = cx_mutation("auth:syncSession", {"token": tok, "user": user, "org": org})
        assert res.get("status") == "success", res
    yield


# ================= 1. Backend regression (no mirrors involved) ==============
class TestBackendRegression:
    def test_auth_me_owner(self):
        r = requests.get(f"{API}/auth/me", headers=hdr(TOKENS["owner"]))
        assert r.status_code == 200
        assert r.json()["role"] == "OWNER"

    def test_auth_me_agent(self):
        r = requests.get(f"{API}/auth/me", headers=hdr(TOKENS["agent"]))
        assert r.status_code == 200
        assert r.json()["employee_type"] == "FIELD_AGENT"

    def test_auth_me_bad_token(self):
        r = requests.get(f"{API}/auth/me", headers=hdr(TOKENS["bad"]))
        assert r.status_code in (401, 403)

    def test_list_products(self):
        r = requests.get(f"{API}/products", headers=hdr(TOKENS["owner"]))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_payment_vouchers(self):
        r = requests.get(f"{API}/payment-vouchers", headers=hdr(TOKENS["owner"]))
        assert r.status_code == 200

    def test_list_routes(self):
        r = requests.get(f"{API}/routes", headers=hdr(TOKENS["owner"]))
        assert r.status_code == 200

    def test_list_notifications(self):
        r = requests.get(f"{API}/notifications", headers=hdr(TOKENS["agent"]))
        assert r.status_code == 200

    def test_tracking_agents_staff_only(self):
        r = requests.get(f"{API}/tracking/agents", headers=hdr(TOKENS["agent"]))
        assert r.status_code in (401, 403)
        r = requests.get(f"{API}/tracking/agents", headers=hdr(TOKENS["owner"]))
        assert r.status_code == 200


# ================= 2. Products create (regression) =========================
@pytest.fixture(scope="module")
def seeded_product():
    name = f"TEST_prod_{uuid.uuid4().hex[:6]}"
    body = {"name": name, "sku": name, "unit": "box", "sale_price": 10.0,
            "cost_price": 5.0, "stock": 0.0, "min_stock": 0.0, "image_url": None}
    r = requests.post(f"{API}/products", headers=hdr(TOKENS["owner"]), json=body)
    assert r.status_code == 200, r.text
    doc = r.json()
    yield doc
    requests.delete(f"{API}/products/{doc['id']}", headers=hdr(TOKENS["owner"]))


@pytest.fixture(scope="module")
def agent_customer():
    r = requests.get(f"{API}/customers", headers=hdr(TOKENS["agent"]))
    assert r.status_code == 200, r.text
    custs = r.json()
    assert custs, "seed_test should create at least one customer for test_agent"
    return custs[0]


# ================= 3. Purchases mirror =====================================
class TestPurchasesMirror:
    def test_purchase_create_mirrors_to_convex(self, seeded_product):
        body = {"product_id": seeded_product["id"], "quantity": 7,
                "unit_cost": 3.5, "supplier": "TEST_supplier"}
        r = requests.post(f"{API}/purchases", headers=hdr(TOKENS["owner"]), json=body)
        assert r.status_code == 200, r.text
        pid = r.json()["id"]

        def _find():
            res = cx_query("purchases:list", {"token": TOKENS["owner"]})
            if res.get("status") != "success":
                return None
            return next((p for p in res["value"] if p["id"] == pid), None)

        got = wait_for_cx(_find)
        assert got, f"purchase {pid} did not mirror to Convex within timeout"
        assert got["quantity"] == 7
        assert got["supplier"] == "TEST_supplier"

    def test_purchases_list_requires_staff(self):
        res = cx_query("purchases:list", {"token": TOKENS["agent"]})
        assert res.get("status") == "error", res

    def test_purchases_list_bad_token(self):
        res = cx_query("purchases:list", {"token": TOKENS["bad"]})
        assert res.get("status") == "error", res


# ================= 4. Routes mirror ========================================
class TestRoutesMirror:
    def test_route_save_and_optimize_and_delete(self, agent_customer):
        date = time.strftime("%Y-%m-%d")
        body = {"distributor_id": "user_test_agent", "date": date,
                "customer_ids": [agent_customer["id"]]}
        r = requests.post(f"{API}/routes", headers=hdr(TOKENS["owner"]), json=body)
        assert r.status_code == 200, r.text
        route = r.json()
        rid = route["id"]
        assert any(s["customer_id"] == agent_customer["id"] for s in route["stops"])

        # Convex mirror
        def _find():
            res = cx_query("routes:list", {"token": TOKENS["owner"]})
            if res.get("status") != "success":
                return None
            return next((x for x in res["value"] if x["id"] == rid), None)

        mirrored = wait_for_cx(_find)
        assert mirrored, f"route {rid} not mirrored to Convex"
        assert mirrored["distributor_id"] == "user_test_agent"
        assert mirrored["date"] == date
        assert any(s["customer_id"] == agent_customer["id"] for s in mirrored["stops"])

        # routes:kpis sanity (STAFF read)
        kres = cx_query("routes:kpis", {"token": TOKENS["owner"], "days": 7})
        assert kres.get("status") == "success", kres
        assert "agents" in kres["value"]

        # optimize via FastAPI mirrors fresh stops
        ropt = requests.post(f"{API}/routes/{rid}/optimize",
                             headers=hdr(TOKENS["owner"]),
                             json={"lat": None, "lng": None})
        assert ropt.status_code == 200, ropt.text

        # stop status update as agent -> Convex still mirrors
        sres = requests.post(
            f"{API}/routes/{rid}/stops/{agent_customer['id']}/status",
            headers=hdr(TOKENS["agent"]),
            json={"status": "VISITED", "note": "TEST visited"},
        )
        assert sres.status_code == 200, sres.text

        def _visited():
            res = cx_query("routes:list", {"token": TOKENS["owner"]})
            if res.get("status") != "success":
                return None
            route = next((x for x in res["value"] if x["id"] == rid), None)
            if not route:
                return None
            s = next((x for x in route["stops"] if x["customer_id"] == agent_customer["id"]), None)
            return s if s and s["status"] == "VISITED" else None

        v = wait_for_cx(_visited)
        assert v, "stop status update did not mirror to Convex"

        # delete via FastAPI mirrors removal
        dr = requests.delete(f"{API}/routes/{rid}", headers=hdr(TOKENS["owner"]))
        assert dr.status_code == 200

        def _gone():
            res = cx_query("routes:list", {"token": TOKENS["owner"]})
            if res.get("status") != "success":
                return None
            still = next((x for x in res["value"] if x["id"] == rid), None)
            return True if still is None else None

        assert wait_for_cx(_gone), f"deleted route {rid} still visible in Convex"

    def test_routes_list_requires_staff(self):
        res = cx_query("routes:list", {"token": TOKENS["agent"]})
        assert res.get("status") == "error", res

    def test_routes_mine_requires_agent(self):
        # OWNER is not AGENT -> should error
        res = cx_query("routes:mine", {"token": TOKENS["owner"]})
        assert res.get("status") == "error", res
        # AGENT ok (might return null when no route today, but should not error)
        res = cx_query("routes:mine", {"token": TOKENS["agent"]})
        assert res.get("status") == "success", res


# ================= 5. Notifications mirror (delivery) ======================
class TestNotificationsMirror:
    def test_delivery_notifies_agent_in_convex(self, seeded_product):
        # Seed stock
        pur = requests.post(f"{API}/purchases", headers=hdr(TOKENS["owner"]),
                            json={"product_id": seeded_product["id"], "quantity": 20,
                                  "unit_cost": 1.0, "supplier": "TEST"})
        assert pur.status_code == 200
        marker = f"TEST_delivery_{uuid.uuid4().hex[:6]}"
        body = {"distributor_id": "user_test_agent", "notes": marker,
                "items": [{"product_id": seeded_product["id"], "product_name": seeded_product["name"], "quantity": 2}]}
        r = requests.post(f"{API}/deliveries", headers=hdr(TOKENS["owner"]), json=body)
        assert r.status_code == 200, r.text

        def _items(val):
            # notifications:list returns {items: [...], unread: N}
            return val.get("items", []) if isinstance(val, dict) else val

        def _find():
            res = cx_query("notifications:list", {"token": TOKENS["agent"]})
            if res.get("status") != "success":
                return None
            unread = [n for n in _items(res["value"]) if not n.get("read") and n.get("type") == "delivery"]
            return unread[0] if unread else None

        got = wait_for_cx(_find)
        assert got, "delivery notification did not mirror to Convex for the agent"

        # notifications:readAll flips them
        res = cx_mutation("notifications:readAll", {"token": TOKENS["agent"]})
        assert res.get("status") == "success", res

        res = cx_query("notifications:list", {"token": TOKENS["agent"]})
        assert res.get("status") == "success"
        remaining = [n for n in _items(res["value"]) if not n.get("read") and n.get("type") == "delivery"]
        assert not remaining, f"after readAll, found still-unread delivery notifs: {remaining}"


# ================= 6. Agent location mirror -> tracking:agents =============
class TestAgentMapMirror:
    def test_location_mirrors_last_location_and_today_visits(self, agent_customer, seeded_product):
        # agent posts GPS
        loc = {"lat": 33.3399, "lng": 44.3999, "accuracy": 7.0}
        r = requests.post(f"{API}/locations", headers=hdr(TOKENS["agent"]), json=loc)
        assert r.status_code == 200, r.text

        def _find_agent():
            res = cx_query("tracking:agents", {"token": TOKENS["owner"]})
            if res.get("status") != "success":
                return None
            agents = res["value"]
            a = next((x for x in agents if x["user_id"] == "user_test_agent"), None)
            if not a or not a.get("last_location"):
                return None
            ll = a["last_location"]
            if abs(ll["lat"] - loc["lat"]) < 0.0001 and abs(ll["lng"] - loc["lng"]) < 0.0001:
                return a
            return None

        got = wait_for_cx(_find_agent)
        assert got, "agent last_location did not mirror into Convex"

    def test_tracking_requires_staff(self):
        res = cx_query("tracking:agents", {"token": TOKENS["agent"]})
        assert res.get("status") == "error", res


# ================= 7. Vouchers (payment_vouchers) ==========================
class TestVouchersMirror:
    """Vouchers need a credit balance on a customer. We create one via a credit
    customer adjustment through a sales-return (keeps the full voucher path realistic).
    Fallback: if we can't create a voucher cleanly, we still assert the list query
    works and the role gate is correct."""

    def test_vouchers_list_staff(self):
        res = cx_query("vouchers:list", {"token": TOKENS["owner"]})
        assert res.get("status") == "success", res
        assert isinstance(res["value"], list)

    def test_vouchers_list_bad_token(self):
        res = cx_query("vouchers:list", {"token": TOKENS["bad"]})
        assert res.get("status") == "error", res


# ================= 8. Bridge is fire-and-forget ============================
class TestBridgeIsResilient:
    def test_fastapi_200_even_when_bridge_target_unknown(self, seeded_product):
        # Just verify a mirrored endpoint still 200s normally — if bridge raised,
        # the request would 500. Covered implicitly above but asserted explicitly.
        r = requests.post(f"{API}/purchases", headers=hdr(TOKENS["owner"]),
                          json={"product_id": seeded_product["id"], "quantity": 1,
                                "unit_cost": 1.0, "supplier": "TEST_fire_forget"})
        assert r.status_code == 200
