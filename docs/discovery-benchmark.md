# Discovery benchmark

How the discovery graph (`scripts/jfgraph/`) compares with the original LLM scout, measured on
fictional candidates against a frozen snapshot of real public job boards with a labeled ground
truth. The protocol is in `sim/README.md`; the gates in `sim/gates.yaml`.

Graphs: **A** = the original `/jobs-scout` (an LLM fetching and judging boards), **poller** = the
deterministic ATS poller alone, **B** = poller-first with board auto-discovery, a web sweep, and
LLM scoring, **C** = B plus the responsibility classifier and learned title terms, **D** = C plus
hiring signals and tier promotion. Recall is measured over the labeled universe; costs are the
CLI's estimates under subscription auth.

<!-- instance:se-leader -->
### Profile 1: leadership-only solutions / forward deployed engineering, remote US or one home city, comp floor set

Snapshot 2026-10-31: 8188 live postings across 29 boards; 160 labeled (17 fit, 16 borderline).

| graph | runs | recall mean | recall best | precision (log band) | precision (act-now) | F1 mean | est. cost / run | cost / true positive | Jaccard (all / act-now) |
|---|---|---|---|---|---|---|---|---|---|
| A | 6 | 0.137 | 0.235 | 1.000 | 1.000 | 0.236 | 0.714 | 0.384 | 0.180 / 0.256 |
| poller | 1 | 0.529 | 0.529 | 0.692 | – | 0.600 | 0.000 | 0.000 | – / – |
| B | 4 | 0.706 | 0.765 | 0.939 | 1.000 | 0.803 | 0.587 | 0.048 | 0.704 / 0.667 |
| C | 1 | 0.824 | 0.824 | 0.933 | 1.000 | 0.875 | 0.789 | 0.056 | – / – |
| D | 1 | 0.824 | 0.824 | 0.933 | 1.000 | 0.875 | 0.399 | 0.029 | – / – |

Board discovery: 30 companies, correct rate 1.0, wrong-board rate 0.0, mean probes 2.07.

**Gates**

| gate | check | verdict | detail |
|---|---|---|---|
| G-B | act-now precision (vs A mean) | PASS | Δ +0.000 vs min -0.050 |
| G-B | recall (vs A mean) | PASS | Δ +0.569 vs min +0.150 |
| G-B | cost per TP (vs A mean) | PASS | ratio 0.12 vs max 0.50 |
| G-B | act-now precision (vs A best run) | PASS | Δ +0.000 vs min -0.050 |
| G-B | recall (vs A best run) | PASS | Δ +0.471 vs min +0.150 |
| G-B | cost per TP (vs A best run) | PASS | ratio 0.25 vs max 0.50 |
| G-B | repeatability (act-now) | FAIL | Jaccard 0.667 vs min 0.85 |
| G-C | act-now precision (vs B mean) | PASS | Δ +0.000 vs min -0.030 |
| G-C | recall (vs B mean) | PASS | Δ +0.118 vs min +0.000 |
| G-C | cost per TP (vs B mean) | PASS | ratio 1.18 vs max 1.50 |
| G-C | learned-term rejection rate | n/a | no human review yet |
| G-D | recall unchanged (vs C mean) | PASS | |Δ| 0.000 vs max 0.010 |
| G-D | precision unchanged (vs C mean) | PASS | |Δ| 0.000 vs max 0.010 |
| G-D | about-to-hire 60-day hit rate | n/a | pending shadow phase |
| G-discover | correct_rate | PASS | 1.0 >= 0.8 |
| G-discover | wrong_board_rate | PASS | 0.0 <= 0.02 |
| G-discover | mean_probes | PASS | 2.07 <= 8 |

<!-- /instance:se-leader -->
<!-- instance:pm-senior -->
### Profile 2: senior individual-contributor platform product management, remote US or one home region, comp floor set

Snapshot 2026-10-31: 8188 live postings across 29 boards; 160 labeled (27 fit, 31 borderline).

| graph | runs | recall mean | recall best | precision (log band) | precision (act-now) | F1 mean | est. cost / run | cost / true positive | Jaccard (all / act-now) |
|---|---|---|---|---|---|---|---|---|---|
| A | 5 | 0.118 | 0.556 | 1.000 | 1.000 | 0.393 | 0.934 | 0.546 | 0.404 / 0.380 |
| poller | 1 | 0.778 | 0.778 | 0.553 | – | 0.646 | 0.000 | 0.000 | – / – |
| B | 1 | 0.926 | 0.926 | 0.781 | 1.000 | 0.847 | 1.185 | 0.047 | – / – |
| C | 1 | 0.926 | 0.926 | 0.862 | 1.000 | 0.893 | 0.910 | 0.036 | – / – |

Board discovery: 30 companies, correct rate 1.0, wrong-board rate 0.0, mean probes 2.07.

**Gates**

| gate | check | verdict | detail |
|---|---|---|---|
| G-B | act-now precision (vs A mean) | PASS | Δ +0.000 vs min -0.050 |
| G-B | recall (vs A mean) | PASS | Δ +0.807 vs min +0.150 |
| G-B | cost per TP (vs A mean) | PASS | ratio 0.09 vs max 0.50 |
| G-B | act-now precision (vs A best run) | PASS | Δ +0.000 vs min -0.050 |
| G-B | recall (vs A best run) | PASS | Δ +0.370 vs min +0.150 |
| G-B | cost per TP (vs A best run) | PASS | ratio 0.27 vs max 0.50 |
| G-B | repeatability (act-now) | n/a | needs 2+ runs |
| G-C | act-now precision (vs B mean) | PASS | Δ +0.000 vs min -0.030 |
| G-C | recall (vs B mean) | PASS | Δ +0.000 vs min +0.000 |
| G-C | cost per TP (vs B mean) | PASS | ratio 0.77 vs max 1.50 |
| G-C | learned-term rejection rate | n/a | no human review yet |
| G-D | D vs C | n/a | missing runs |
| G-discover | correct_rate | PASS | 1.0 >= 0.8 |
| G-discover | wrong_board_rate | PASS | 0.0 <= 0.02 |
| G-discover | mean_probes | PASS | 2.07 <= 8 |

<!-- /instance:pm-senior -->
