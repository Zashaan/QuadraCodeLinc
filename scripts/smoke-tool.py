"""Exercise the live local HTTP tool boundary without AWS or Twilio calls."""

import json
import os
from urllib.request import Request, urlopen
from uuid import uuid4

base_url = os.environ.get("ABE_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
token = os.environ.get("ABE_INTERNAL_TOKEN", "")
if len(token) < 32:
    raise SystemExit("Set ABE_INTERNAL_TOKEN in the root .env before running this check.")


def request(path: str, method: str, body: dict[str, object] | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=5) as response:
        return json.load(response) if response.status != 204 else None


session = request("/sessions", "POST", {"call_id": f"CA{uuid4().hex}"})
session_id = session["session_id"]
try:
    result = request(
        f"/sessions/{session_id}/tools",
        "POST",
        {"tool_name": "get_member", "tool_call_id": str(uuid4()), "arguments": {"member_id": "DEMO001"}},
    )
    assert result["result"]["status"] == "success"
    assert result["result"]["member"]["annual_maximum_remaining"] == 800
    print("PASS: get_member(DEMO001) returned the stored demo balance of $800.")
finally:
    request(f"/sessions/{session_id}", "DELETE")
