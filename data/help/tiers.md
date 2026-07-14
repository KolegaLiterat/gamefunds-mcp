## Tiers

- **budget_tier**: derived from `amount_max_usd` when parsed, else from `$`/`$$`/`$$$` symbols in publisher rows
  - 1 = under ~$200k
  - 2 = $200k–$2M
  - 3 = above ~$2M
- **comm_rating**: 1–3 stars (★..★★★) — sections A/B only

### Currency conversion (approximate)

When parsing grant/VC amounts in £ or €, GameFunds uses fixed rates for **rough USD bounds only**:

- **GBP → USD**: × 1.27
- **EUR → USD**: × 1.09

These are not live FX rates. Treat `amount_min_usd` / `amount_max_usd` as order-of-magnitude hints, not accounting precision.
