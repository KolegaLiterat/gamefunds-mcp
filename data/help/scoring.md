## match_project scoring (v2)

Deterministic heuristic scoring (no LLM). Scores are comparable **only within each funding_type group**.

### Weights (breakdown fields)

| Field | Publisher | VC equity | Project investor | Grant |
|---|---|---|---|---|
| budget (exact) | 25 | 25 | 25 | 25 |
| budget (near) | 8 | 8 | 8 | 8 |
| country match | 5 | 10 | 5 | 28 (15 if amount unknown) |
| stage fit | 14 | 14 | 14 | 14 |
| genre (phrase) | up to 30 | up to 20 | up to 20 | up to 20 |
| genre (tokens) | up to 30 | up to 20 | up to 20 | up to 20 |
| comm rating | 4× stars | 4× stars | 4× stars | 4× stars |

`breakdown.budget: null` means budget data is unknown (not “checked, no match”).

### Genre matching

- Phrases (e.g. `pixel art`, `cozy pottery`) score higher than single tokens.
- Stopwords (`the`, `with`, …) and low-signal descriptors (`art`, `game`, `sim` alone) are filtered.
- Matched against notes, notable titles, eligibility, backing, funding terms, target scope.

### Hard filters (removed from results, counted in `hard_filtered`)

- Budget tier mismatch (≥2 tiers apart)
- Grant outside requested country
- Genre exclusivity violations:
  - Negative: `strictly no sandbox`, `no X games`
  - Positive: `horror only`, `only cozy`, `we publish horror`, `strategy focus`, `strictly strategy/sim/roguelike`

### Genre signal (`genre_signal`)

Per-term coverage against the full directory corpus. Tokens appearing in >20% of entries are treated as domain noise (e.g. `indie`, `games`) and excluded from scoring and coverage.

`genre_terms` lists `matched` and `unmatched` significant terms (single tokens and multi-word phrases from comma-separated segments). Unmatched terms are informational only — they never reduce scores or confidence.

- `good` — at least one significant term appears in the corpus → genre scoring runs normally, no `genre_warning`
- `none` — zero significant terms in the corpus → `genre_warning`, all candidates `confidence: low`, genre scoring skipped

When `genre_warning` is set, it names terms with no corpus coverage.

### Budget signal (`budget_signal`)

Bounds are computed from parsed `amount_min_usd` / `amount_max_usd` in the directory (outlier-resistant).

- `in_range` — project budget within catalog scale; budget scoring is meaningful
- `above_range` — budget above largest sensible catalog entries → `budget_warning`, all candidates `confidence: low`
- `below_range` — budget below smallest sensible catalog entries → same as `above_range`

`budget_usd` must be positive; zero or negative raises `ValueError`.

### Output caps

- Default `limit_per_type`: 3 (max 10)
- `hard_filtered` — excluded by rules above
- `below_cutoff` — scored but not returned due to per-group limit
