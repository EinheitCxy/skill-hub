# ScholarAPI operations

Run these from this nested `scholarapi` skill directory. The key stays only in
the parent shell environment:

```bash
read -rs SCHOLARAPI_API_KEY
export SCHOLARAPI_API_KEY
uv run --no-project python scholarapi.py search --q 'robot learning' --limit 50
```

Continue an already-budgeted relevance search with its returned cursor:

```bash
uv run --no-project python scholarapi.py search --q 'robot learning' --cursor '<next_cursor>' --limit 50
```

Use index-order enumeration only when the Planner decision explicitly allows
it and states the finite page/result budget:

```bash
uv run --no-project python scholarapi.py list --q 'robot learning' --limit 100
```

Validate the wrapper without network access:

```bash
uv run --no-project python scholarapi.py --help
env -u SCHOLARAPI_API_KEY uv run --no-project python scholarapi.py search --q 'robot learning'
```

The missing-key command must exit with code 2 before making a request.
