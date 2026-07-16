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

## Tools

Thirteen tools, grouped by what they touch. Catalog tools are public (read scope);
pipeline tools are private to the server owner (write scope).

| Tool | Scope | What it does |
|------|-------|--------------|
| `match_project` | read | Rank funding sources for a project (genre, budget, stage, country). Returns candidates grouped by funding type — see [Scoring](#scoring). |
| `search_funding` | read | Full-text search over the catalog. Hyphens/slashes/`&` are safe (`turn-based`, `hack & slash`). |
| `filter_funding` | read | Structured filter: section, country, budget tier, comm rating, has-email. |
| `get_entity` | read | Full record for one source: links, contact, submission path, notes, amounts. |
| `get_submission_brief` | read | How to pitch a specific source: channel, submission links, stated criteria, hard filters, portfolio. |
| `get_pitch_rubric` | read | Deck rubric tailored to the target's funding type (publisher / VC / project investor / grant), derived from the upstream pitch tutorial. |
| `review_pitch` | read | Deterministic deck check: missing slides, missing numbers, hard-filter violations. It stays quiet on a solid deck — it does not pad. |
| `gamefunds_help` | read | Guidance topics + a bridge to the concept guides (`gamefunds://guide/*`). |
| `check_updates` | read | Cheap SHA check for upstream directory changes. |
| `set_status` | write | Set a pipeline status (`contacted`, `in_talks`, `signed`, …) for a source. |
| `add_note` | write | Append a dated note to a pipeline entry. |
| `list_pipeline` | write | Your outreach state; `stale_days=N` surfaces overdue follow-ups. |
| `sync_directory` | write | Re-sync the directory + guides from upstream. |

## Scoring

`match_project` uses a **deterministic heuristic — no LLM**. Results are grouped by
funding type (`publisher`, `vc_equity`, `project_investor`, `grant`), and **scores are
comparable only within a group**: a grant scoring 15 is not "worse" than a publisher
scoring 66, they are different funding paths.

Each candidate carries a `breakdown` (budget / country / stage / genre / comm), a
`confidence` (`high` / `medium` / `low`), and human-readable `reasons`. Two design
choices are worth knowing:

- **Genre outweighs country for publishers.** A cozy-game publisher in the US beats a
  generic publisher in your own country — country is a tie-breaker (weight 5), not the
  driver. For grants, country is near-decisive (weight 28), because grants are regional.
- **Missing data is not zero.** `breakdown.budget: null` and `confidence: low` mean the
  catalog lacks the data, not that it scored poorly. `genre_signal: none` means no
  catalog entry matches your genre at all — the ranking is then budget-and-stage only,
  flagged with `genre_warning`, and you should verify by hand.

**Hard filters** remove sources that cannot work (counted in `hard_filtered`): budget
off by 2+ tiers, a grant outside your country, or a genre-exclusivity clash (a
sandbox game against a publisher that says *"strictly no sandbox"*, a cozy sim against
*"horror only"*). These are dropped, not down-ranked.

The full weight table lives in `data/help/scoring.md` and is available at runtime via
`gamefunds_help("scoring")`.

## Data attribution

Directory data comes from [github.com/GameDevGrzesiek/GameFunds](https://github.com/GameDevGrzesiek/GameFunds) © Grzegorz Wątroba, MIT license.

`data/rubrics.json` is derived from Grzegorz's `PitchDeckTutorial.md` in that repository.

Grzegorz is a working game dev — his game is [**Foxy Dumplings**](https://store.steampowered.com/app/4184130/Foxy_Dumplings/) on Steam. If this tool is useful to you, wishlisting it is a good way to say thanks.

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

If `gamefunds sync` returns a GitHub **403 rate limit** error, set an unscoped personal access token and retry:

```bash
export GITHUB_TOKEN=<token>   # no scopes needed — the upstream repo is public
gamefunds sync --apply
```

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens) (classic, no checkboxes). Sync uses at most one `api.github.com` call per run; guide files are fetched from `raw.githubusercontent.com` (outside the 60/h unauthenticated API quota).

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

Generate `.env` from `.env.example`. Optional **shared read-only** token: `GAMEFUNDS_TOKEN_READONLY` — catalog access only; pipeline data is hidden and pipeline tools are refused with setup instructions.

### Rate limiting

Owner token: **60 requests/minute** (`GAMEFUNDS_RATE_LIMIT`). Shared read-only token: **300/minute** (`GAMEFUNDS_RATE_LIMIT_READONLY`) — higher because many forum users share one token. One LLM conversation uses ~10–50 tool calls. Limits are keyed by SHA-256 hash of the bearer token — the raw token never appears in logs or rate-limit storage.

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
| `read` | search, filter, get_entity (catalog only — no pipeline block), match_project, briefs, rubrics, review_pitch, help, check_updates |
| `write` | set_status, add_note, list_pipeline, sync_directory; get_entity includes pipeline |

Read-only tokens calling pipeline tools get a clear message pointing to the open-source repo — not a 500, and no private data.

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
