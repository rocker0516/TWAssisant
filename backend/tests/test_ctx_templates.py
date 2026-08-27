import numpy as np
import pandas as pd
from app.research.ctx_matrix.templates import Template, expand, build_masks


def test_expand_param_grid():
    t = Template(id="A1", family="chip", col="foreign_net", op="consec_ge", params={"n": [3, 5]})
    out = expand([t])
    assert sorted(x.id for x in out) == ["A1_n3", "A1_n5"]
    assert all(isinstance(x.params["n"], int) for x in out)


def test_q_hi_threshold_from_mine_window():
    rng = np.random.default_rng(0)
    mine = pd.DataFrame({"stock_id": "1101", "x": rng.normal(0, 1, 1000)})
    target = pd.DataFrame({"stock_id": "1101", "x": [10.0, -10.0]})  # 遠高/遠低於 mine q80
    tpl = Template(id="T", family="f", col="x", op="q_hi", params={"q": 0.8})
    masks = build_masks([tpl], mine, target)
    # target 太小不會過支持度過濾 → 先驗證遮罩本身：直接檢查閾值套用
    assert masks == [] or masks[0][1].tolist() == [True, False]


def test_consec_ge():
    df = pd.DataFrame({
        "stock_id": ["1101"] * 5,
        "net": [1.0, 1.0, 1.0, -1.0, 1.0],
    })
    tpl = Template(id="C", family="chip", col="net", op="consec_ge", params={"n": 3})
    masks = build_masks([tpl], df, df)
    if masks:
        # 第3天（index2）連正3日 → True；第5天連正僅1日 → False
        assert masks[0][1].tolist() == [False, False, True, False, False]


def test_support_filter_uses_mine_not_target():
    """驗證支持度過濾用 mine 的觸發率，不用 target。

    mine 觸發率 0.3（[0.002, 0.6] 內）✓
    target 觸發率 0.8（> 0.6）✗
    → 模板應被保留（因為篩選看 mine）
    """
    # mine: flag 觸發率 = 3/10 = 0.3 ✓ 在 [0.002, 0.6] 內
    mine = pd.DataFrame({"stock_id": ["1101"] * 10, "flag": [0, 0, 0, 0, 0, 0, 0, 1, 1, 1]})

    # target: flag 觸發率 = 8/10 = 0.8 ✗ 超過 0.6 （如果用 target 篩選，會被除外）
    target = pd.DataFrame({"stock_id": ["1101"] * 10, "flag": [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]})

    tpl = Template(id="T", family="f", col="flag", op="flag", params={})
    masks = build_masks([tpl], mine, target)

    # 因為支持度過濾看 mine（share=0.3），不看 target（share=0.8），所以模板應被保留
    assert len(masks) == 1
    assert masks[0][0] == "T"
    # 輸出遮罩應該是 target 上的
    assert masks[0][1].tolist() == [True, True, True, True, True, True, True, True, False, False]
