## Workflows

### od_projektu_do_shortlisty
- Zawołaj `match_project(...)`.
- Dociągnij 2–3 finalistów przez `get_entity(slug)`.
- Dla każdego finalistycznego targetu zawołaj `get_submission_brief(slug)` zanim zaczniesz pisać pitch.

### pitch_pod_konkretny_target
- `get_submission_brief(slug)` → sprawdź kanał i hard_filters.
- `get_pitch_rubric(target_slug=slug, funding_type=...)` → zbuduj deck.
- `review_pitch(deck_markdown, target_slug=slug, ...)` → napraw twarde braki.

### cotygodniowy_przeglad_pipeline
- `list_pipeline(stale_days=30)` → follow-upy i zaległe rozmowy.
- `set_status(...)` / `add_note(...)` po każdej akcji.

