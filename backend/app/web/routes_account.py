"""機能畫面（帳號流程）：註冊／登入／Email 驗證／忘記密碼／重設密碼。

畫面地圖的「機能・支撐」列——不在轉換漏斗上，登入牆的實體。
與 routes_public.py 分檔：那邊是內容頁（資料、可被索引），這邊是表單頁
（robots noindex、不需要 cutoff 資訊列）。

表單頁都是同一個骨架（auth_form.html）＋欄位參數；提交由頁內 fetch 打
/api/auth/*，成功後導向。唯一伺服器端處理的是 /verify/{token}——點信裡的
連結是 GET，沒有 JS 也要能完成驗證。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..api.deps import get_session_write
from ..api.routes_auth import consume_verification

router = APIRouter(tags=["account"], include_in_schema=False)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _form(request: Request, **ctx) -> HTMLResponse:
    return _templates.TemplateResponse(request, "auth_form.html",
                                       {"cutoff": "", **ctx})


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request) -> HTMLResponse:
    return _form(
        request, page_title="註冊",
        heading="建立帳號",
        sub="免費追蹤持股與觀察清單；註冊後需完成 Email 驗證。",
        api="/api/auth/signup", submit_label="註冊",
        fields=[
            {"name": "email", "label": "Email", "type": "email", "autocomplete": "email"},
            {"name": "password", "label": "密碼（至少 8 字元）", "type": "password",
             "autocomplete": "new-password"},
        ],
        success_message="驗證信已寄出，請至信箱完成驗證後登入。",
        links=[{"href": "/login", "label": "已有帳號？登入"}],
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return _form(
        request, page_title="登入",
        heading="登入",
        sub="",
        api="/api/auth/login", submit_label="登入",
        fields=[
            {"name": "username", "label": "Email", "type": "email", "autocomplete": "username"},
            {"name": "password", "label": "密碼", "type": "password",
             "autocomplete": "current-password"},
        ],
        redirect="/app",
        links=[{"href": "/signup", "label": "還沒有帳號？註冊"},
               {"href": "/forgot", "label": "忘記密碼"}],
    )


@router.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request) -> HTMLResponse:
    return _form(
        request, page_title="忘記密碼",
        heading="重設密碼",
        sub="輸入註冊 Email，我們會寄送重設連結（1 小時內有效）。",
        api="/api/auth/forgot", submit_label="寄送重設信",
        fields=[{"name": "email", "label": "Email", "type": "email", "autocomplete": "email"}],
        success_message="若該 Email 已註冊，重設信已寄出，請至信箱查看。",
        links=[{"href": "/login", "label": "回登入"}],
    )


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(token: str, request: Request) -> HTMLResponse:
    return _form(
        request, page_title="設定新密碼",
        heading="設定新密碼",
        sub="重設後所有裝置都需要重新登入。",
        api="/api/auth/reset", submit_label="更新密碼",
        fields=[{"name": "password", "label": "新密碼（至少 8 字元）", "type": "password",
                 "autocomplete": "new-password"}],
        hidden={"token": token},
        success_message="密碼已更新，請重新登入。",
        links=[{"href": "/login", "label": "前往登入"}],
    )


@router.get("/verify/{token}", response_class=HTMLResponse)
def verify_page(token: str, request: Request,
                session: Session = Depends(get_session_write)) -> HTMLResponse:
    user = consume_verification(session, token)
    return _templates.TemplateResponse(request, "verify.html", {
        "cutoff": "", "ok": user is not None,
        "email": user.email if user else None,
    })
