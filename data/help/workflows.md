## Workflows

### project_to_shortlist
- Call `match_project(...)`.
- Pull 2–3 finalists with `get_entity(slug)`.
- For each finalist, call `get_submission_brief(slug)` before writing any pitch or email.

### pitch_for_specific_target
- `get_submission_brief(slug)` → check channel and hard_filters.
- `get_pitch_rubric(target_slug=slug, funding_type=...)` → build the deck.
- `review_pitch(deck_markdown, target_slug=slug, ...)` → fix hard gaps.

### weekly_pipeline_review
- `list_pipeline(stale_days=30)` → follow-ups and stale conversations.
- `set_status(...)` / `add_note(...)` after every real action.
