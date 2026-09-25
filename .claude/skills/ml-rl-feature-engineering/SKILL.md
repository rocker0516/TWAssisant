---
name: ml-rl-feature-engineering
description: Design, audit, implement, and validate features for supervised ML, time-series models, or reinforcement learning. Use for feature ideation, leakage audits, observation/state design, feature selection, ablation, walk-forward evaluation, or debugging weak and unstable model features. Especially relevant to financial and other sequential data. Do not use for ordinary rule-based indicators unless they will feed a learned model or policy.
---

# ML / RL Feature Engineering

Engineer causal, deployable features whose value survives out-of-sample validation. Treat feature engineering as a hypothesis-and-measurement task, not an indicator inventory.

## Route the task

Identify the actual mode before editing code:

- **Tabular/supervised ML:** read [references/ml.md](references/ml.md).
- **Time-series, panel, market, or event data:** also read [references/time-series.md](references/time-series.md).
- **RL, offline RL, contextual bandits, or state/observation design:** read [references/rl.md](references/rl.md).
- **Weak features, feature combinations, conditional effects, or interaction discovery:** read [references/interactions.md](references/interactions.md).
- **Any empirical validation, feature selection, or comparison:** read [references/validation.md](references/validation.md).

Read only the references that match the task.

## Establish the feature contract first

Before proposing features, determine from the request and repository:

1. Decision or prediction time and the action that follows.
2. Target and horizon for ML, or action, reward, transition, and episode boundary for RL.
3. Which information is truly observable at decision time, including publication and ingestion delays.
4. Unit of observation, entity keys, timestamp semantics, and train/validation/test split.
5. Production constraints: latency, update cadence, missing data, compute, and reproducibility.

If a missing choice materially changes the result, state assumptions and keep implementation reversible. Do not silently invent target semantics.

For each implemented feature, make the contract recoverable from code or documentation:

| Field | Meaning |
|---|---|
| Name | Stable machine-readable identifier |
| Hypothesis | Why it should help the target or policy |
| Formula/source | Exact inputs and transformation |
| Availability | Earliest time it is knowable |
| Lookback | History consumed, including warm-up |
| Fit scope | Where learned statistics are fit |
| Missing policy | How unavailable values are represented |
| Leakage risk | Future, target, post-action, revision, or entity leakage |

## Non-negotiable invariants

- Compute every row using only information available at its decision time.
- Split before fitting imputers, scalers, encoders, selectors, PCA, bins, or learned embeddings.
- Fit learned transforms on each training fold only, then apply them unchanged to later folds.
- Preserve the real ordering and grouping structure; do not default to random row splits for sequential or grouped data.
- Distinguish event time, publication time, ingestion time, and effective time when they differ.
- Do not convert unknown-at-the-time values to zero unless zero has the intended domain meaning. Add missingness or staleness signals where useful.
- Keep feature generation shared between research, backtest/training, and inference. Avoid separate formulas that can drift.
- Prefer a small, interpretable causal baseline before high-dimensional interactions or automated search.
- Do not drop an observable feature solely because its univariate correlation, IC, single-feature model, SHAP value, or marginal importance is weak. First determine whether it serves as context, gate, scale, risk, uncertainty, or agent state and test its conditional or joint value.
- Never claim a feature works from in-sample importance alone.

## Workflow

### 1. Audit

Inspect schemas, feature code, label/reward construction, split logic, and inference path. Trace timestamps through joins and rolling windows. Identify leakage and train/serve skew before adding features.

### 2. Build a baseline

Record a simple reproducible baseline with the same split, costs, and evaluation protocol intended for the candidate features. For RL, include a simple heuristic or behavior-policy baseline.

### 3. Generate hypotheses

Group candidates by mechanism and role, not by library name. Distinguish direct signal from context, gating, scaling/benchmark, risk/constraint, uncertainty/quality, and RL agent-state features. Examples include level, change, trend, dispersion, relative position, interactions, regime/context, freshness, uncertainty, action history, and exposure/risk state.

For each candidate or family, state:

- the mechanism;
- why it is observable and causal;
- expected useful horizon;
- likely failure regime;
- added dimensionality and operational cost.

Reject candidates with no plausible mechanism unless the task is explicitly exploratory.

When weak-feature combinations are in scope, form a bounded set of mechanism-backed pairs such as signal × context, signal × gate, signal ÷ risk, entity − benchmark, short − long horizon, or strength × freshness. Do not create an unrestricted polynomial feature explosion.

### 4. Implement causally

Use pure, deterministic transformations where possible. Make ordering and grouping explicit. Preserve indexes/keys, define minimum history, and test boundary timestamps, missingness, and absence of future reads.

When modifying this repository, reuse its data access, engine, and storage boundaries. Do not bury feature computation inside API routes or LLM code.

### 5. Validate incrementally

Validate one feature family at a time against the unchanged baseline. For an interaction between `A` and `B`, preserve the same folds and model settings while comparing baseline, `+A`, `+B`, `+A+B`, and `+A+B+A×B` or the analogous gated/ratio/residual form. Check:

- schema, range, coverage, NaN/inf rate, and cardinality;
- temporal and cross-entity stability;
- leakage and duplicate/near-duplicate features;
- out-of-sample incremental value and confidence/dispersion across folds;
- marginal, conditional, joint, and interaction value where the feature's role warrants it;
- sensitivity to costs, delays, perturbations, and plausible regime shifts;
- inference parity and runtime/memory cost.

Use ablation and permutation only on held-out data. Treat model-native importance as diagnostic, not proof.

### 6. Decide and report

Classify each feature family as **keep**, **revise**, **drop**, or **insufficient evidence**. Preserve a weak marginal feature only when its conditional, joint, risk, or state value is stable out of sample and justifies the complexity. Prefer the smallest set that delivers stable incremental value. Report negative results instead of tuning them away.

The final response should include:

1. assumptions and decision-time definition;
2. leakage or validity findings;
3. implemented/proposed feature families, roles, interactions, and hypotheses;
4. validation protocol and results versus baseline;
5. retained/dropped features and rationale;
6. remaining risks and the next highest-value experiment.

## Boundaries

- Do not optimize a trading or RL policy solely on one backtest or one seed.
- Do not treat correlation, SHAP, or feature importance as causal evidence.
- Do not use the final test set for feature iteration.
- Do not recommend deep representation learning when data volume, stationarity, or latency does not justify it.
- Do not turn reward shaping terms into observations unless they are genuinely available before the action.
- State uncertainty; never imply that a profitable historical feature guarantees future returns.
