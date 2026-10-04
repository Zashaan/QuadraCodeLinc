"""Finite, offline HTTP/tool smoke test; uses only synthetic fixtures and no server."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, logger


def main() -> None:
    logger.disabled = True
    token = "offline-smoke-token-not-a-secret-123456"
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    with TestClient(create_app(Settings(token))) as client:
        response = client.post("/sessions", headers=headers, json={"call_id": "CA" + "f" * 32})
        response.raise_for_status()
        session = response.json()
        sid = session["session_id"]
        try:
            calls = [
                ("resolve_member_id", {"spoken_id": "D E M O zero zero one"}),
                ("get_member", {"member_id": "DEMO001"}),
                ("update_conversation_context", {"intent": "estimate", "procedure": "crown", "provider_id": "DEMO_P1"}),
                ("retrieve_plan_context", {"query": "What does the plan say about crowns?"}),
                ("search_providers", {"zip_code": "27401", "radius_miles": 10, "procedure": "crown"}),
                ("calculate_benefit", {"treatment_date": "2026-11-01"}),
                ("calculate_benefit", {"procedure": "root canal", "provider_id": "DEMO_P3", "treatment_date": "2026-11-01"}),
                ("retrieve_plan_context", {"query": "orthodontic braces"}),
            ]
            for index, (name, args) in enumerate(calls):
                body = {"tool_name": name, "tool_call_id": f"smoke-{index}", "arguments": args}
                response = client.post(f"/sessions/{sid}/tools", headers=headers, json=body)
                response.raise_for_status()
                results.append(response.json())
            assert results[1]["result"]["member"]["annual_maximum_remaining"] == 800
            assert results[5]["result"]["plan_payment"] == "600.00"
            assert results[5]["result"]["estimated_member_payment"] == "600.00"
            assert results[6]["result"]["plan_payment"] == "800.00"
            assert results[7]["result"]["status"] == "unverified"
        finally:
            assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
    if "--contracts" in sys.argv:
        print(json.dumps({"session": session, "results": results}))
    else:
        print("Offline benefits smoke passed: lookup, context, scoped retrieval, provider search, calculator, annual cap, cleanup.")


if __name__ == "__main__":
    main()
