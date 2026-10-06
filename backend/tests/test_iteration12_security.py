"""
Iteration 12: Security — verify internal-only Convex functions
cannot be invoked over the public HTTP mutation API.

Expected: POST .../api/mutation with path edge:createSession / migrate:* / bridge:*
must fail with 'Could not find public function' (or equivalent not-found error).
"""
import json
import pytest
import requests

CLOUD = "https://fearless-ostrich-878.eu-west-1.convex.cloud"
URL = f"{CLOUD}/api/mutation"
URL_QUERY = f"{CLOUD}/api/query"
URL_ACTION = f"{CLOUD}/api/action"


def _call(url: str, path: str, args: dict | None = None):
    return requests.post(
        url,
        headers={"Content-Type": "application/json"},
        data=json.dumps({"path": path, "args": args or {}, "format": "json"}),
        timeout=15,
    )


def _assert_not_public(r: requests.Response, name: str):
    """Convex returns 200 with status:error for not-found public function."""
    body = r.text
    # Accept any of:
    #  - "Could not find public function"
    #  - "Could not find function"
    #  - "is not a public function"
    #  - 404 status
    low = body.lower()
    assert (
        "could not find public function" in low
        or "could not find function" in low
        or "is not a public function" in low
        or r.status_code == 404
    ), f"{name} appears to be publicly callable: status={r.status_code} body={body[:400]}"


class TestInternalFunctionsNotPublic:
    def test_edge_create_session_not_public(self):
        r = _call(URL, "edge:createSession", {"email": "attacker@test.com", "name": "x"})
        _assert_not_public(r, "edge:createSession")

    @pytest.mark.parametrize("fn", [
        "migrate:importUsers",
        "migrate:importOrgs",
        "migrate:importAll",
        "migrate:run",
        "migrate:verify",
    ])
    def test_migrate_not_public(self, fn):
        r = _call(URL, fn, {})
        _assert_not_public(r, fn)

    @pytest.mark.parametrize("fn", [
        "bridge:upsert",
        "bridge:insertRow",
        "bridge:removeByKey",
    ])
    def test_bridge_not_public(self, fn):
        r = _call(URL, fn, {"secret": "x", "table": "y", "doc": {}})
        _assert_not_public(r, fn)

    def test_edge_exchange_session_still_public_action(self):
        """exchangeSession is the public OAuth action and MUST remain callable
        (will fail validation but not with 'Could not find public function')."""
        r = _call(URL_ACTION, "edge:exchangeSession", {})
        body = r.text.lower()
        # Should NOT be "could not find public function" — it exists but will error on bad args.
        assert "could not find public function" not in body, body[:400]


class TestPublicReadsStillWork:
    """Sanity: public query path still finds public functions."""

    def test_app_version_query_exists(self):
        # pick any known public query (extra:appVersionLatest or similar)
        r = _call(URL_QUERY, "extra:appVersionLatest", {"platform": "android"})
        body = r.text.lower()
        # Function must exist (not "could not find public function").
        assert "could not find public function" not in body, body[:400]
