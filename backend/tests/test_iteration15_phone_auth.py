"""Iteration 15 — Phone + Bird SMS OTP auth tests.

Verifies the switch from Google to phone+OTP in Convex deployment fearless-ostrich-878:
  - /auth/otp/request validation + rate limit
  - /auth/otp/verify validation, 'request first' guard, brute-force lockout
  - /auth/name validation (>=2 chars)
  - Removed Google /auth/session route is 404
  - Public Convex functions used by the removed Google flow / internals are NOT public
  - Seeded tokens continue to work (/auth/me returns phone + name for 4 roles)
"""
import os

import pytest
import requests

API = "https://fearless-ostrich-878.eu-west-1.convex.site/api"
CONVEX = "https://fearless-ostrich-878.eu-west-1.convex.cloud/api/mutation"

# Fake Syrian number reserved for rate-limit / brute-force tests. NEVER a real phone.
FAKE_RL_PHONE = "+963900000777"
FAKE_BF_PHONE = "+963900000778"


def _post(path, body=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.post(f"{API}{path}", json=body or {}, headers=h, timeout=30)


def _get(path, token=None):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.get(f"{API}{path}", headers=h, timeout=30)


# ----- /auth/otp/request validation -----
class TestOtpRequest:
    def test_invalid_phone_too_short(self):
        r = _post("/auth/otp/request", {"phone": "12"})
        assert r.status_code == 400
        assert "أدخل رقم هاتف صحيح" in r.json().get("detail", "")

    def test_invalid_phone_no_country_code(self):
        r = _post("/auth/otp/request", {"phone": "0999"})
        assert r.status_code == 400
        assert "أدخل رقم هاتف صحيح" in r.json().get("detail", "")

    def test_valid_phone_bird_scope_error(self):
        # Known external issue: Bird key lacks verify:write → 400 "تعذر إرسال رمز التحقق"
        r = _post("/auth/otp/request", {"phone": "+963900000111"})
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        # Either Bird scope error OR "خدمة الرسائل غير مهيأة" (env missing). Both are external, not code bugs.
        assert ("تعذر إرسال رمز التحقق" in detail or "خدمة الرسائل" in detail
                or "رقم الهاتف غير صالح" in detail or "محاولات كثيرة" in detail)

    def test_rate_limit_after_5_requests(self):
        # The 1st-5th requests each insert a row and hit Bird (which 4xxs after rate note).
        # The 6th should be blocked BEFORE Bird by noteOtpRequest with the Arabic rate-limit msg.
        last = None
        for _ in range(7):
            last = _post("/auth/otp/request", {"phone": FAKE_RL_PHONE})
        assert last.status_code == 400
        assert "تم تجاوز عدد مرات الإرسال" in last.json().get("detail", "")


# ----- /auth/otp/verify -----
class TestOtpVerify:
    def test_invalid_code_format(self):
        r = _post("/auth/otp/verify", {"phone": "+963900000112", "code": "abc"})
        assert r.status_code == 400
        assert "رمز التحقق غير صحيح" in r.json().get("detail", "")

    def test_invalid_phone_format(self):
        r = _post("/auth/otp/verify", {"phone": "12", "code": "123456"})
        assert r.status_code == 400

    def test_verify_without_prior_request(self):
        # Use a brand-new phone that has never had /otp/request called in this hour.
        r = _post("/auth/otp/verify", {"phone": "+963900000999", "code": "123456"})
        assert r.status_code == 400
        assert "اطلب رمز تحقق أولاً" in r.json().get("detail", "")

    def test_brute_force_lockout_after_5_attempts(self):
        # Prime: one otp_request row (may 400 due to Bird, but row still inserted).
        _post("/auth/otp/request", {"phone": FAKE_BF_PHONE})
        last = None
        for _ in range(7):
            last = _post("/auth/otp/verify", {"phone": FAKE_BF_PHONE, "code": "000000"})
        assert last.status_code == 400
        assert "محاولات خاطئة كثيرة" in last.json().get("detail", "")


# ----- /auth/name -----
class TestSetName:
    def test_name_too_short(self):
        r = _post("/auth/name", {"name": "A"}, token="test_token_owner")
        assert r.status_code == 400
        assert "أدخل اسماً صحيحاً" in r.json().get("detail", "")

    def test_name_empty(self):
        r = _post("/auth/name", {"name": "  "}, token="test_token_owner")
        assert r.status_code == 400

    def test_name_valid_persists_and_restores(self):
        # Owner seeded name = "مالك الاختبار". Set to new value then restore.
        orig = "مالك الاختبار"
        r = _post("/auth/name", {"name": "اختبار الاسم"}, token="test_token_owner")
        assert r.status_code == 200
        me = _get("/auth/me", token="test_token_owner").json()
        assert me["name"] == "اختبار الاسم"
        # Restore
        r2 = _post("/auth/name", {"name": orig}, token="test_token_owner")
        assert r2.status_code == 200
        assert _get("/auth/me", token="test_token_owner").json()["name"] == orig


# ----- Removed Google route -----
class TestRemovedGoogleRoute:
    def test_auth_session_is_404(self):
        r = _post("/auth/session", {"token": "whatever"})
        assert r.status_code == 404
        assert "غير موجود" in r.json().get("detail", "")


# ----- Public Convex function surface — these MUST NOT be publicly callable -----
class TestConvexInternalsPrivate:
    @pytest.mark.parametrize("path", [
        "auth:syncSession",
        "edge:createSession",
        "edge:seedTestAccounts",
        "edge:noteOtpRequest",
        "edge:noteVerifyAttempt",
    ])
    def test_internal_function_not_public(self, path):
        r = requests.post(CONVEX, json={"path": path, "args": {}, "format": "json"}, timeout=30)
        body = r.text
        # Convex returns 200 with status:'error' + ErrorMessage "Could not find public function"
        # or a 4xx. Either way the body must contain the "not public" marker.
        assert ("Could not find public function" in body
                or "could not find public function" in body.lower()), f"{path}: {body[:300]}"


# ----- Seeded sessions still work for all 4 roles -----
class TestSeededTokens:
    @pytest.mark.parametrize("token,phone,name,role", [
        ("test_token_dev", "+963900000001", "مطور الاختبار", "DEVELOPER"),
        ("test_token_owner", "+963900000002", "مالك الاختبار", "OWNER"),
        ("test_token_acct", "+963900000003", "محاسب الاختبار", "EMPLOYEE"),
        ("test_token_agent", "+963900000004", "موزع الاختبار", "EMPLOYEE"),
    ])
    def test_me(self, token, phone, name, role):
        r = _get("/auth/me", token=token)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["phone"] == phone
        assert u["name"] == name
        assert u["role"] == role
