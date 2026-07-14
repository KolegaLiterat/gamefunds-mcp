# GameFunds MCP

MCP server for browsing a **local copy** of the [GameFunds](https://github.com/GameDevGrzesiek/GameFunds) directory (publishers, grants, VCs) and tracking your personal outreach pipeline.

## What it does — and what it does not

**Does:**
- Search and filter 200+ funding sources from the GameFunds directory
- Match your project (genre, budget, stage, country) to publishers, grants, and investors
- Return submission briefs, pitch rubrics, and deterministic deck checks
- Keep *your* pipeline state locally (contacts, notes, follow-ups)

**Does not:**
- Know about adult/NSFW games — the upstream catalog has almost no coverage there; `genre_signal: none` is honest, not a bug
- Guarantee grant amounts — many entries say "varies" or omit numbers; grants without budget data get `confidence: low`
- Match genres in languages other than English — queries like "przygodowa" won't map to catalog terms
- Replace legal or financial advice — it surfaces directory notes, not deal terms

## Data attribution

Directory data comes from [github.com/GameDevGrzesiek/GameFunds](https://github.com/GameDevGrzesiek/GameFunds) © Grzegorz Wątroba, MIT license.

`data/rubrics.json` is derived from Grzegorz's `PitchDeckTutorial.md` in that repository.

The database (`data/gamefunds.db`) and synced guides (`data/guides/`) are **not** in git — run `gamefunds sync --apply` on first use.

Your pipeline (publisher contacts, notes) lives only in your local DB and must never be committed.

## Local setup (stdio, no tokens)

```bash
git clone <this-repo>
cd gamefunds-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
gamefunds sync --apply
```

Stdio mode needs **no** `GAMEFUNDS_TOKEN`. Auth applies only to HTTP transport.

Paths to the database, guides, and logs are resolved from the package install location (not the process cwd). Override with `GAMEFUNDS_DATA_DIR` if needed (e.g. Docker volume at `/app/data`).

## Connect it to an agent

### Claude Code (local, stdio)

One command, from any directory:

```bash
claude mcp add gamefunds -- /absolute/path/to/gamefunds-mcp/.venv/bin/python -m gamefunds.server
```

Use the **absolute path to the virtualenv's Python**, not bare `python` — the agent
launches the server as a subprocess and will not inherit your activated venv.

Add `--scope project` to write it into the project's `.mcp.json` (shareable with a
team) instead of your personal config.

<details>
<summary>Manual config instead of the CLI</summary>

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "gamefunds": {
      "command": "/absolute/path/to/gamefunds-mcp/.venv/bin/python",
      "args": ["-m", "gamefunds.server"]
    }
  }
}
```

`cwd` is optional — data paths resolve from the package install location, not the process working directory.
</details>

### Claude Code (remote, HTTP + token)

If you deployed the server (see [HTTP deployment](#http-deployment)):

```bash
claude mcp add --transport http gamefunds https://your-host.example/mcp \
  --header "Authorization: Bearer $GAMEFUNDS_TOKEN"
```

### Claude Desktop

Edit the config file directly, then restart the app:

- macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gamefunds": {
      "command": "/absolute/path/to/gamefunds-mcp/.venv/bin/python",
      "args": ["-m", "gamefunds.server"]
    }
  }
}
```

`cwd` is optional — data paths resolve from the package install location, not the process working directory.

### Cursor

Same JSON as above, in `.cursor/mcp.json` (per project) or `~/.cursor/mcp.json` (global).

### Verify it works

In Claude Code, run `/mcp` — `gamefunds` should be listed as **connected**, exposing
13 tools and 6 resources. If it is not, check that `gamefunds sync --apply` has run
and that the Python path is absolute.

> **Editing the server code?** The agent starts the server once and keeps the process
> alive. Code changes are **not** picked up until you reconnect (`/mcp` → reconnect, or
> restart the agent). Testing against a stale process is the fastest way to conclude a
> working fix is broken.

### Then ask it something

The tools are self-describing, so plain language works:

```
I'm making a cozy pottery sim. Two-person studio in Poland, $180k budget,
vertical slice done. Where should I look for funding?
```

```
What does Team17 want in a pitch, and how do I submit to them?
```

```
Review this deck for Whitethorn Games: <paste your pitch deck as markdown>
```

```
Log that I sent Kiln to Anshar today, follow up in two weeks.
```

Genre matching works against an **English** catalog — describe the genre in English
(`cozy crafting sim`, `turn-based tactics`), even if you talk to the agent in another
language. If nothing in the catalog matches your genre, `match_project` tells you so
(`genre_signal: none`) rather than inventing a shortlist.

## CLI

| Command | Purpose |
|---------|---------|
| `gamefunds sync` | Dry-run upstream sync |
| `gamefunds sync --apply` | Download directory + guides into local DB |
| `gamefunds check` | Cheap SHA check for upstream updates |
| `gamefunds stats` | Entity and pipeline counts |
| `gamefunds token` | Generate a secure HTTP bearer token |
| `gamefunds serve --transport http` | Run HTTP server (requires token) |

## HTTP deployment

**HTTPS is mandatory.** A bearer token sent over plain HTTP is visible on the first request. Never expose HTTP publicly without TLS termination.

The server **refuses to start** in HTTP mode without `GAMEFUNDS_TOKEN`:

```bash
gamefunds token          # copy output
export GAMEFUNDS_TOKEN=...
gamefunds serve --transport http --port 8080
```

Generate `.env` from `.env.example`. Optional read-only token: `GAMEFUNDS_TOKEN_READONLY`.

### Rate limiting

Default: **60 requests/minute per token** (`GAMEFUNDS_RATE_LIMIT`). One LLM conversation uses ~10–50 tool calls, so 60/min is generous for real use but stops scrapers. The limit key is a SHA-256 hash of the bearer token — the raw token never appears in logs or rate-limit storage.

### Docker

```bash
cp .env.example .env   # set GAMEFUNDS_TOKEN
docker compose up -d --build
```

- Binds to `127.0.0.1:8080` by default — put **nginx, Caddy, or Traefik** in front with TLS
- `GAMEFUNDS_DATA_DIR` (default `/app/data` in the image) holds DB, synced guides, and logs — mount it as a volume
- Fresh container auto-runs `gamefunds sync --apply` if no DB exists
- Memory limit: 256 MB

### Scopes

| Scope | Tools |
|-------|-------|
| `read` | search, filter, get_entity, match_project, briefs, rubrics, review_pitch (read-only path), help, check_updates |
| `write` | set_status, add_note, sync_directory |

Read-only tokens calling `set_status` get a clear error, not a 500.

## Development

```bash
pip install -e ".[dev,http]"
pytest
```

## Security checklist

- [ ] `GAMEFUNDS_TOKEN` set before HTTP start
- [ ] TLS terminates before traffic hits the app
- [ ] `.env` not committed (in `.gitignore`)
- [ ] `data/*.db` and `data/guides/` not committed
- [ ] `GAMEFUNDS_LOG_ARGS=0` in production (default — no argument logging)

## License

MIT — see [LICENSE](LICENSE). GameFunds directory data © Grzegorz Wątroba, used under MIT with attribution.
