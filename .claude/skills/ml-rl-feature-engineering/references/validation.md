# Validation and Experiment Design

## Experiment contract

Freeze before comparing candidates:

- dataset snapshot and universe;
- target/reward and horizon;
- split/folds and gap/embargo;
- preprocessing fit scope;
- baseline model/policy and main hyperparameters;
- primary and guardrail metrics;
- seeds, costs, delay, and stopping rule.

Change one feature family at a time unless testing a predeclared interaction. Save fold-level results and configuration so the experiment can be reproduced.

Discover candidate interactions only inside training data. Use validation data to select among the predeclared candidates and keep the final test untouched. For walk-forward experiments, repeat this separation within each fold rather than selecting interactions using the full history.

## Feature diagnostics

Check, by split and relevant group:

- coverage, missing/inf rate, unique count, and invalid ranges;
- distribution drift and population stability;
- correlation/redundancy and implausibly high target association;
- lagged versus contemporaneous relationships;
- conditional effects by predeclared regime/group, with coverage and uncertainty for every slice;
- sensitivity to small timestamp shifts, delayed availability, and noise;
- training-serving parity and deterministic recomputation.

## Evidence ladder

Increasingly persuasive evidence:

1. Plausible mechanism and correct availability.
2. Clean feature diagnostics.
3. Incremental held-out value across folds.
4. Ablation confirms the family is responsible.
5. Robustness across time, groups, seeds, costs, and perturbations.
6. Final untouched test or prospective shadow evaluation.

Do not skip levels 1-2 merely because a flexible model reports high importance.

## Keep/drop rubric

Keep a feature family when it adds stable out-of-sample decision value, is operationally reproducible, and its gain justifies complexity. Revise it when the mechanism is sound but availability, scaling, or robustness is weak. Drop it when gains are isolated, unstable, redundant, leakage-dependent, or too costly. Mark insufficient evidence when sample size or coverage cannot support a conclusion.

Weak marginal performance is not by itself a drop criterion for context, gating, scaling, risk, uncertainty, or agent-state features. Require evidence that the intended conditional or joint contribution is absent or unstable.

Report effect sizes and fold dispersion. Avoid false precision and broad claims from a single run.
