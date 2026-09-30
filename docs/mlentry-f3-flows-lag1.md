# Feature challenger: ds_2026-09-29_6613b41aaa23a13da763aaa2 (feature f_aa23a13d, 69 feats) vs baseline ds_2026-09-29_6613b41a35737d0ca763aaa2 (feature f_35737d0c, 57 feats)
same labels l_a763aaa2==l_a763aaa2, split s_b355f0e2==s_b355f0e2

## Stage A — conditional directional information (ATR-decile top/bottom 10%)
ConditionalTargetLift    challenger 1.5557 (worst fold 1.149) | baseline 1.4852 (worst 1.144) | Δ +0.0705
ConditionalStopReduction challenger 0.7282 (worst fold 0.296) | baseline 0.7530 (worst 0.509) | Δ -0.0247
by ATR decile CTL  ch [2.69, 1.893, 1.655, 1.524, 1.406, 1.352, 1.319, 1.281, 1.227, 1.216]
                   bl [2.38, 1.78, 1.648, 1.454, 1.339, 1.327, 1.295, 1.24, 1.182, 1.212]
by ATR decile CSR  ch [0.561, 0.642, 0.719, 0.765, 0.735, 0.742, 0.756, 0.755, 0.783, 0.822]
                   bl [0.617, 0.679, 0.745, 0.786, 0.78, 0.762, 0.783, 0.773, 0.779, 0.826]
Stage A: PASS (at least one improved); both-improved=True

## Stage B — policy_baseline_v1 harness (gate 95/20, vn_diff, K=5)
                 n  coverage  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  median_mfe  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
challenger  2174.0    0.9959       0.1960       1.3229     0.2732      0.7947        0.5308      0.0351     -0.0292      0.0127      0.0068           1.0475             1.0
baseline    2275.0    0.9898       0.1833       1.2374     0.2593      0.7543        0.5574      0.0346     -0.0268      0.0126      0.0067           1.0051             1.0

challenger fold lifts [np.float64(1.21), np.float64(1.605), np.float64(1.048), np.float64(1.29)] stop ratios [np.float64(0.716), np.float64(0.829), np.float64(0.789), np.float64(0.822)]
paired block bootstrap vs baseline (block 20): d_net10 +0.0001 CI [-0.0035,+0.0037] | d_target_rate +0.0127 CI [-0.0074,+0.0344] | d_stop_rate +0.0141 CI [-0.0053,+0.0338]
challenger CI: target_lift [1.133,1.500] stop_ratio [0.722,0.861] net10 [-0.0022,+0.0153]
promotion check (challenger @5): {"target_lift_at_5": {"value": 1.3229, "op": ">=", "threshold": 1.5, "pass": false}, "stop_ratio_at_5": {"value": 0.7947, "op": "<=", "threshold": 0.8, "pass": true}, "worst_fold_lift_at_5": {"value": 1.0475, "op": ">", "threshold": 1.0, "pass": true}, "coverage": {"value": 0.9959, "op": ">=", "threshold": 0.8, "pass": true}, "eligible": false}
