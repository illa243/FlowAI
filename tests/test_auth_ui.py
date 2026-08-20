from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from flowai.codex_auth import (
    CodexRateLimit,
    CodexUser,
    rate_limits_from_response,
    user_from_account_response,
)
from flowai.ui.login_dialog import ChatGPTLoginDialog
from flowai.ui.main_window import MainWindow


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_user_profile_uses_email_local_part_as_nickname() -> None:
    user = user_from_account_response(
        {
            "account": {
                "type": "chatgpt",
                "email": "flow.builder@example.com",
                "planType": "pro",
            }
        }
    )
    assert user == CodexUser("flow.builder@example.com", "pro", "chatgpt")
    assert user.nickname == "flow.builder"
    assert user.initial == "F"


def test_login_dialog_has_only_cancel_button() -> None:
    application()
    dialog = ChatGPTLoginDialog()
    buttons = dialog.findChildren(QPushButton)
    assert len(buttons) == 1
    assert buttons[0].text() == "СКАСУВАТИ"
    dialog.deleteLater()


def test_account_button_switches_to_avatar_and_nickname() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.current_user = CodexUser(
        "flow.builder@example.com",
        "pro",
        rate_limits=(
            CodexRateLimit("codex", "", "primary", 58, 10080, 1_800_000_000),
            CodexRateLimit("codex_fast", "Codex Fast", "primary", 25, 300),
        ),
    )
    window._update_account_button()
    assert window.account_button.text() == "flow.builder · 42%"
    assert not window.account_button.icon().isNull()
    tooltip = window.account_button.toolTip()
    assert "flow.builder@example.com · залишилось 42%" in tooltip
    assert "Codex · 7 дн.: залишилось 42%" in tooltip
    assert "Codex Fast · 5 год.: залишилось 75%" in tooltip
    window.close()


def test_rate_limits_use_all_buckets_and_most_restrictive_window() -> None:
    limits = rate_limits_from_response(
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 71, "windowDurationMins": 10080},
                    "secondary": {"usedPercent": 25, "windowDurationMins": 300},
                },
                "codex_fast": {
                    "limitId": "codex_fast",
                    "limitName": "Codex Fast",
                    "primary": {"usedPercent": 0, "windowDurationMins": 60},
                },
            }
        }
    )
    user = CodexUser("flow.builder@example.com", rate_limits=limits)
    assert len(limits) == 3
    assert user.remaining_percent == 29
    assert limits[0].display_name == "Codex"
