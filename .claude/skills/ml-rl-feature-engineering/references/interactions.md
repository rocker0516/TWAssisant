# Conditional and Interaction Feature Discovery

Use this reference when apparently weak features may become useful in combination, when a model underuses context, or when the task asks for feature crosses, gating, regimes, ratios, residuals, or interaction mining.

## Classify each feature's role

Do not apply one marginal-importance threshold to every feature. Assign the intended role before testing:

- **Direct signal:** predicts the target, value, or advantage directly.
- **Context/regime:** identifies when another relationship changes.
- **Gate/confirmation:** controls whether another signal is trustworthy or actionable.
- **Scale/benchmark:** makes values comparable through normalization, ratios, ranks, or residuals.
- **Risk/constraint:** modifies utility, exposure, drawdown, feasibility, or action safety.
- **Uncertainty/quality:** expresses reliability, freshness, coverage, or measurement error.
- **Agent state:** tells an RL policy its inventory, resources, prior actions, constraints, or progress.

A feature can have more than one role, but state the primary role and judge it accordingly. A weak direct effect does not invalidate a non-signal role.

## Generate bounded, mechanistic candidates

Prefer transformations whose semantics can be stated before seeing validation results:

- signal × context or regime;
- signal × gate or confirmation;
- signal ÷ risk or scale, with a safe denominator policy;
- entity value − peer, sector, market, seasonal, or model benchmark;
- short-horizon − long-horizon value;
- strength × freshness, confidence, or coverage;
- opportunity × feasibility or liquidity;
- proposed action × current agent exposure or constraint.

Consider thresholds, bins, splines, monotonic transforms, or model-learned interactions when the mechanism is nonlinear. Treat tree splits, SHAP interaction values, partial dependence, and neural representations as candidate generators, not proof.

Do not enumerate all pairs by default. Define an interaction budget and prioritize role-compatible pairs. Avoid ratios with unstable denominators, redundant algebraic variants, and interactions that are not available at decision time.

## Test the interaction ladder

For candidate features `A` and `B`, keep data, folds, preprocessing, model capacity, costs, and seeds fixed while comparing:

1. `M0 = baseline`
2. `M1 = baseline + A`
3. `M2 = baseline + B`
4. `M3 = baseline + A + B`
5. `M4 = baseline + A + B + interaction(A, B)`

Use the transformation implied by the hypothesis: product, gate, ratio, residual, rank, difference, or conditional model. Compare `M4` primarily with `M3`; this isolates the explicit interaction beyond the two main effects. Also compare with `M0` to ensure total value is practically meaningful.

For flexible models that can learn interactions without an explicit cross, compare both:

- a capacity-controlled model with raw `A` and `B`;
- the same model with the explicit mechanistic interaction.

An explicit feature is useful only if it improves stability, sample efficiency, interpretability, or deployability—not merely because it duplicates what the model already learns.

## Examine conditional effects

Estimate the effect of `A` inside predeclared slices of `B`, such as regime, volatility, liquidity, sector, size, data freshness, exposure, or action-history state. For every slice report:

- sample/trajectory and event counts;
- target or reward base rate;
- effect size and uncertainty;
- fold/time-period consistency;
- operational coverage and turnover/action frequency.

Beware Simpson's paradox: an aggregate near-zero effect can hide stable effects with opposite signs in different regimes. Conversely, a striking slice can be a low-count accident. Merge, revise, or reject slices that lack support.

## Control search bias

- Generate or rank interaction candidates using training data only.
- Select among candidates on validation data; use the final test once.
- In walk-forward evaluation, repeat candidate discovery and selection within each fold.
- Count tried interactions as part of the experiment budget and report the search space.
- Prefer two-way interactions. Add higher-order interactions only after their lower-order components show stable evidence and the higher-order mechanism is clear.
- Use multiple-testing controls, stability selection, or stronger confirmation thresholds when the search is broad.
- Stop expanding when additional candidates fail to add stable validation value or exceed the declared budget.

## Retention rule

Keep a weak marginal feature only when its intended joint, conditional, risk, or state contribution is reproducible across held-out folds and materially improves the decision after costs and constraints. Keep required agent-state or safety features even when predictive ablation is neutral if removing them makes the environment partially specified or unsafe; document that rationale separately from predictive value.

Drop or revise an interaction when it depends on one period, one entity group, a fragile threshold, a few extreme observations, leaked timing, excessive dimensionality, or model capacity that the baseline comparison did not control.

Record retained interactions as first-class feature contracts, including parent features, exact formula, availability, denominator/threshold policy, role, expected regime, and failure conditions.
