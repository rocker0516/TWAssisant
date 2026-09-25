# RL Observation and State Engineering

Use this reference for reinforcement learning, offline RL, contextual bandits, or sequential decision policies.

## Start from the decision process

Define `(observation_t, action_t, reward_t, observation_t+1, done)` and confirm exact timing. Separate:

- latent environment state;
- observable information before `action_t`;
- action/execution result only known after the action;
- reward used for learning;
- episode termination versus truncation.

An observation should help approximate a Markov state without revealing future or post-action information.

## Observation families

- Current environment signals and causal histories or learned histories.
- Agent state: prior actions, inventory/position, cash/resources, risk budget, cooldowns, constraints.
- Context: regime, seasonality, task identity, opponent/market conditions.
- Uncertainty and quality: staleness, sensor missingness, confidence, coverage.
- Time: elapsed/remaining time only when the environment truly exposes it.

Prefer compact sufficient state. Use frame stacking, rolling summaries, RNNs, or transformers only when partial observability warrants them. Ensure training and serving reconstruct histories identically.

## Normalization and boundaries

- Fit static normalizers only on training experience, or use causal online statistics with frozen evaluation behavior.
- Bound heavy-tailed observations with defensible transforms; document clipping.
- Include masks for unavailable actions and missing observations when supported by the algorithm.
- Do not encode an arbitrary category ordering as a continuous magnitude.
- Ensure terminal observations and resets cannot leak episode outcomes.

## Reward and observation separation

Reward shaping changes the objective; it is not ordinary feature engineering. Evaluate shaped and unshaped objectives separately. Do not include realized reward components, next state, fill outcome, or future P&L in the pre-action observation.

## Offline RL checks

- Measure state-action coverage and behavior-policy support before trusting counterfactual actions.
- Avoid features that make coverage sparse without clear benefit.
- Split trajectories chronologically or by independent units; never split transitions randomly across the same trajectory.
- Use off-policy evaluation cautiously and report estimator assumptions and uncertainty.

## Evaluation

Compare against a random/constant policy, a domain heuristic, and the behavior policy where applicable. Report multiple seeds, learning curves, constraint violations, worst-case behavior, and performance under observation noise, delay, missingness, and regime shift. Use ablations by observation family; reject features that improve training return without robust evaluation gains.
