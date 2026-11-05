"""Controlled A/B on the shop repo. python ab.py N  -> prints a JSON list of run records."""
import json
import sys
import threading
import time
import urllib.request

REPO = r"C:\Users\AASHIS~1\AppData\Local\Temp\claude\c--aashish-programming-files-harness\76e2dc44-ac6e-4250-8e1d-069b2f4c534c\scratchpad\shopbg"
PROMPT = (
    "The checkout total for a discounted cart is wrong: tests/test_checkout.py::test_discounted_total fails "
    "(31.87 instead of 31.88). Find the cause, fix it without changing the tests, and run the tests."
)
ARMS = {  # name: (backend url, thinking)
    "A_graph_off_medium": ("http://127.0.0.1:8002", "medium"),
    "B_graph_on_medium": ("http://127.0.0.1:8001", "medium"),
    "C_graph_on_high": ("http://127.0.0.1:8001", "high"),
}
N = int(sys.argv[1])
out: list[dict] = []
lock = threading.Lock()


def call(url, path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def one(arm, i):
    url, thinking = ARMS[arm]
    t = call(url, "/api/tasks", {"repo_path": REPO, "instructions": PROMPT, "thinking": thinking})
    call(url, f"/api/tasks/{t['id']}/run", {})
    t0 = time.time()
    while time.time() - t0 < 900:
        t = call(url, f"/api/tasks/{t['id']}")
        if t["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(3)
    ev = call(url, f"/api/tasks/{t['id']}/events?after=0&limit=1000")
    ev = ev if isinstance(ev, list) else ev.get("items", [])
    types = [e["type"] for e in ev]
    rec = {
        "arm": arm, "i": i, "id": t["id"], "status": t["status"], "outcome": t.get("outcome"),
        "iterations": t.get("iterations"), "tool_calls": t.get("tool_calls"),
        "in_tok": t.get("input_tokens"), "out_tok": t.get("output_tokens"),
        "seconds": round(time.time() - t0), "graph_ctx": "graph.context" in types,
        "denied": sum(1 for e in ev if e["type"] == "tool.authorization" and e["data"].get("decision") == "deny"),
        "tool_errors": sum(1 for e in ev if e["type"] == "tool.result" and not e["data"].get("ok")),
    }
    with lock:
        out.append(rec)
        print(json.dumps(rec), flush=True)


def server_thread(arms):  # one task at a time per backend; interleave arms that share it
    for i in range(N):
        for arm in arms:
            one(arm, i)


ts = [threading.Thread(target=server_thread, args=(a,)) for a in (["A_graph_off_medium"], ["B_graph_on_medium", "C_graph_on_high"])]
[t.start() for t in ts]
[t.join() for t in ts]
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else "ab_out.json", "w"), indent=1)
