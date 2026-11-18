"""HTTP-level test client for the FastAPI app, wired to the local bare git repository."""
import time

from starlette.testclient import TestClient

from arkstorage.app import create_app
from tests.helpers import C, FakeClient, FakeGit

LIVE = []


def open_client(app):
    tc = TestClient(app, base_url="http://localhost")
    tc.__enter__()
    LIVE.append(tc)
    return tc


class Api:
    def __init__(self, store, remote):
        self.app = create_app(testing=True)
        self.remote = remote
        st = self.app.ark_store
        st.client, st.git = FakeClient(remote), FakeGit(remote)
        st.cache.client = st.client
        self.st = st
        self.c = C(open_client(self.app))
        self.csrf = ""

    def anon(self):
        return C(open_client(self.app))

    def login(self, user="ark", pw="ark"):
        r = self.c.post("/api/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            self.csrf = r.get_json()["csrf"]
        return r

    def call(self, method, url, **kw):
        h = kw.pop("headers", {})
        h["X-CSRF-Token"] = self.csrf
        return getattr(self.c, method)(url, headers=h, **kw)

    def job(self, resp):
        jid = resp.get_json()["job"]["id"]
        for _ in range(300):
            j = self.call("get", f"/api/jobs/{jid}").get_json()
            if j["state"] != "running":
                assert j["state"] == "done", j
                return j
            time.sleep(0.1)
        raise AssertionError("job timeout")

    def upload(self, files, dest="/", policy="rename"):
        b = self.call("post", "/api/upload/begin", json={"files": [{"id": f"f{i}", "size": len(d)} for i, (n, d) in enumerate(files)]}).get_json()["batch"]
        for i, (_n, d) in enumerate(files):
            for off in range(0, max(1, len(d)), 700_000):
                r = self.call("put", f"/api/upload/{b}/f{i}?offset={off}", data=d[off:off + 700_000])
                assert r.status_code == 200, r.get_json()
        r = self.call("post", f"/api/upload/{b}/commit", json={"files": [
            {"id": f"f{i}", "path": (dest.rstrip("/") + "/" + n), "mtime": 1700000000000} for i, (n, d) in enumerate(files)], "on_conflict": policy})
        assert r.status_code == 200, r.get_json()
        return self.job(r)
