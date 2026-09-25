# Supervised ML Feature Engineering

Use this reference for classification, regression, ranking, anomaly detection, and tabular prediction.

## Candidate families

Choose only families supported by a domain hypothesis:

- **Level and scale:** raw level, log transform, robust scale, distance from a meaningful reference.
- **Change:** first difference, percentage/log return, acceleration, change over multiple horizons.
- **Distribution:** rolling mean, median, quantiles, dispersion, skew, tail frequency, robust z-score.
- **Relative context:** within-group rank, percentile, deviation from peer/market/seasonal baseline.
- **Interactions:** ratios or products with a mechanistic interpretation; avoid blind polynomial explosions.
- **Categorical:** frequency, hashing, or fold-safe target encoding. Handle unseen categories explicitly.
- **Missingness/freshness:** missing flags, age since last observation, source quality, revision status.
- **Uncertainty:** sample size, confidence width, disagreement, volatility, or measurement reliability.

## Leakage traps

- Target encoding, feature selection, scaling, imputation, or PCA fitted before splitting.
- Aggregate statistics that include validation/test rows or future records from the same entity.
- IDs, status codes, workflow fields, or timestamps created after the outcome.
- Duplicate entities or related samples split across folds.
- Labels or proxies embedded in filenames, joins, row ordering, or missingness patterns.
- Retrospectively revised data used as though it were known historically.

## Selection order

1. Remove invalid and unavailable features.
2. Remove constants, identifiers, duplicates, and pathological missingness.
3. Assign remaining candidates a role: direct signal, context, gate, scale/benchmark, risk/constraint, uncertainty/quality, or agent state.
4. Compare feature families with fold-consistent ablation, including conditional and interaction tests for non-signal roles.
5. Check redundancy and stability before using embedded importance or regularization.
6. Tune the model only after a defensible feature set exists.

Do not require a context, gate, scale, risk, or state feature to predict the target well by itself. Judge it by the stable incremental value it contributes in its intended role.

Use metrics matched to the decision: calibration and threshold utility may matter more than AUROC; ranking metrics may matter more than classification accuracy. Report the metric distribution across folds, not only its mean.
