"""情境路由矩陣端點（實驗）。

矩陣＝data/ctx_matrix.json（挖掘凍結產物）：每個 (group, signal, context) 格子
在 KPI（horizon 日、x% 門檻）下的樣本數／命中率／對照組差距／t 值／fold 穩定
度／holdout 對照，附 tier 判定（pass/watch/insufficient/fail）。純觀察層：
與排序無關，不碰 DB。

回傳時 cells 濾掉 tier=="fail"（前端不需要展示已淘汰的格子），另附全量 tier
統計（counts）供頁面顯示淘汰比例；groups/signals 由 cells 去重彙整。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..engines.ctx_matrix_loader import load_matrix

router = APIRouter(prefix="/ctx-matrix", tags=["ctx"])


class CtxCellOut(BaseModel):
    group: str
    group_kind: str
    signal: str
    family: str
    context: str
    n_picks: int
    n_days: int
    hit: float
    ctrl: float
    t_ctrl: float
    fold_ctrl: list[float | None]
    holdout_ctrl: float | None = None
    routing_delta: float
    tier: str


class ChainAuditOut(BaseModel):
    chain_id: str
    n_cells: int
    n_pass: int
    n_fail: int
    verdict: str


class GroupOut(BaseModel):
    group: str
    group_kind: str


class SignalOut(BaseModel):
    signal: str
    family: str


class CtxMatrixResponse(BaseModel):
    generated_at: str
    kpi: dict
    excluded_features: list[str] = []
    groups: list[GroupOut]
    signals: list[SignalOut]
    cells: list[CtxCellOut]
    counts: dict
    chain_audit: list[ChainAuditOut]


@router.get("", response_model=CtxMatrixResponse)
def ctx_matrix() -> CtxMatrixResponse:
    data = load_matrix()
    if data is None:
        raise HTTPException(status_code=404, detail="ctx_matrix.json 不存在（情境路由軌未啟用）")

    cells = data.get("cells", [])

    counts = {"pass": 0, "watch": 0, "insufficient": 0, "fail": 0}
    for c in cells:
        tier = c.get("tier")
        if tier in counts:
            counts[tier] += 1

    visible_cells = [c for c in cells if c.get("tier") != "fail"]

    groups_seen: dict[str, GroupOut] = {}
    signals_seen: dict[str, SignalOut] = {}
    for c in visible_cells:
        groups_seen.setdefault(
            c["group"], GroupOut(group=c["group"], group_kind=c["group_kind"]))
        signals_seen.setdefault(
            c["signal"], SignalOut(signal=c["signal"], family=c["family"]))

    return CtxMatrixResponse(
        generated_at=data.get("generated_at", ""),
        kpi=data.get("kpi", {}),
        excluded_features=data.get("excluded_features", []),
        groups=list(groups_seen.values()),
        signals=list(signals_seen.values()),
        cells=visible_cells,
        counts=counts,
        chain_audit=data.get("chain_audit", []),
    )
