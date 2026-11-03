# Redress points, conditional on finishing

Main comparison (at least three earlier finishes): **267 hidden-boat cases** in 2026, **28 course-races**, **14 evenings**. Lower MAE/RMSE is better. Negative signed error awards too few points (generous redress); positive error awards too many.

## All 2026 finishing cases

Combining established and sparse-history cases gives **354 simulated duties**. Each boat is hidden separately. All methods receive the same cases.

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.329 | 1.942 | +0.086 |
| Time distribution → expected points | 1.385 | 1.807 | +0.022 |
| Ranking model → expected points | 1.610 | 2.073 | -0.087 |

The aggregate time-distribution estimate has the smallest squared error; the single-time estimate has the smallest absolute error. This is not evidence that either dominates for every boat or stage of the season. Paired uncertainty for this combined sample is in `all_paired_comparisons.csv`.

## 2026 evaluation with established history

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.177 | 1.790 | +0.160 |
| Time distribution → expected points | 1.218 | 1.610 | +0.071 |
| Ranking model → expected points | 1.413 | 1.794 | -0.066 |

Each course-race has equal weight; boats within it share that weight. RMSE is the square root of mean squared point error. Squared error directly targets the conditional mean; MAE is also reported but instead favours the conditional median.

### Lille Bane

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.184 | 1.818 | +0.322 |
| Time distribution → expected points | 1.167 | 1.568 | +0.161 |
| Ranking model → expected points | 1.394 | 1.773 | +0.042 |

### Stor Bane

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.171 | 1.761 | -0.002 |
| Time distribution → expected points | 1.269 | 1.652 | -0.019 |
| Ranking model → expected points | 1.432 | 1.815 | -0.175 |

## Paired uncertainty

20,000 date-block bootstrap resamples; both courses and all hidden boats on the same evening stay together. Differences are candidate minus baseline; negative is better. These intervals do not capture all dependence across evenings from shared boats and fitted history.

| Candidate − baseline | Metric | Difference | Approx. 95% interval |
|---|---|---:|---:|
| Ranking model → expected points − Single predicted time | MAE | +0.236 | [+0.133, +0.382] |
| Ranking model → expected points − Single predicted time | RMSE | +0.005 | [-0.096, +0.135] |
| Ranking model → expected points − Time distribution → expected points | MAE | +0.195 | [+0.098, +0.338] |
| Ranking model → expected points − Time distribution → expected points | RMSE | +0.184 | [+0.092, +0.307] |
| Time distribution → expected points − Single predicted time | MAE | +0.041 | [-0.030, +0.112] |
| Time distribution → expected points − Single predicted time | RMSE | -0.179 | [-0.263, -0.095] |
| Ranking model → expected points − Time distribution (season reset) | MAE | +0.152 | [+0.074, +0.233] |
| Ranking model → expected points − Time distribution (season reset) | RMSE | +0.148 | [+0.056, +0.243] |

## Early-season / sparse-history cases

An additional **87 2026 cases** have fewer than 3 previous finishes that season. These are evaluated separately because the dashboard normally hides such estimates. The ranking model has no previous-season boat strengths; the primary time model retains 2025 history. These cases matter for duty assignments near the season start.

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.381 | 1.993 | -0.168 |
| Time distribution → expected points | 1.605 | 2.037 | -0.324 |
| Ranking model → expected points | 1.921 | 2.367 | -0.214 |


## History sensitivity

The existing time model retains 2025 boat history in 2026; the dashboard ranking model starts each season fresh. The following additional time-model results reset boat states at the season boundary, so the state history matches the ranking model.

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time (season reset) | 1.211 | 1.789 | +0.240 |
| Time distribution (season reset) | 1.261 | 1.646 | +0.153 |
| Ranking model → expected points | 1.413 | 1.794 | -0.066 |

## Boat-level calibration check

Low aggregate bias can conceal opposite errors for different boats. This diagnostic shows the five lowest observed average scores among boats with at least five established-history test cases. Selection is retrospective; it is not used to tune or correct predictions. Larger predicted points than observed points mean less favourable compensation.

| Boat | Cases | Actual average | Single time | Time distribution | Ranking |
|---|---:|---:|---:|---:|---:|
| Christian GREJS | 14 | 1.071 | 1.000 | 1.594 | 1.273 |
| Oluf GRØNKJÆR | 9 | 1.667 | 3.000 | 3.204 | 4.120 |
| Lotte MARKERT | 12 | 2.417 | 2.167 | 2.380 | 1.908 |
| Axel THOMSEN | 13 | 2.538 | 2.769 | 3.059 | 4.018 |
| Henrik AXELSEN | 13 | 3.385 | 3.000 | 3.265 | 3.537 |

Full boat-level errors are in `boat_summary.csv`. An aggregate improvement does not establish calibrated or neutral compensation for every boat.

## Protocol

- Only raw 2025–2026 corrected times/statuses enter. Saved fitted redress states, 2023/2024 results and the production parameter cache are never read.
- The time model uses the existing filter and likelihood. Global q, q_gamma and initial variance are fitted on 2025 only, with zero initial boat means, then frozen. There is no replay from terminal states. Course histories stay separate.
- Ranking prior SD is frozen at 2, previously chosen on 2025. It is not retuned on these 2026 redress outcomes. 2026 was already explored in the odds project, so this is a chronological retrospective test, not a pristine external validation.
- The established-history comparison requires 3 earlier finishes in the current season; the separate sparse-history comparison covers the remaining boats. At least two other finishers must remain. All methods use exactly the same cases.
- Hide one actual finisher at a time. Its time and position are excluded from all current-race fitting; reveal it only for scoring and subsequent-race updates.
- The time model re-estimates both the day mean and day variance with the target removed. Its single-time method ranks exp(predicted log-time); its distribution method integrates the existing predictive lognormal across the rivals’ actual corrected times.
- Ranking conditions on the other finishers’ observed order, including an update to the joint strength posterior. Conditional insertion probabilities are then averaged over 4,096 fixed Sobol draws from its Laplace approximation. It does not sum independent simulated head-to-head outcomes.
- Both methods assume the missing boat finishes. Other non-finishers score behind it and do not contribute thresholds. No DNF penalty or attendance probability is fitted.
- Points follow the current combined-course low-point implementation: 1 + the number of strictly faster finishers (minimum rank for recorded ties). Ranking treats observed ties as unresolved orders; it has no generative tie model. 0 held-out 2026 targets have a tied recorded time.
- Only one boat is hidden at a time. This assumes its absence would not materially change the others’ performances. Multiple simultaneous duty boats need joint treatment.
- This evaluates per-race points before season discards. Fractional expected points are estimates; the existing publishing pipeline still inserts a time and uses integer points. No live scoring rule, duty assignment or published result is changed.

## Limitations

The time-distribution version preserves the existing variance formula: it plugs in the estimated day parameters, rather than integrating their estimation uncertainty, and uses the prior boat state as the current estimator does. The ranking version assumes constant within-season strengths and discards time margins. Both posterior approximations can be wrong. Conclusions are conditional on the eligible finishers; they do not establish accuracy for actual absentees or non-finishers. Sparse-history cases are shown separately when available.

Per-boat signed errors are in `boat_summary.csv`; they are diagnostic, not separately validated boat-specific corrections. The sample contains few independent evenings.

## 2025 development data

| Method | MAE (points) | RMSE (points) | Signed error |
|---|---:|---:|---:|
| Single predicted time | 1.092 | 1.645 | +0.020 |
| Time distribution → expected points | 1.208 | 1.582 | -0.006 |
| Ranking model → expected points | 1.198 | 1.554 | -0.062 |

These scores use parameters selected on 2025 and are not held-out evidence.

## Reproduce

The required ranking model, configuration and saved results are included in
Onsdagsbanen at `scripts/ranking_odds.py`,
`analysis/models/ranking_odds_config.json` and
`data/odds_results_2025_2026.csv`. No adjacent repository is needed. These files
were copied unchanged from the original dashboard project. The stored manifest
records the original run before these paths were moved into this repository;
rerunning writes a new manifest for the local paths and current evaluator.

From the Onsdagsbanen directory:

```bash
OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/evaluate_redress_points.py
.venv/bin/python -m unittest discover -s tests -p "test_redress_points.py"
```

The script uses local saved results, makes no network requests, and writes only this analysis directory. Training settings are reused only when the 2025 data and time-model code hashes match. Use `--refit` to repeat optimization or `--sobol-power 14` for finer integration. Input/configuration/code hashes and optimizer diagnostics are in `manifest.json`.
