"""Maps user settings + current load to concrete visual quality."""

from __future__ import annotations

from dataclasses import dataclass

LOAD_FPS_CAP = 15


@dataclass(frozen=True)
class Quality:
    fps: int
    particles: int
    glow: bool
    pulses: bool
    static: bool = False       # reduced motion: draw once, redraw only on data/clock changes


_INTENSITY = {
    "low": dict(particles=10, glow=False, pulses=False),
    "medium": dict(particles=28, glow=True, pulses=True),
    "high": dict(particles=48, glow=True, pulses=True),
}


def effective_quality(
    fps_setting: int, intensity: str, under_load: bool, reduce_under_load: bool,
    particle_count: int | None = None, pulse_strength: float | None = None,
    reduced_motion: bool = False,
) -> Quality:
    """Return the quality actually used for drawing.

    Under sustained load (and when the user allows it) expensive effects are
    switched off and the frame rate is capped.
    """
    base = _INTENSITY.get(intensity, _INTENSITY["medium"])
    fps = max(5, min(60, int(fps_setting)))
    if under_load and reduce_under_load:
        return Quality(fps=min(fps, LOAD_FPS_CAP), particles=0, glow=False, pulses=False,
                       static=bool(reduced_motion))
    particles = base["particles"] if particle_count is None else min(int(particle_count), 48)
    pulses = base["pulses"] if pulse_strength is None else (base["pulses"] and pulse_strength > 0)
    if reduced_motion:       # static emblem: the frame timer is not used at all (see WallpaperScene)
        return Quality(fps=min(fps, 5), particles=0, glow=base["glow"], pulses=False, static=True)
    return Quality(fps=fps, particles=particles, glow=base["glow"], pulses=pulses)
