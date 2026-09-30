# Feature challenger: ds_2026-09-29_6613b41a3e76fba6a763aaa2 (feature f_3e76fba6, 70 feats) vs baseline ds_2026-09-29_6613b41a35737d0ca763aaa2 (feature f_35737d0c, 57 feats)
same labels l_a763aaa2==l_a763aaa2, split s_b355f0e2==s_b355f0e2

## Stage A — conditional directional information (ATR-decile top/bottom 10%)
ConditionalTargetLift    challenger 1.5200 (worst fold 1.027) | baseline 1.4852 (worst 1.144) | Δ +0.0349
ConditionalStopReduction challenger 0.7178 (worst fold 0.319) | baseline 0.7530 (worst 0.509) | Δ -0.0351
by ATR decile CTL  ch [2.649, 1.849, 1.723, 1.485, 1.348, 1.275, 1.29, 1.217, 1.194, 1.177]
                   bl [2.38, 1.78, 1.648, 1.454, 1.339, 1.327, 1.295, 1.24, 1.182, 1.212]
by ATR decile CSR  ch [0.545, 0.623, 0.69, 0.746, 0.733, 0.739, 0.758, 0.755, 0.757, 0.83]
                   bl [0.617, 0.679, 0.745, 0.786, 0.78, 0.762, 0.783, 0.773, 0.779, 0.826]
Stage A: PASS (at least one improved); both-improved=True

## Stage B — policy_baseline_v1 harness (gate 95/20, vn_diff, K=5)
                 n  coverage  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  median_mfe  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
challenger  2279.0    1.0000       0.1751       1.1819     0.3120      0.9074        0.5129      0.0335     -0.0326      0.0048     -0.0011           0.9521            0.75
baseline    2275.0    0.9898       0.1833       1.2374     0.2593      0.7543        0.5574      0.0346     -0.0268      0.0126      0.0067           1.0051            1.00

challenger fold lifts [np.float64(1.146), np.float64(1.228), np.float64(0.952), np.float64(1.366)] stop ratios [np.float64(0.797), np.float64(0.984), np.float64(0.962), np.float64(0.848)]
paired block bootstrap vs baseline (block 20): d_net10 -0.0077 CI [-0.0126,-0.0026] | d_target_rate -0.0084 CI [-0.0345,+0.0161] | d_stop_rate +0.0528 CI [+0.0278,+0.0795]
challenger CI: target_lift [1.068,1.295] stop_ratio [0.838,0.974] net10 [-0.0113,+0.0077]
promotion check (challenger @5): {"target_lift_at_5": {"value": 1.1819, "op": ">=", "threshold": 1.5, "pass": false}, "stop_ratio_at_5": {"value": 0.9074, "op": "<=", "threshold": 0.8, "pass": false}, "worst_fold_lift_at_5": {"value": 0.9521, "op": ">", "threshold": 1.0, "pass": false}, "coverage": {"value": 1.0, "op": ">=", "threshold": 0.8, "pass": true}, "eligible": false}
