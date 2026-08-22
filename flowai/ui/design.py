from __future__ import annotations

COLORS: dict[str, str] = {
    "bg": "#0B1017",
    "surface": "#121A26",
    "surface_raised": "#18222F",
    "surface_sunken": "#080D14",
    "border": "#1F2B3A",
    "border_strong": "#2C3B4E",
    "text": "#EDF1F7",
    "text_muted": "#9AA9BD",
    "text_dim": "#64748B",
    "accent": "#6D4AFF",
    "accent_hover": "#8067FF",
    "accent_text": "#FFFFFF",
    "success": "#22C55E",
    "danger": "#EF4444",
    "warning": "#EAB308",
    "focus": "#8B7BFF",
}

RADII: dict[str, int] = {"sm": 8, "md": 12, "lg": 16, "pill": 999}
SPACE: dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
TYPE: dict[str, tuple[int, int]] = {
    "caption": (11, 500),
    "body": (13, 400),
    "label": (13, 600),
    "title": (15, 600),
    "heading": (20, 700),
}
DURATION: dict[str, int] = {"fast": 120, "base": 180, "slow": 260}

CONTROL_HEIGHT = 34
SHADOW_BLUR = 28
SHADOW_ALPHA = 110
