"""來源底層抽象（架構②）：HTTP / 重試 / 限流 / health / test。

具體來源（Fugle/FinMind/Twse）繼承 BaseSource 取得共同的請求行為，
只實作各自的能力介面方法與「如何打 API」。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..credentials import get_token


class SourceError(Exception):
    """來源請求失敗（已重試耗盡）。reason 給設定頁顯示。"""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class TokenBucket:
    """簡單 token bucket 限流：每秒補 rate 顆、上限 capacity。acquire() 會 block。"""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                time.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


@dataclass
class SourceHealth:
    name: str
    has_token: bool
    last_ok_at: float | None = None
    last_error: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "has_token": self.has_token,
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
            **self.extra,
        }


class BaseSource:
    """所有來源的底層。子類設 name / base_url，必要時覆寫 _auth_headers / _probe。"""

    name: str = "base"
    base_url: str = ""
    requires_token: bool = True

    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else get_token(self.name)
        cfg = settings.rate_limits.get(self.name, {"rate": 1.0, "capacity": 3, "timeout": 20.0})
        self._bucket = TokenBucket(cfg["rate"], cfg["capacity"])
        self._timeout = cfg.get("timeout", 20.0)
        self._client = httpx.Client(timeout=self._timeout, headers={"User-Agent": "TWAssistant/0.1"})
        self._health = SourceHealth(name=self.name, has_token=bool(self.token))

    # ── 子類可覆寫 ──

    def _auth_headers(self) -> dict[str, str]:
        return {}

    def _auth_params(self) -> dict[str, str]:
        return {}

    # ── 請求核心（限流 + 退避重試）──

    def _request(
        self, path_or_url: str, params: dict | None = None, *, data: dict | None = None
    ) -> httpx.Response:
        """GET 請求；帶 data 時改用 POST（form），限流/重試行為相同。"""
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        merged = {**self._auth_params(), **(params or {})}
        headers = self._auth_headers()

        last_exc: Exception | None = None
        for attempt in range(settings.max_retries):
            self._bucket.acquire()
            try:
                if data is not None:
                    resp = self._client.post(url, params=merged, data=data, headers=headers)
                else:
                    resp = self._client.get(url, params=merged, headers=headers)
                if resp.status_code == 429:  # 額度 / 限流，退避重試
                    raise SourceError("rate limited (429)", status=429)
                resp.raise_for_status()
                self._health.last_ok_at = time.time()
                self._health.last_error = None
                return resp
            except (httpx.HTTPStatusError, httpx.TransportError, SourceError) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if isinstance(exc, SourceError):
                    status = exc.status
                # 4xx（非 429）多為請求問題，重試無用 → 直接失敗
                if status is not None and 400 <= status < 500 and status != 429:
                    break
                time.sleep(settings.backoff_base ** attempt)

        reason = self._explain(last_exc)
        self._health.last_error = reason
        raise SourceError(reason, status=getattr(last_exc, "status", None))

    @staticmethod
    def _explain(exc: Exception | None) -> str:
        if exc is None:
            return "unknown error"
        if isinstance(exc, SourceError):
            return exc.reason
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            mapping = {401: "401 未授權（token 錯誤或過期）", 403: "403 禁止存取", 404: "404 找不到端點"}
            return mapping.get(code, f"HTTP {code}")
        if isinstance(exc, httpx.TimeoutException):
            return "逾時"
        return f"連線錯誤：{exc.__class__.__name__}"

    # ── 設定頁用：health / test ──

    def health(self) -> dict:
        self._health.has_token = bool(self.token)
        return self._health.as_dict()

    def test(self, token: str | None = None) -> dict:
        """輕量探測（抓一檔一天）。回 {ok, reason}。設定頁[測試連線]用。

        可帶 token 先測再存（測通才存進 Keychain）。
        """
        probe_token = token if token is not None else self.token
        if self.requires_token and not probe_token:
            return {"ok": False, "reason": "尚未設定 token"}
        original = self.token
        self.token = probe_token
        try:
            self._probe()
            return {"ok": True, "reason": "連線成功"}
        except SourceError as exc:
            return {"ok": False, "reason": exc.reason}
        except Exception as exc:  # noqa: BLE001 — 探測不可炸掉設定頁
            return {"ok": False, "reason": self._explain(exc)}
        finally:
            self.token = original

    def _probe(self) -> None:
        """子類覆寫：打一個最小請求，失敗則 raise。"""
        raise NotImplementedError

    def close(self) -> None:
        self._client.close()
