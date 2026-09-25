# Time-Series and Financial Features

Use this reference when observations have temporal order, delayed publication, multiple entities, or market/backtest semantics.

## Define time precisely

For every source, establish:

- observation/event time;
- publication/release time;
- time received by the system;
- earliest eligible decision time;
- whether later revisions exist.

Use point-in-time/as-of joins. Lag a source to its real availability, not merely to the next stored row. A daily bar's close, volume, and indicators derived from them are unavailable to a decision made before that close.

## Useful families

- Multi-horizon returns, differences, slopes, and acceleration.
- Rolling volatility, downside variation, range, drawdown, and tail events.
- Distance from rolling extrema, means, support/resistance, or anchored references.
- Volume/liquidity, turnover, spread proxies, price impact, and data staleness.
- Cross-sectional rank or residual versus sector/market, computed using the contemporaneously eligible universe.
- Breadth, dispersion, correlation, beta, and regime/context variables.
- Event recency, surprise versus prior expectation, and decay since publication.
- Fundamental quality/growth/valuation using the actual report availability date.

Favor dimensionless or relative forms when instruments differ greatly in price, scale, or volatility. Fit normalization on past data only; for changing distributions, expanding or rolling past-only normalization may be more appropriate than one global scaler.

## Financial leakage and bias checklist

- Survivorship and delisting bias.
- Universe membership reconstructed with today's constituents.
- Adjusted prices or corporate actions used before their effective availability.
- Fundamentals aligned to fiscal period end instead of announcement time.
- Same-close features used for an assumed same-close fill.
- Forward-filled stale values without age indicators.
- Cross-sectional transforms using names not eligible at that timestamp.
- Overlapping labels leaking across fold boundaries; purge and embargo where needed.
- Hyperparameters or features selected on the final test period.
- Ignored transaction costs, slippage, liquidity, borrow limits, and execution delay.

## Validation

Use expanding-window or rolling walk-forward evaluation that matches retraining. Keep a final untouched chronological test. For panels, preserve both temporal and entity structure. Compare performance by horizon, regime, liquidity bucket, sector, and calendar period.

For trading-oriented features, evaluate both predictive metrics and decision outcomes after costs. Prefer stable rank IC, calibration, turnover-adjusted utility, and drawdown behavior over a single headline return.
