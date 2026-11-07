"""Wallpaper appearance derived from settings (pure Python, no Qt)."""

from __future__ import annotations

from dataclasses import dataclass

PARTICLES = {"off": 0, "low": 10, "medium": 28, "high": 48}
PULSE_STRENGTH = {"off": 0.0, "low": 0.55, "medium": 1.0, "high": 1.45}


@dataclass(frozen=True)
class Appearance:
    emblem_scale: float = 1.0
    dimmer: float = 0.0            # 0..0.7 black overlay
    show_clock: bool = True
    show_overlay: bool = True
    overlay_expanded: bool = False
    reduced_motion: bool = False
    pulse_strength: float = 1.0    # 0 disables the pulse rings
    particle_count: int = 28       # requested; effective_quality may lower it under load


def appearance_from(settings) -> Appearance:
    g = settings.get
    return Appearance(
        emblem_scale=max(0.5, min(1.5, g("emblem_size") / 100.0)),
        dimmer=max(0.0, min(0.7, g("dimmer_percent") / 100.0)),
        show_clock=bool(g("show_clock")),
        show_overlay=bool(g("show_overlay")),
        overlay_expanded=g("overlay_mode") == "expanded",
        reduced_motion=bool(g("reduced_motion")),
        pulse_strength=PULSE_STRENGTH[g("pulse_intensity")],
        particle_count=PARTICLES[g("particle_intensity")],
    )
