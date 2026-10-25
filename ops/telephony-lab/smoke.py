"""HTTP smoke check for the synthetic offline lab, never for production."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-history-fixtures", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = {}
    headers = {"Origin": "http://localhost:28081", "X-Requested-With": "Telefonagent"}
    # Fixed loopback origin, synthetic login, no provider endpoints or secrets.
    with httpx.Client(base_url="http://localhost:28001/api/v1", timeout=10, follow_redirects=False) as client:
        assert client.get("/health").status_code == 200
        results["backend_health"] = True
        assert client.get("http://localhost:28081/").status_code == 200
        results["frontend_http"] = True
        login = client.post("/auth/login", json={"username": "qa-owner@telefonagent.local", "password": "synthetic-offline-lab-password"}, headers=headers)
        assert login.status_code == 200
        results["login"] = True

        def write(method, path, body):
            csrf = client.cookies.get("telefonagent_csrf")
            response = client.request(method, path, json=body, headers={**headers, "X-CSRF-Token": csrf or ""})
            assert response.status_code == 200
            return response.json()

        companies = client.get("/platform/companies").json()
        salon = next(company for company in companies if company["name"] == "Salon Haarkunst Test")
        write("POST", "/auth/context", {"company_id": salon["id"]})
        original = client.get("/agent/config").json()
        config = original
        try:
            for mode in ("answering_machine", "agent"):
                payload = {key: value for key, value in original.items() if key not in {"tenant_id", "version", "updated_at", "can_edit", "role"}}
                config = write("PUT", "/agent/config", {**payload, "expected_version": config["version"], "operating_mode": mode})
                assert config["operating_mode"] == mode
                runtime = write("POST", "/agent/test-session", {})
                assert bool(runtime["tool_names"]) == (mode == "agent")
                results[mode + "_config_and_tools"] = True
        finally:
            payload = {key: value for key, value in original.items() if key not in {"tenant_id", "version", "updated_at", "can_edit", "role"}}
            write("PUT", "/agent/config", {**payload, "expected_version": config["version"]})
        results["original_mode_restored"] = True

        fixture_calls = []
        for mode in ("agent", "answering_machine"):
            fixtures = [company for company in companies if company["name"] == "Synthetic QA " + mode]
            if not fixtures:
                results[mode + "_persisted_history"] = False
                assert not args.require_history_fixtures
                continue
            matching_calls = []
            for company in fixtures:
                write("POST", "/auth/context", {"company_id": company["id"]})
                calls = client.get("/calls").json()
                matching_calls = [item for item in calls if item["operating_mode"] == mode]
                if matching_calls:
                    break
            assert matching_calls, "Run the PostgreSQL process-chain fixtures after any migration roundtrip."
            call = matching_calls[0]
            detail_response = client.get("/calls/" + call["id"])
            assert detail_response.headers.get("cache-control") == "no-store"
            detail = detail_response.json()
            assert detail["analysis_status"] == "completed" and detail["summary"]
            assert detail["transcript"] and detail["caller_name"] == "Anna Beispiel"
            assert detail["appointments"] == []
            assert bool(detail["actions"]) == (mode == "agent")
            for other_id in fixture_calls:
                assert client.get("/calls/" + other_id).status_code == 404
            fixture_calls.append(call["id"])
            results[mode + "_persisted_history"] = True
        results["cross_company_call_denied"] = len(fixture_calls) == 2
    rendered = json.dumps(results, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
