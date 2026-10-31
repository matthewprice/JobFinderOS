# Opportunity Scoring Rubric — TEMPLATE

> Copy to `config/scoring_rubric.md` (or let `/onboard` generate it). The agents score
> every opportunity 1–10 against YOUR priorities, then surface the best ones. Nothing
> here is career-specific — you set the weights and what "great" means for each
> dimension based on your own profile.

## Dimensions & Weights
Adjust the weights to reflect what matters most to you (they should sum to 100%).

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Role fit | 25% | How closely the title/scope matches your Target Roles |
| Company fit | 20% | Is it the kind of employer you want (stage, mission, reputation)? |
| Comp alignment | 20% | Does pay meet or beat your floor? (unknown = assume possible, mid score) |
| Skill/domain fit | 15% | Does it use your real strengths and experience? |
| Growth signal | 10% | Is the employer growing / hiring / stable vs. contracting? |
| Location fit | 10% | Remote / your city / acceptable arrangement? |

> Add, remove, or rename dimensions to fit your field. A nurse might add "shift type"
> or "patient ratio"; a contractor might add "contract length."

## Scoring Guide (per dimension)
For each dimension, define what a 10, 6, and 2 look like *for you*. Example skeleton:
- **10** — exact match to your top priority
- **8** — strong match
- **6** — viable but not ideal / unknown for a credible employer
- **4** — stretch
- **2** — poor match

Fill in concrete anchors so scores are consistent. (The `/onboard` skill drafts these
from your profile; edit freely.)

## Formula
```
score = Σ (dimension_score × dimension_weight)
```

## Action Thresholds
The numbers live in `config/discovery.yaml` (`thresholds.log`, `thresholds.act`; defaults 5 and 7) so the
discovery graph, the scout, and the digest all use the same two lines.

| Score | Action |
|-------|--------|
| 7–10 | Act — feature in the daily digest, human-path stub, outreach before applying |
| 5–6 | Log — opportunity note in the vault, included in the weekly review |
| < 5 | Discard |
