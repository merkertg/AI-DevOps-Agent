"""Exercise the investigation API end to end against a running server.

Used for the container check in CI and for the clean-environment walkthrough.
Tokens come from SMOKE_READ_TOKEN and SMOKE_WRITE_TOKEN; standard library only.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures/stage05/after_deployment.json"


class SmokeFailure(AssertionError):
    """Report the first step whose response did not match the contract."""


def request(
    base: str,
    method: str,
    path: str,
    token: str | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Send one request; return status, lowercase headers and body, even on errors."""
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(
        base + path, data=body, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, headers, response.read()
    except urllib.error.HTTPError as error:
        headers = {key.lower(): value for key, value in error.headers.items()}
        return error.code, headers, error.read()


def expect(condition: bool, step: str) -> None:
    """Fail with the step name when a check does not hold."""
    if not condition:
        raise SmokeFailure(step)


def wait_for_health(base: str, seconds: float) -> None:
    """Poll /health until it answers 200 or the time runs out."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            if request(base, "GET", "/health")[0] == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SmokeFailure("health did not become ready")
        time.sleep(0.5)


def run(
    base: str, read_token: str, write_token: str, fixture: Path
) -> dict[str, object]:
    """Run every step and return the facts observed along the way."""
    body = fixture.read_bytes()
    status, _, _ = request(base, "GET", "/investigations")
    expect(status == 401, "a request without a token is refused with 401")
    status, _, _ = request(base, "POST", "/investigations", read_token, body)
    expect(status == 403, "a read token cannot save")
    status, headers, raw = request(base, "POST", "/investigations", write_token, body)
    expect(status in {200, 201}, "the write token saves the fixture")
    receipt = json.loads(raw)
    identifier = str(receipt["investigation_id"])
    expect(
        headers.get("location") == f"/investigations/{identifier}", "location header"
    )
    status, _, raw = request(base, "POST", "/investigations", write_token, body)
    expect(
        status == 200 and json.loads(raw)["created"] is False, "repeat is idempotent"
    )
    status, _, first = request(base, "GET", f"/investigations/{identifier}", read_token)
    _, _, second = request(base, "GET", f"/investigations/{identifier}", read_token)
    expect(status == 200 and first == second, "the stored report reads back unchanged")
    report = json.loads(first)
    status, _, raw = request(
        base, "GET", f"/investigations/{identifier}/verification", read_token
    )
    expect(status == 200 and json.loads(raw)["status"] == "verified", "verification")
    status, _, raw = request(
        base, "POST", f"/investigations/{identifier}/agent", read_token
    )
    agent = json.loads(raw)
    expect(status == 200 and agent["agent"]["status"] == "answered", "agent answers")
    expect(
        agent["next_check"] is None or agent["next_check"]["executed"] is False,
        "the agent executes nothing",
    )
    status, _, _ = request(base, "GET", "/investigations/inv-" + "0" * 24, read_token)
    expect(status == 404, "an unknown investigation is 404")
    return {
        "investigation_id": identifier,
        "facts": [fact["code"] for fact in report["facts"]],
        "data_quality": report["data_quality"]["status"],
        "agent_hypotheses": len(agent["hypotheses"]),
        "next_check": agent["next_check"]["kind"] if agent["next_check"] else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Wait for the server, run the smoke steps and print a JSON summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    arguments = parser.parse_args(argv)
    read_token = os.environ.get("SMOKE_READ_TOKEN", "")
    write_token = os.environ.get("SMOKE_WRITE_TOKEN", "")
    if not read_token or not write_token:
        print("error: set SMOKE_READ_TOKEN and SMOKE_WRITE_TOKEN", file=sys.stderr)
        return 2
    base = arguments.base_url.rstrip("/")
    try:
        wait_for_health(base, arguments.wait_seconds)
        summary = run(base, read_token, write_token, arguments.fixture)
    except (SmokeFailure, OSError, ValueError, KeyError) as error:
        print(f"error: smoke step failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", **summary}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
