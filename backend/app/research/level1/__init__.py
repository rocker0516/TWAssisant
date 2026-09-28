"""Level 1 股票推薦 ML 模型（FRS v1.0）共用元件。

規格：docs 之 Level 1 FRS——每日 T 收盤後對 Point-in-Time Universe 預測
「未來 N 日橫斷面報酬百分位」並排序，只做 Prediction/Ranking，不做交易。

模組：
- universe：Point-in-Time Research Universe（§3）
- targets：Target Generator（§5–7），Y = Percentile(R(i,t,N) | U_t)
- prices／targets_v3／features_v3／evaluation_v3：v3（隔日開盤進場淨報酬、q25 排名，
  設計 docs/superpowers/specs/2026-09-28-level1-v3-target-features-design.md）

基本面可得性一律走 app/services/pit_fundamentals.py，不得自寫期限規則。
"""
