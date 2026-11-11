# ScholarAPI API contract

Source: [official API documentation](https://scholarapi.net/docs/api), checked
2026-08-04. Use this reference for endpoint facts; do not infer parameters from
metadata records.

## Connection

- Base URL: `https://scholarapi.net/api/v1`.
- Send `SCHOLARAPI_API_KEY` only as the `X-API-Key` request header.
- The Planner helper never fetches full text or PDFs implicitly.

## Bounded endpoints

`GET /search` is relevance-ranked, requires at least one repeatable `q`, and
uses `cursor` / `next_cursor` for continuation. `GET /list` is index-order
enumeration, accepts optional repeatable `q`, and uses `indexed_after` /
`next_indexed_after`. Both support date and availability filters plus a limit
between 1 and 1000.

Records can include an ID, DOI, source URL, title, authors, abstract, journal,
publication/index dates, and availability flags. Those fields are candidate
metadata, not verified evidence.

## Error surface

Preserve HTTP status and `X-Request-Id` where supplied. The wrapper classifies
validation, transport, HTTP, malformed JSON, and malformed response errors;
it never prints the key or an unbounded error body.
