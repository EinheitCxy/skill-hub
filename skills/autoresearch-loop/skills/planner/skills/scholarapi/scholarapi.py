#!/usr/bin/env python3
"""Run bounded ScholarAPI metadata retrieval with auditable JSON output."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://scholarapi.net/api/v1"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
REQUEST_TIMEOUT_SECONDS = 30


def parse_bool(value: str) -> bool:
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retrieve bounded ScholarAPI metadata as JSON."
    )
    subparsers = parser.add_subparsers(dest="endpoint", required=True)

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--q", action="append", default=[], metavar="QUERY")
        command.add_argument("--published-after", metavar="DATE")
        command.add_argument("--published-before", metavar="DATE")
        command.add_argument("--has-text", type=parse_bool, metavar="true|false")
        command.add_argument("--has-pdf", type=parse_bool, metavar="true|false")
        command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    search = subparsers.add_parser("search", help="Run a relevance-ranked search.")
    add_common_options(search)
    search.add_argument("--cursor", help="Continue from a prior next_cursor.")

    listing = subparsers.add_parser("list", help="Run an explicit index-order scan.")
    add_common_options(listing)
    listing.add_argument(
        "--indexed-after", help="Continue from a prior next_indexed_after."
    )
    return parser


def emit_error(
    kind: str,
    message: str,
    *,
    status: int | None = None,
    request_id: str | None = None,
) -> None:
    error: dict[str, Any] = {"kind": kind, "message": message}
    if status is not None:
        error["status"] = status
    if request_id:
        error["request_id"] = request_id
    print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)


def request_parameters(args: argparse.Namespace) -> list[tuple[str, str]]:
    parameters: list[tuple[str, str]] = [("q", query) for query in args.q]
    for name in ("published_after", "published_before"):
        value = getattr(args, name)
        if value:
            parameters.append((name, value))
    for name in ("has_text", "has_pdf"):
        value = getattr(args, name)
        if value is not None:
            parameters.append((name, str(value).lower()))
    parameters.append(("limit", str(args.limit)))
    continuation_name = "cursor" if args.endpoint == "search" else "indexed_after"
    continuation_value = getattr(args, continuation_name)
    if continuation_value:
        parameters.append((continuation_name, continuation_value))
    return parameters


def request_provenance(args: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {"q": args.q, "limit": args.limit}
    for name in ("published_after", "published_before", "has_text", "has_pdf"):
        value = getattr(args, name)
        if value is not None:
            request[name] = value
    continuation_name = "cursor" if args.endpoint == "search" else "indexed_after"
    continuation_value = getattr(args, continuation_name)
    if continuation_value:
        request[continuation_name] = continuation_value
    return request


def validate(args: argparse.Namespace) -> str | None:
    if not 1 <= args.limit <= MAX_LIMIT:
        return f"--limit must be between 1 and {MAX_LIMIT}"
    if args.endpoint == "search" and not args.q:
        return "search requires at least one --q QUERY"
    return None


def retrieve(args: argparse.Namespace, api_key: str) -> dict[str, Any]:
    parameters = request_parameters(args)
    url = f"{BASE_URL}/{args.endpoint}?{urlencode(parameters)}"
    request = Request(url, headers={"X-API-Key": api_key})
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            request_id = response.headers.get("X-Request-Id")
            body = response.read()
    except HTTPError as error:
        emit_error(
            "http",
            f"ScholarAPI returned HTTP {error.code}",
            status=error.code,
            request_id=error.headers.get("X-Request-Id"),
        )
        raise SystemExit(1) from error
    except URLError as error:
        emit_error("transport", f"ScholarAPI request failed: {error.reason}")
        raise SystemExit(1) from error

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        emit_error("malformed_json", "ScholarAPI returned invalid JSON")
        raise SystemExit(1) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        emit_error("malformed_response", "ScholarAPI response lacks a results list")
        raise SystemExit(1)

    pagination_key = "next_cursor" if args.endpoint == "search" else "next_indexed_after"
    return {
        "schema_version": 1,
        "provider": "scholarapi",
        "endpoint": f"/{args.endpoint}",
        "retrieved_at": retrieved_at,
        "request": request_provenance(args),
        "http": {"status": status, "request_id": request_id},
        "result_count": len(payload["results"]),
        "records": payload["results"],
        "pagination": {pagination_key: payload.get(pagination_key)},
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validation_error = validate(args)
    if validation_error:
        emit_error("validation", validation_error)
        return 2

    api_key = os.environ.get("SCHOLARAPI_API_KEY")
    if not api_key:
        emit_error(
            "configuration",
            "SCHOLARAPI_API_KEY is not set; configure it before making a request",
        )
        return 2

    print(json.dumps(retrieve(args, api_key), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
