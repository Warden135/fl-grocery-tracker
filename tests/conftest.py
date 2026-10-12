import itertools
import os
import tempfile

import pytest

# These must be set before the app is imported.
_TMP = tempfile.mkdtemp(prefix="shelfscout-")
os.environ["GROCERY_DB"] = os.path.join(_TMP, "test.db")
os.environ["GEOCODER"] = "off"
os.environ["SIGNUP_LIMIT"] = "1000"
os.environ.pop("APP_PASSWORD", None)

import seed  # noqa: E402
from app.main import app  # noqa: E402

H = {"X-App": "1"}
_counter = itertools.count(1)


@pytest.fixture(scope="session", autouse=True)
def seeded():
    seed.main()


@pytest.fixture()
def client():
    return app.test_client()


@pytest.fixture()
def make_user():
    """Create a signed-in client (a new user each call). Returns (client, user_json)."""
    def _make(zip5="32202", name="Tester"):
        c = app.test_client()
        n = next(_counter)
        r = c.post("/api/auth/signup", headers=H, json={
            "name": f"{name} {n}", "email": f"user{n}@example.com",
            "password": "correct-horse-1", "zip": zip5})
        assert r.status_code == 201, r.get_json()
        return c, r.get_json()["user"]
    return _make


def post(c, path, **body):
    return c.post(path, json=body, headers=H)


def patch(c, path, **body):
    return c.patch(path, json=body, headers=H)


def delete(c, path):
    return c.delete(path, headers=H)
