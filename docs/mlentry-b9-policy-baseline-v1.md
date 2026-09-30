# B9 report — policy_baseline_v1 (p_cb492c6d) on ds_2026-09-29_6613b41a35737d0ca763aaa2
eval rows=834,399 days=488 | market target=0.1481 stop=0.3438 ret10=+0.0018 net10=-0.0040 (cost_rt=0.00585)

## @K=1
                 n  coverage  rec_per_day_mean  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  mean_mfe  median_mfe  mean_mae  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
policy       481.0    0.9857            0.9857       0.2162       1.4597     0.2245      0.6531        0.5593    0.0694      0.0331   -0.0363     -0.0231      0.0168       0.011           1.0565             1.0
atr_topk     488.0    1.0000            1.0000       0.3320       2.2411     0.6107      1.7762        0.0574    0.1468      0.1050   -0.1023     -0.0872      0.0148       0.009           1.7656             1.0
market    834399.0    1.0000         1709.8340       0.1481       1.0000     0.3438      1.0000        0.5081    0.0586         NaN   -0.0489         NaN      0.0018      -0.004              NaN             NaN

policy fold lifts [np.float64(1.396), np.float64(1.956), np.float64(1.056), np.float64(1.293)] stop ratios [np.float64(0.528), np.float64(0.578), np.float64(0.758), np.float64(0.721)]
block bootstrap (main block=20, n=2000, CI 0.95):
  net10          mean +0.0110  CI [+0.0003, +0.0205]
  target_lift    mean +1.4613  CI [+1.1995, +1.7478]
  stop_ratio     mean +0.6521  CI [+0.5259, +0.7733]
  d_net10        mean +0.0018  CI [-0.0210, +0.0255]
  d_target_rate  mean -0.1160  CI [-0.1818, -0.0522]
  d_stop_rate    mean -0.3849  CI [-0.4450, -0.3219]
robustness (net10 CI lo/hi by block): 10D [+0.0008,+0.0210], 20D [+0.0003,+0.0205], 40D [+0.0014,+0.0195]
robustness (target_lift CI lo by block): 10D 1.180, 20D 1.200, 40D 1.206
robustness (stop_ratio CI hi by block): 10D 0.767, 20D 0.773, 40D 0.779

## @K=3
                 n  coverage  rec_per_day_mean  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  mean_mfe  median_mfe  mean_mae  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
policy      1419.0    0.9918            2.9078       0.1868       1.2607     0.2467      0.7174        0.5666    0.0670      0.0343   -0.0394     -0.0264      0.0136      0.0077           0.9870            0.75
atr_topk    1464.0    1.0000            3.0000       0.3559       2.4025     0.6031      1.7543        0.0410    0.1375      0.1033   -0.0986     -0.0858      0.0047     -0.0012           1.9565            1.00
market    834399.0    1.0000         1709.8340       0.1481       1.0000     0.3438      1.0000        0.5081    0.0586         NaN   -0.0489         NaN      0.0018     -0.0040              NaN             NaN

policy fold lifts [np.float64(1.064), np.float64(1.667), np.float64(0.987), np.float64(1.213)] stop ratios [np.float64(0.626), np.float64(0.625), np.float64(0.819), np.float64(0.785)]
block bootstrap (main block=20, n=2000, CI 0.95):
  net10          mean +0.0077  CI [-0.0022, +0.0168]
  target_lift    mean +1.2614  CI [+1.0646, +1.4637]
  stop_ratio     mean +0.7169  CI [+0.6301, +0.7937]
  d_net10        mean +0.0089  CI [-0.0036, +0.0222]
  d_target_rate  mean -0.1689  CI [-0.2082, -0.1257]
  d_stop_rate    mean -0.3561  CI [-0.4049, -0.3067]
robustness (net10 CI lo/hi by block): 10D [-0.0014,+0.0163], 20D [-0.0022,+0.0168], 40D [-0.0012,+0.0166]
robustness (target_lift CI lo by block): 10D 1.075, 20D 1.065, 40D 1.051
robustness (stop_ratio CI hi by block): 10D 0.793, 20D 0.794, 40D 0.794

## @K=5
                 n  coverage  rec_per_day_mean  target_rate  target_lift  stop_rate  stop_ratio  timeout_rate  mean_mfe  median_mfe  mean_mae  median_mae  mean_ret10  mean_net10  worst_fold_lift  pos_fold_ratio
policy      2263.0    0.9918            4.6373       0.1825        1.232     0.2572      0.7481        0.5603    0.0668      0.0343   -0.0411      -0.027      0.0125      0.0066           1.0175             1.0
atr_topk    2440.0    1.0000            5.0000       0.3480        2.349     0.6094      1.7726        0.0426    0.1353      0.1014   -0.0966      -0.084      0.0066      0.0008           2.0328             1.0
market    834399.0    1.0000         1709.8340       0.1481        1.000     0.3438      1.0000        0.5081    0.0586         NaN   -0.0489         NaN      0.0018     -0.0040              NaN             NaN

policy fold lifts [np.float64(1.017), np.float64(1.515), np.float64(1.047), np.float64(1.26)] stop ratios [np.float64(0.69), np.float64(0.675), np.float64(0.831), np.float64(0.794)]
block bootstrap (main block=20, n=2000, CI 0.95):
  net10          mean +0.0066  CI [-0.0030, +0.0154]
  target_lift    mean +1.2332  CI [+1.0698, +1.4028]
  stop_ratio     mean +0.7480  CI [+0.6781, +0.8114]
  d_net10        mean +0.0058  CI [-0.0045, +0.0166]
  d_target_rate  mean -0.1654  CI [-0.1990, -0.1302]
  d_stop_rate    mean -0.3517  CI [-0.3913, -0.3072]
robustness (net10 CI lo/hi by block): 10D [-0.0025,+0.0149], 20D [-0.0030,+0.0154], 40D [-0.0025,+0.0154]
robustness (target_lift CI lo by block): 10D 1.080, 20D 1.070, 40D 1.062
robustness (stop_ratio CI hi by block): 10D 0.808, 20D 0.811, 40D 0.808

## Coverage / NO_TRADE sensitivity (all dev days, incl. non-evaluable rows in gating)
{
  "coverage": 0.9918032786885246,
  "no_trade_pct": 0.00819672131147541,
  "median_candidates_per_day": 9.5,
  "median_recommendations_per_day": 5.0,
  "qualified_count_quantiles": {
    "0.05": 3.0,
    "0.25": 6.0,
    "0.5": 9.5,
    "0.75": 15.0,
    "0.95": 28.6
  }
}

## Promotion contract check (@5)
{
  "target_lift_at_5": {
    "value": 1.232,
    "op": ">=",
    "threshold": 1.5,
    "pass": false
  },
  "stop_ratio_at_5": {
    "value": 0.7481,
    "op": "<=",
    "threshold": 0.8,
    "pass": true
  },
  "worst_fold_lift_at_5": {
    "value": 1.0175,
    "op": ">",
    "threshold": 1.0,
    "pass": true
  },
  "eligible": false
}
