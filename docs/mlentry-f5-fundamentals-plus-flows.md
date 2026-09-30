# Feature challenger: ds_2026-09-29_6613b41a85c1d0afa763aaa2 (feature f_85c1d0af, 82 feats) vs baseline ds_2026-09-29_6613b41a35737d0ca763aaa2 (feature f_35737d0c, 57 feats)
same labels l_a763aaa2==l_a763aaa2, split s_b355f0e2==s_b355f0e2

## Stage A — conditional directional information (ATR-decile top/bottom 10%)
ConditionalTargetLift    challenger 1.5745 (worst fold 1.090) | baseline 1.4852 (worst 1.144) | Δ +0.0893
ConditionalStopReduction challenger 0.7044 (worst fold 0.251) | baseline 0.7530 (worst 0.509) | Δ -0.0485
by ATR decile CTL  ch [2.889, 1.964, 1.706, 1.504, 1.413, 1.308, 1.278, 1.238, 1.258, 1.194]
                   bl [2.38, 1.78, 1.648, 1.454, 1.339, 1.327, 1.295, 1.24, 1.182, 1.212]
by ATR decile CSR  ch [0.516, 0.607, 0.69, 0.711, 0.729, 0.715, 0.74, 0.752, 0.759, 0.824]
                   bl [0.617, 0.679, 0.745, 0.786, 0.78, 0.762, 0.783, 0.773, 0.779, 0.826]
Stage A: PASS (at least one improved); both-improved=True

## Stage B — policy_baseline_v1 harness (gate 95/20, vn_diff, K=5)
                 n  coverage  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  median_mfe  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
challenger  2272.0    0.9918       0.1818       1.2272     0.3019      0.8782        0.5163      0.0367     -0.0313      0.0100      0.0041           1.1111             1.0
baseline    2275.0    0.9898       0.1833       1.2374     0.2593      0.7543        0.5574      0.0346     -0.0268      0.0126      0.0067           1.0051             1.0

challenger fold lifts [np.float64(1.185), np.float64(1.324), np.float64(1.111), np.float64(1.247)] stop ratios [np.float64(0.791), np.float64(0.928), np.float64(0.872), np.float64(0.902)]
paired block bootstrap vs baseline (block 20): d_net10 -0.0026 CI [-0.0083,+0.0024] | d_target_rate -0.0016 CI [-0.0267,+0.0210] | d_stop_rate +0.0426 CI [+0.0149,+0.0708]
challenger CI: target_lift [1.108,1.335] stop_ratio [0.814,0.939] net10 [-0.0058,+0.0130]
promotion check (challenger @5): {"target_lift_at_5": {"value": 1.2272, "op": ">=", "threshold": 1.5, "pass": false}, "stop_ratio_at_5": {"value": 0.8782, "op": "<=", "threshold": 0.8, "pass": false}, "worst_fold_lift_at_5": {"value": 1.1111, "op": ">", "threshold": 1.0, "pass": true}, "coverage": {"value": 0.9918, "op": ">=", "threshold": 0.8, "pass": true}, "eligible": false}
