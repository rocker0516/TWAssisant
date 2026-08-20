"""寄信（驗證信／密碼重設信）。

兩種模式，由 TWA_SMTP_HOST 是否設定決定：
  - SMTP 模式：smtplib + STARTTLS，適用任何一般信件服務（Gmail App Password、
    SES SMTP、Mailgun…）。故意不綁任何供應商 SDK——寄兩種系統信用不到那些。
  - console 模式（未設定）：把信件內容連同連結寫進 log。開發與自架單人模式
    照樣能走完整個註冊→驗證流程（從 log 撿連結），不需要真的有信箱服務。

寄信失敗不 raise 到 route：註冊本身已成功，信寄不出去是可重試的次要故障
（之後可加「重寄驗證信」端點）；把它變 500 只會讓使用者重複註冊。
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import settings

log = logging.getLogger("twa.mailer")


def send(to: str, subject: str, body: str) -> bool:
    """寄純文字信。回傳是否成功；console 模式一律視為成功。"""
    if not settings.smtp_host:
        log.warning("[console-mail] to=%s subject=%s\n%s", to, subject, body)
        return True
    try:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return True
    except Exception:
        log.exception("寄信失敗 to=%s subject=%s", to, subject)
        return False


def send_verification(to: str, token: str) -> bool:
    link = f"{settings.site_base_url}/verify/{token}"
    return send(to, "TWAssistant｜請驗證你的 Email", (
        "感謝註冊 TWAssistant。\n\n"
        f"請在 24 小時內點擊以下連結完成驗證：\n{link}\n\n"
        "若非本人操作，忽略此信即可。"
    ))


def send_reset(to: str, token: str) -> bool:
    link = f"{settings.site_base_url}/reset/{token}"
    return send(to, "TWAssistant｜重設密碼", (
        "我們收到你的密碼重設請求。\n\n"
        f"請在 1 小時內點擊以下連結設定新密碼：\n{link}\n\n"
        "若非本人操作，忽略此信即可；你的密碼不會被改變。"
    ))
