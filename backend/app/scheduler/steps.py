"""Pipeline steps。P0 只有 FetchStep（抓資料落庫）。

後續階段在此新增 IndicatorStep / SectorStep / NewsStep / ScoringStep /
ExitStep / NotifyStep，再加進 run.py 的 step 清單。
（LLM 翻白話已改端點首讀懶生成 llm/lazy.py + news_digest.py，不再是 pipeline step。）
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, func, select

from ..engines.corners import CornerEngine
from ..engines.exit_engine import ExitEngine, ExitStatus
from ..engines.indicators import IndicatorEngine
from ..engines.news_engine import NewsEngine
from ..engines.poppability import PoppabilityEfficacyEngine
from ..engines.scoring import ScoringEngine
from ..engines.sector_engine import SectorEngine
from ..engines.signal_log import SignalLogEngine
from ..notify import build_daily_message, send_discord
from ..sources import registry
from ..sources.base import SourceError
from ..storage import models, repositories as repo
from .pipeline import PipelineContext, PipelineStep


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → upsert 用 dicts，pandas NA/NaN 轉成 None（SQLite 友善）。"""
    if df.empty:
        return []
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict("records")


class FetchStep(PipelineStep):
    """抓全市場盤後資料落庫。各來源各自 try，單一來源失敗不中斷整步。"""

    name = "fetch"
    required = True

    # 增量抓（用各表 max_date 當起點）；冷啟動回補天數。
    # P0 範圍：全市場日K + 三大法人 + 融資券（皆 TWSE 官方免費全市場）。
    # 估值/月營收/季財報屬長線軌（P1）才需要，屆時加進此清單即可。
    _INCREMENTAL = [
        ("price", "price", "DailyPriceRepository", "fetch_prices", 150),
        ("institutional", "chip", "InstitutionalRepository", "fetch_institutional", 90),
        ("margin", "chip", "MarginRepository", "fetch_margin", 90),
        ("short_lending", "chip", "ShortLendingRepository", "fetch_short_lending", 90),
        ("day_trading", "chip", "DayTradingRepository", "fetch_day_trading", 90),
        ("valuation", "fundamental", "ValuationRepository", "fetch_valuation", 90),
    ]
    # 快照來源（回最新期，日期參數忽略，靠 upsert 去重）
    # holding：TDCC 集保股權分散僅回最新一週，靠每週 upsert 累積歷史。
    # financials：已改走 MOPS 累計制差分（回最近 2 個已結束季度的「單季」值），
    #             歷史由 scripts.backfill_fundamentals 回補。
    _WINDOW = [
        ("revenue", "fundamental", "RevenueMonthlyRepository", "fetch_revenue_monthly", 1),
        ("financials", "fundamental", "FinancialQuarterRepository", "fetch_financials", 1),
        ("holding", "holding", "ShareholdingRepository", "fetch_holding_distribution", 1),
        # insider：董監持股月快照（t187ap11），PK=(stock_id,year,month) 靠 upsert 累積
        ("insider", "fundamental", "InsiderHoldingRepository", "fetch_insider_holdings", 1),
    ]
    # 市場級資料（無 stock_id，PK=date，不過濾股號）：全市場三大法人總表 + 加權指數。
    # (key, source_name, repo_cls, method, lookback)
    _MARKET = [
        ("inst_market", "twse", "InstitutionalMarketTotalRepository", "fetch_institutional_market_total", 90),
        ("market_index", "twse", "MarketIndexRepository", "fetch_index", 150),
        ("derivatives", "taifex", "MarketDerivativesRepository", "fetch_market_derivatives", 90),
    ]

    def run(self, ctx: PipelineContext) -> dict:
        session = ctx.session
        td = ctx.trading_date
        results: dict[str, dict] = {}

        # 1) 主檔（universe）— 先抓，後續落庫要靠它過濾未知股號 + FK
        results["universe"] = self._fetch_universe(session)

        known_ids = self._known_ids(session)
        ctx.shared["stock_ids"] = sorted(known_ids)

        # 1b) ETF 身分資料（追蹤指數/類型/含國外/發行單位數，TWSE 全快照）
        results["etf_profile"] = self._fetch_etf_profiles(session, known_ids)

        # 1c) 公司基本資料（董事長/股本/發行股數，上市+上櫃全快照）
        results["company_profile"] = self._fetch_company_profiles(session, known_ids)

        # 1d) 產業價值鏈成員（ic.tpex.org.tw，慢變 → 7 天內抓過即跳過）
        results["industry_chain"] = self._fetch_industry_chains(session, known_ids)

        # 2) 增量抓
        for key, capability, repo_cls, method, lookback in self._INCREMENTAL:
            results[key] = self._fetch_dataset(
                session, key, capability, repo_cls, method, td, known_ids,
                incremental=True, lookback=lookback,
            )

        # 3) 固定視窗抓
        for key, capability, repo_cls, method, lookback in self._WINDOW:
            results[key] = self._fetch_dataset(
                session, key, capability, repo_cls, method, td, known_ids,
                incremental=False, lookback=lookback,
            )

        # 3b) 基本面首次入庫日（append-only 側表；Level 1 PIT 可得性下界，
        #     語意見 app/services/pit_fundamentals.py）
        results["fundamental_first_seen"] = self._record_fundamental_first_seen(session)

        # 4) 市場級增量抓（無 stock_id，不過濾股號）
        for key, source_name, repo_cls, method, lookback in self._MARKET:
            results[key] = self._fetch_market_dataset(
                session, source_name, repo_cls, method, td, lookback,
            )

        ok = sum(1 for r in results.values() if r.get("status") == "ok")
        return {"datasets": results, "ok_count": ok, "total": len(results)}

    # ── 主檔 ──

    def _fetch_universe(self, session) -> dict:
        try:
            src = registry.provider("universe")
            df = src.fetch_universe()
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "empty"}

        sector_repo = repo.SectorRepository()
        names = sorted({n for n in df["sector_name"].dropna().tolist() if n})
        name_to_id = sector_repo.ensure(session, names)

        rows = []
        for rec in _records(df):
            rows.append(
                {
                    "id": rec["id"],
                    "name": rec["name"],
                    "sector_id": name_to_id.get(rec.get("sector_name")),
                    "market": rec.get("market"),
                    "industry_category": rec.get("industry_category"),
                    "is_etf": bool(rec.get("is_etf")),
                    "listed_date": rec.get("listed_date"),
                }
            )
        n = repo.StockRepository().upsert_many(session, rows)
        session.flush()
        return {"status": "ok", "rows": n, "sectors": len(name_to_id)}

    def _known_ids(self, session) -> set[str]:
        return set(session.execute(select(models.Stock.id)).scalars().all())

    def _record_fundamental_first_seen(self, session) -> dict:
        """基本面新列補記首次入庫日（失敗不擋 pipeline，PIT 退回法定期限規則）。"""
        try:
            from app.services import pit_fundamentals

            out = pit_fundamentals.record_first_seen(session)
            session.flush()
            return {"status": "ok", **out}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": str(exc)}

    # ── ETF 身分資料（TWSE 全快照，僅落已知股號）──

    def _fetch_etf_profiles(self, session, known_ids: set[str]) -> dict:
        try:
            src = registry.get_source("twse")
            df = src.fetch_etf_profiles()
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "empty"}
        df = df[df["stock_id"].astype(str).isin(known_ids)]
        n = repo.EtfProfileRepository().upsert_many(session, _records(df))
        session.flush()
        return {"status": "ok", "rows": n}

    # ── 公司基本資料（上市 t187ap03_L + 上櫃 mopsfin_t187ap03_O，全快照）──

    def _fetch_company_profiles(self, session, known_ids: set[str]) -> dict:
        frames = []
        errors = []
        for source_name in ("twse", "tpex"):
            try:
                frames.append(registry.get_source(source_name).fetch_company_profiles())
            except SourceError as exc:
                errors.append(f"{source_name}:{exc.reason}")
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            return {"status": "error", "reason": ";".join(errors)} if errors else {"status": "empty"}
        df = df[df["stock_id"].astype(str).isin(known_ids)]
        n = repo.CompanyProfileRepository().upsert_many(session, _records(df))
        session.flush()
        return {"status": "ok", "rows": n, "errors": errors or None}

    # ── 產業價值鏈（40 鏈頁逐頁爬，慢變資料 7 天更新一次）──

    def _fetch_industry_chains(self, session, known_ids: set[str]) -> dict:
        from datetime import datetime

        last = session.execute(
            select(func.max(models.IndustryChainMember.updated_at))
        ).scalar()
        if last is not None and (datetime.now() - last).days < 7:
            return {"status": "ok", "rows": 0, "note": "up_to_date"}
        try:
            df = registry.get_source("tpex_ic").fetch_industry_chains()
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}
        if df.empty:
            return {"status": "empty"}
        df = df[df["stock_id"].astype(str).isin(known_ids)]
        # 全快照 → 先清後寫，成員異動不殘留
        session.execute(delete(models.IndustryChainMember))
        n = repo.IndustryChainRepository().upsert_many(session, _records(df))
        session.flush()
        return {"status": "ok", "rows": n}

    # ── 通用資料集抓取 ──

    def _fetch_dataset(
        self, session, key, capability, repo_cls_name, method, td, known_ids,
        *, incremental: bool, lookback: int,
    ) -> dict:
        try:
            src = registry.provider(capability)
            repository = getattr(repo, repo_cls_name)()

            if incremental:
                last = repository.max_date(session)
                start = (last + timedelta(days=1)) if last else (td - timedelta(days=lookback))
                if start > td:
                    return {"status": "ok", "rows": 0, "note": "up_to_date"}
            else:
                start = td - timedelta(days=lookback)

            df = getattr(src, method)(start, td, None)
            if df.empty:
                return {"status": "empty", "from": start.isoformat(), "to": td.isoformat()}

            df = df[df["stock_id"].astype(str).isin(known_ids)]
            n = repository.upsert_many(session, _records(df))
            session.flush()
            return {"status": "ok", "rows": n, "from": start.isoformat(), "to": td.isoformat()}
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}

    def _fetch_market_dataset(
        self, session, source_name, repo_cls_name, method, td, lookback: int,
    ) -> dict:
        """市場級資料集（PK=date，無 stock_id）增量抓。起點＝max_date+1（冷啟回補 lookback）。"""
        try:
            src = registry.get_source(source_name)
            repository = getattr(repo, repo_cls_name)()
            last = repository.max_date(session)
            start = (last + timedelta(days=1)) if last else (td - timedelta(days=lookback))
            if start > td:
                return {"status": "ok", "rows": 0, "note": "up_to_date"}
            df = getattr(src, method)(start, td, None)
            if df.empty:
                return {"status": "empty", "from": start.isoformat(), "to": td.isoformat()}
            n = repository.upsert_many(session, _records(df))
            session.flush()
            return {"status": "ok", "rows": n, "from": start.isoformat(), "to": td.isoformat()}
        except SourceError as exc:
            return {"status": "error", "reason": exc.reason}


class IndicatorStep(PipelineStep):
    """daily_prices → indicators（P1）。"""

    name = "indicator"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return IndicatorEngine().run(ctx.session, ctx.trading_date)


class SectorStep(PipelineStep):
    """類股強弱/方向/輪動 → sector_daily（P3）。需在 Scoring 前。"""

    name = "sector"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return SectorEngine().run(ctx.session, ctx.trading_date)


class NewsStep(PipelineStep):
    """重訊/事件分類 → events（P4，非必要）。掛了用既有事件不影響選股。"""

    name = "news"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return NewsEngine().run(ctx.session, ctx.trading_date)


class TargetPriceStep(PipelineStep):
    """FactSet 共識目標價（鉅亨 tw_forecast）。首次自動回補 180 天，之後增量。非必要。"""

    name = "target_price"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        from ..sources.cnyes_forecast import CnyesForecastSource

        session = ctx.session
        known = set(
            session.execute(
                select(models.TargetPrice.news_id).where(models.TargetPrice.news_id.isnot(None))
            ).scalars().all()
        )
        min_date = ctx.trading_date - timedelta(days=180)
        src = CnyesForecastSource()
        try:
            rows = src.fetch_target_prices(known, min_date)
        except SourceError as exc:
            return {"ok": False, "reason": exc.reason}
        finally:
            src.close()
        # 只留 universe 內股票（FK 保護）；同日同股取 news_id 較大者
        valid_ids = set(session.execute(select(models.Stock.id)).scalars().all())
        best: dict[tuple[str, object], dict] = {}
        for r in rows:
            if r["stock_id"] not in valid_ids:
                continue
            key = (r["stock_id"], r["date"])
            if key not in best or (r.get("news_id") or 0) > (best[key].get("news_id") or 0):
                best[key] = r
        n = repo.TargetPriceRepository().upsert_many(session, list(best.values()))
        return {"ok": True, "rows": n}


class AttentionStep(PipelineStep):
    """注意/處置股名單（TWSE+TPEX 官方公告）。抓近 7 日窗增量 upsert。非必要。"""

    name = "attention"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        from ..sources import attention

        session = ctx.session
        try:
            rows = attention.fetch_range(ctx.trading_date - timedelta(days=7), ctx.trading_date)
        except Exception as exc:  # 官方端點偶發異常，明日再補（7 日窗自帶重疊）
            return {"ok": False, "reason": str(exc)[:120]}
        valid_ids = set(session.execute(select(models.Stock.id)).scalars().all())
        rows = [r for r in rows if r["stock_id"] in valid_ids]
        n = repo.AttentionRepository().upsert_many(session, rows)
        return {"ok": True, "rows": n}


class MLConsensusStep(PipelineStep):
    """ML 共識確認器日更推論（scripts/build_ml_consensus.py infer）。

    子行程執行（特徵重建吃記憶體，不進 uvicorn 行程）；模型檔不存在或失敗
    只記 reason——推薦卡的共識徽章遇日期不符自動隱藏，無害降級。非必要。
    """

    name = "ml_consensus"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        import subprocess
        import sys as _sys
        from pathlib import Path

        base = Path(__file__).resolve().parents[2]
        if not (base / "data" / "ml_consensus_model.joblib").exists():
            return {"ok": False, "reason": "model not trained (run scripts/build_ml_consensus.py train)"}
        r = subprocess.run(
            [_sys.executable, str(base / "scripts" / "build_ml_consensus.py"), "infer"],
            cwd=base, capture_output=True, text=True, timeout=1200,
        )
        if r.returncode != 0:
            return {"ok": False, "reason": (r.stderr or r.stdout)[-200:]}
        return {"ok": True}


class Level1PredictStep(PipelineStep):
    """Level 1 每日推薦（scripts/level1_predict.py，含 Ledger 成熟回填）。

    子行程執行（同 MLConsensusStep 慣例：特徵重建吃記憶體，不進 uvicorn 行程；
    lightgbm 另有 OpenMP DLL 載入順序坑，隔離在子行程最安全）。

    子行程要寫同一顆 SQLite（level1_predictions），而 pipeline 的 session 從
    run_row flush 起就持有寫鎖直到整條結束——不先放鎖子行程會卡 busy_timeout
    30 秒後報 database is locked。pipeline 冪等（增量+upsert），中途 commit 無害，
    故啟動子行程前先 commit 釋放寫鎖。失敗只記 reason 不擋盤後流程。
    """

    name = "level1_predict"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        import subprocess
        import sys as _sys
        from pathlib import Path

        ctx.session.commit()  # 釋放 SQLite 寫鎖，讓子行程能寫 ledger
        base = Path(__file__).resolve().parents[2]
        r = subprocess.run(
            [_sys.executable, "-m", "scripts.level1_predict"],
            cwd=base, capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            return {"ok": False, "reason": (r.stderr or r.stdout)[-200:]}
        tail = [ln for ln in r.stdout.strip().splitlines() if ln][-4:]
        return {"ok": True, "log_tail": tail}


class ScoringStep(PipelineStep):
    """雙軌評分 → scores（P1，含類股修正）。"""

    name = "scoring"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return ScoringEngine().run(ctx.session, ctx.trading_date)


class SignalLogStep(PipelineStep):
    """名單進出 → signal_log（append-only）。

    緊接 ScoringStep：它只依賴 scores.passed，而 ScoringStep 之後沒有任何 step
    會再動那個欄位（MLConsensus/Corner 都是純標籤層）。放這裡而不是最後，是為了
    讓後面的 NotifyStep 能直接讀事件、不必自己再比對一次兩日名單。

    required=False：事件寫失敗不該擋掉當日推薦與出場評估——那是使用者當天要看的東西，
    事件只影響通知與事後回顧，下次重跑會補上（insert-ignore 天生可重跑）。
    """

    name = "signal_log"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return SignalLogEngine().run(ctx.session, ctx.trading_date)


class ExitStep(PipelineStep):
    """持股出場評估：日更持有最高價（P2）。"""

    name = "exit"
    required = True

    def run(self, ctx: PipelineContext) -> dict:
        return ExitEngine().run(ctx.session, ctx.trading_date)


def format_exit_lines(rows: list[tuple[str, ExitStatus]]) -> list[str]:
    """持股出場燈號 → Discord 推播行（純函式，不觸資料庫）。

    - 🔴🟠 持股原樣列出（label 已含股名/報酬%，signals 附後）。
    - 波段持股論點明日到期（thesis_state == "expiring" 且 days_left == 1）
      追加一行「⏳ 論點明日到期」預告（awaiting_reaudit 本身已是 🟠 會自然入列，不重複判斷）。
    """
    lines: list[str] = []
    for label, st in rows:
        if st.level in ("red", "orange"):
            sig = "、".join(st.signals[:3]) or "—"
            lines.append(f"{st.light} {label}：{sig}")
        if st.thesis_state == "expiring" and st.days_left == 1:
            n = st.horizon_days
            frac = f"（第 {n - 1}/{n} 天未兌現）" if isinstance(n, int) else ""
            lines.append(f"⏳ {label} 論點明日到期{frac}")
    return lines


class NotifyStep(PipelineStep):
    """Discord 推播持股提醒 + 推薦檔數（P2，非必要）。"""

    name = "notify"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        msg = build_daily_message(ctx.session, ctx.trading_date)
        if msg is None:
            return {"status": "ok", "sent": False, "note": "無可報內容"}
        sent = send_discord(msg)
        return {"status": "ok", "sent": sent, "note": None if sent else "未設定 webhook"}


class CornerStep(PipelineStep):
    """高確信角落影子軌（實驗）→ corner_signals。純標籤層，掛了不影響主流程。"""

    name = "corners"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return CornerEngine().run(ctx.session, ctx.trading_date)


class PoppableEfficacyStep(PipelineStep):
    """會噴清單成效回測 → Setting['poppable_efficacy']（非必要、較重 ~分鐘級）。

    放在最後：純歷史回測、不影響當日推薦/通知，掛了不擾動主流程（白天讀舊快取）。
    """

    name = "poppable_efficacy"
    required = False

    def run(self, ctx: PipelineContext) -> dict:
        return PoppabilityEfficacyEngine().run(ctx.session, ctx.trading_date)
