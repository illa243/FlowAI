from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Any

LOGGER = logging.getLogger(__name__)
FALLBACK_MODELS = ("gpt-5.6-terra", "gpt-5.6-terra-max", "gpt-5.6-flex")


class CodexAuthError(RuntimeError):
    pass


def available_models() -> list[str]:
    """Return account models, falling back to the known composer set."""
    try:
        import openai_codex

        with openai_codex.Codex() as codex:
            response = codex.models()
            items = getattr(response, "models", None)
            if items is None:
                items = getattr(response, "data", [])
            names = [
                str(
                    getattr(item, "id", "")
                    or getattr(item, "model", "")
                    or getattr(item, "name", "")
                )
                for item in items
            ]
            cleaned = list(dict.fromkeys(name for name in names if name))
            if cleaned:
                return cleaned
    except Exception:
        LOGGER.info("Не вдалося отримати список моделей", exc_info=True)
    return list(FALLBACK_MODELS)


@dataclass(frozen=True, slots=True)
class CodexRateLimit:
    limit_id: str
    limit_name: str
    window_kind: str
    used_percent: float
    window_duration_mins: int | None = None
    resets_at: int | None = None

    @property
    def remaining_percent(self) -> int:
        remaining = max(0.0, min(100.0, 100.0 - self.used_percent))
        return round(remaining)

    @property
    def display_name(self) -> str:
        return self.limit_name or (
            "Codex" if self.limit_id == "codex" else self.limit_id
        )


@dataclass(frozen=True, slots=True)
class CodexUser:
    email: str
    plan_type: str = ""
    account_type: str = "chatgpt"
    rate_limits: tuple[CodexRateLimit, ...] = ()

    @property
    def nickname(self) -> str:
        if "@" in self.email:
            local = self.email.split("@", 1)[0].strip()
            if local:
                return local
        return self.email.strip() or "Codex"

    @property
    def initial(self) -> str:
        return self.nickname[:1].upper() or "C"

    @property
    def remaining_percent(self) -> int | None:
        if not self.rate_limits:
            return None
        return min(limit.remaining_percent for limit in self.rate_limits)


def _json_payload(response: Any, *, description: str) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json")
    elif isinstance(response, dict):
        payload = response
    else:
        raise CodexAuthError(f"Codex повернув невідомий формат {description}")
    if not isinstance(payload, dict):
        raise CodexAuthError(f"Codex повернув невідомий формат {description}")
    return payload


def _value(mapping: dict[str, Any], camel_case: str, snake_case: str) -> Any:
    return mapping.get(camel_case, mapping.get(snake_case))


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def rate_limits_from_response(response: Any) -> tuple[CodexRateLimit, ...]:
    if response is None:
        return ()
    payload = _json_payload(response, description="лімітів")
    buckets = _value(payload, "rateLimitsByLimitId", "rate_limits_by_limit_id")
    if not isinstance(buckets, dict) or not buckets:
        single_bucket = _value(payload, "rateLimits", "rate_limits")
        buckets = {"codex": single_bucket} if isinstance(single_bucket, dict) else {}

    limits: list[CodexRateLimit] = []
    for fallback_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        limit_id = str(_value(bucket, "limitId", "limit_id") or fallback_id)
        limit_name = str(_value(bucket, "limitName", "limit_name") or "")
        for window_kind in ("primary", "secondary"):
            window = bucket.get(window_kind)
            if not isinstance(window, dict):
                continue
            used_value = _value(window, "usedPercent", "used_percent")
            if isinstance(used_value, bool):
                continue
            try:
                used_percent = float(used_value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(used_percent):
                continue
            limits.append(
                CodexRateLimit(
                    limit_id=limit_id,
                    limit_name=limit_name,
                    window_kind=window_kind,
                    used_percent=used_percent,
                    window_duration_mins=_optional_int(
                        _value(window, "windowDurationMins", "window_duration_mins")
                    ),
                    resets_at=_optional_int(_value(window, "resetsAt", "resets_at")),
                )
            )
    return tuple(limits)


def user_from_account_response(response: Any) -> CodexUser | None:
    if response is None:
        return None
    payload = _json_payload(response, description="профілю")

    account = payload.get("account")
    if not isinstance(account, dict):
        return None
    account_type = str(account.get("type") or "")
    if account_type != "chatgpt":
        return None
    return CodexUser(
        email=str(account.get("email") or ""),
        plan_type=str(account.get("plan_type") or account.get("planType") or ""),
        account_type=account_type,
    )


def _rate_limits_response(codex: Any) -> Any:
    public_method = getattr(codex, "rate_limits", None)
    if callable(public_method):
        return public_method()

    # openai-codex 0.147 does not expose this App Server method yet.
    client = getattr(codex, "_client", None)
    raw_request = getattr(client, "_request_raw", None)
    if not callable(raw_request):
        raise CodexAuthError(
            "Встановлена версія Codex SDK не підтримує читання лімітів"
        )
    return raw_request("account/rateLimits/read")


def read_codex_user_from_client(
    codex: Any, *, refresh_token: bool = False
) -> CodexUser | None:
    user = user_from_account_response(codex.account(refresh_token=refresh_token))
    if user is None:
        return None
    try:
        limits = rate_limits_from_response(_rate_limits_response(codex))
    except Exception:
        LOGGER.warning("Could not read Codex rate limits", exc_info=True)
        return user
    return replace(user, rate_limits=limits)


def read_codex_user(*, refresh_token: bool = False) -> CodexUser | None:
    try:
        from openai_codex import Codex
    except ImportError as exc:
        raise CodexAuthError("Не встановлено офіційний Codex SDK") from exc

    try:
        with Codex() as codex:
            return read_codex_user_from_client(codex, refresh_token=refresh_token)
    except Exception as exc:
        raise CodexAuthError(str(exc)) from exc


def logout_codex_user() -> None:
    try:
        from openai_codex import Codex
    except ImportError as exc:
        raise CodexAuthError("Не встановлено офіційний Codex SDK") from exc

    try:
        with Codex() as codex:
            codex.logout()
    except Exception as exc:
        raise CodexAuthError(str(exc)) from exc
