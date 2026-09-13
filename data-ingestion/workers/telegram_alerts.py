"""
VLTHR Telegram Alert Utility
============================
Shared module for sending alerts to Telegram channels.
Reads BOT_TOKEN and CHAT_ID from .env (loaded by caller or auto-loaded here).
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ── AUTO-LOAD .env FROM ENGINE ROOT ──────────────────────────────────────
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        pass

# ── CONFIG ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ENABLE_ALERTS = os.getenv("ENABLE_TELEGRAM_ALERTS", "true").lower() in ("1", "true", "yes")

CHAT_IDS = {
    "data": os.getenv("TELEGRAM_CHAT_ID_DATA", ""),
    "trading": os.getenv("TELEGRAM_CHAT_ID_TRADING", ""),
    "devops": os.getenv("TELEGRAM_CHAT_ID_DEVOPS", ""),
    "default": os.getenv("TELEGRAM_CHAT_ID", ""),
}

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


# HTML escaping is safer than MarkdownV2 escaping
import html as _html

def _escape_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return _html.escape(str(text))


def send_telegram(
    message: str,
    chat_type: str = "data",
    parse_mode: str = "HTML",
    silent: bool = False,
) -> bool:
    """
    Send a Telegram message.

    Args:
        message: The message text.
        chat_type: 'data', 'trading', 'devops', or 'default'.
        parse_mode: 'MarkdownV2', 'HTML', or ''.
        silent: True to disable notification sound.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not ENABLE_ALERTS:
        return False
    if not BOT_TOKEN:
        print("[Telegram] BOT_TOKEN not set — skipping alert")
        return False

    chat_id = CHAT_IDS.get(chat_type, CHAT_IDS["default"])
    if not chat_id:
        print(f"[Telegram] No chat_id for type '{chat_type}' — skipping alert")
        return False

    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_notification": silent,
    }
    # Default to HTML for safety; caller can override
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        r = requests.post(API_URL, json=payload, timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            print(f"[Telegram] Alert sent to {chat_type} chat")
            return True
        else:
            print(f"[Telegram] Send failed: HTTP {r.status_code} — {r.text[:200]}")
            return False
    except Exception as e:
        print(f"[Telegram] Exception sending alert: {e}")
        return False


def alert_error(
    source: str,
    error_msg: str,
    symbol: Optional[str] = None,
    details: Optional[str] = None,
    chat_type: str = "data",
) -> bool:
    """
    Send a formatted error alert.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = f"Time:    {ts}\nSource:  {source}\n"
    if symbol:
        body += f"Symbol:  {symbol}\n"
    body += f"Error:   {error_msg}\n"
    if details:
        body += f"Details: {details[:500]}\n"
    msg = f"🚨 <b>VLTHR ERROR — {_escape_html(source)}</b>\n<pre>{_escape_html(body)}</pre>"
    return send_telegram(msg, chat_type=chat_type, parse_mode="HTML")


def alert_proxy_banned(
    proxy_url: str,
    endpoint: str,
    status_code: int,
    response_body: Optional[str] = None,
) -> bool:
    """Alert when a proxy appears blocked or banned."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    masked = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
    body = (response_body or "")[:300]
    content = f"Time:   {ts}\nProxy:  {masked}\nEndpoint: {endpoint}\nStatus: {status_code}\nBody:   {body}\n"
    msg = f"🚫 <b>VLTHR PROXY BLOCKED</b>\n<pre>{_escape_html(content)}</pre>"
    return send_telegram(msg, chat_type="data", parse_mode="HTML")


def alert_rate_limit(
    source: str,
    retry_after: Optional[int] = None,
) -> bool:
    """Alert on rate limit detection."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    retry = f"Retry after: {retry_after}s" if retry_after else "Unknown retry time"
    content = f"Time:   {ts}\nSource: {source}\n{retry}\n"
    msg = f"⏳ <b>VLTHR RATE LIMIT</b>\n<pre>{_escape_html(content)}</pre>"
    return send_telegram(msg, chat_type="data", parse_mode="HTML")


def alert_data_quality(
    report_lines: list[str],
    chat_type: str = "data",
) -> bool:
    """Send hourly data quality summary."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = "\n".join(report_lines)
    msg = f"📊 <b>VLTHR Data Quality Report</b>\n<pre>Time: {ts}\n\n{_escape_html(body)}</pre>"
    return send_telegram(msg, chat_type=chat_type, parse_mode="HTML")


def alert_trade_close(
    symbol: str,
    side: str,
    reason: str,  # TP_HIT, SL_HIT, MANUAL
    entry_price: float,
    exit_price: float,
    net_pnl_pct: float,
    net_pnl_usd: float,
    hours_held: float,
    qty: float,
    leverage: int,
) -> bool:
    """
    Send trading alert when a paper trade closes (TP/SL hit).
    Uses TELEGRAM_CHAT_ID_TRADING.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    emoji = "🎯" if reason == "TP_HIT" else "🛑" if reason == "SL_HIT" else "🔒"
    pnl_emoji = "🟢" if net_pnl_usd >= 0 else "🔴"

    content = (
        f"Time:      {ts}\n"
        f"Symbol:    {symbol}\n"
        f"Side:      {side}\n"
        f"Entry:     {entry_price:,.2f}\n"
        f"Exit:      {exit_price:,.2f}\n"
        f"Qty:       {qty:.4f}\n"
        f"Leverage:  {leverage}x\n"
        f"Hours:     {hours_held:.1f}\n"
        f"\n"
        f"P&L %:     {net_pnl_pct:+.2f}%\n"
        f"P&L $:     {pnl_emoji} {net_pnl_usd:+.2f} USD\n"
    )
    msg = f"{emoji} <b>VLTHR Trade Closed — {reason}</b>\n<pre>{_escape_html(content)}</pre>"
    return send_telegram(msg, chat_type="trading", parse_mode="HTML")


if __name__ == "__main__":
    # Quick self-test
    print("[Telegram Alerts] Self-test...")
    print(f"  BOT_TOKEN present: {bool(BOT_TOKEN)}")
    print(f"  ENABLE_ALERTS:   {ENABLE_ALERTS}")
    for k, v in CHAT_IDS.items():
        print(f"  CHAT_ID [{k}]: {bool(v)} {'✅' if v else '❌'}")
    if BOT_TOKEN and CHAT_IDS["data"]:
        send_telegram(
            "🧪 <b>VLTHR Test Alert</b>\nScheduler monitoring is online.",
            chat_type="data",
            parse_mode="HTML",
        )
    else:
        print("[Telegram Alerts] Skipping live test — missing config")
