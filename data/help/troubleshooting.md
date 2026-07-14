## Troubleshooting

### Parser crash during sync
- `sync_directory` writes nothing if the parser fails.
- Check `parser_error` (line number + raw row), then update `parser.py` for the new upstream format.

### Result sets too large
- Narrow with `filter_funding(...)` instead of paging through hundreds of records.
- If you see `truncated: true`, reduce the query scope.
