## Troubleshooting

### Parser crash during sync
- `sync_directory` nie zapisuje nic, jeśli parser padnie.
- Zobacz `parser_error` (linia + raw_row), zaktualizuj `parser.py` pod nowy format.

### Za duże wyniki
- Zawężaj `filter_funding(...)` zamiast stronicować przez setki rekordów.
- Jeśli widzisz `truncated: true`, zmniejsz zakres zapytania.

