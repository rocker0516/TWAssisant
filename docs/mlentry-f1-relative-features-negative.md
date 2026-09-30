# Feature challenger: ds_2026-09-29_6613b41a8545eaeea763aaa2 (feature f_8545eaee, 68 feats) vs baseline ds_2026-09-29_6613b41a35737d0ca763aaa2 (feature f_35737d0c, 57 feats)
same labels l_a763aaa2==l_a763aaa2, split s_b355f0e2==s_b355f0e2

## Stage A — conditional directional information (ATR-decile top/bottom 10%)
ConditionalTargetLift    challenger 1.4995 (worst fold 1.129) | baseline 1.4852 (worst 1.144) | Δ +0.0144
ConditionalStopReduction challenger 0.7498 (worst fold 0.496) | baseline 0.7530 (worst 0.509) | Δ -0.0032
by ATR decile CTL  ch [2.433, 1.891, 1.695, 1.458, 1.319, 1.332, 1.244, 1.252, 1.187, 1.188]
                   bl [2.38, 1.78, 1.648, 1.454, 1.339, 1.327, 1.295, 1.24, 1.182, 1.212]
by ATR decile CSR  ch [0.594, 0.662, 0.726, 0.772, 0.782, 0.768, 0.786, 0.787, 0.782, 0.837]
                   bl [0.617, 0.679, 0.745, 0.786, 0.78, 0.762, 0.783, 0.773, 0.779, 0.826]
Stage A: PASS (at least one improved); both-improved=True

## Stage B — policy_baseline_v1 harness (gate 95/20, vn_diff, K=5)
                 n  coverage  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  median_mfe  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
challenger  2283.0    0.9959       0.1879       1.2686     0.2676      0.7785        0.5445      0.0370     -0.0282      0.0133      0.0075           1.1609             1.0
baseline    2275.0    0.9898       0.1833       1.2374     0.2593      0.7543        0.5574      0.0346     -0.0268      0.0126      0.0067           1.0051             1.0

challenger fold lifts [np.float64(1.161), np.float64(1.485), np.float64(1.203), np.float64(1.18)] stop ratios [np.float64(0.61), np.float64(0.766), np.float64(0.829), np.float64(0.869)]
paired block bootstrap vs baseline (block 20): d_net10 +0.0007 CI [-0.0033,+0.0050] | d_target_rate +0.0049 CI [-0.0122,+0.0229] | d_stop_rate +0.0082 CI [-0.0106,+0.0284]
challenger CI: target_lift [1.104,1.433] stop_ratio [0.702,0.846] net10 [-0.0026,+0.0168]
promotion check (challenger @5): {"target_lift_at_5": {"value": 1.2686, "op": ">=", "threshold": 1.5, "pass": false}, "stop_ratio_at_5": {"value": 0.7785, "op": "<=", "threshold": 0.8, "pass": true}, "worst_fold_lift_at_5": {"value": 1.1609, "op": ">", "threshold": 1.0, "pass": true}, "coverage": {"value": 0.9959, "op": ">=", "threshold": 0.8, "pass": true}, "eligible": false}
