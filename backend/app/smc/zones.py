"""Premium/Discount/Equilibrium zones + Strong/Weak High/Low labels.

Classic SMC/ICT entry-timing context: avoid buying deep in the "premium"
(top of the range -- everyone who bought lower is in profit and may sell
into you) and avoid selling deep in the "discount" (bottom of the range).
Derived purely from the current active swing range (levels.support/
resistance) -- no new state to track.

Previously excluded from this module (see app.smc.events' own history) as
"duplicative of Wyckoff Spring/Upthrust". Re-added here as DISPLAY-ONLY
reference context, not a signal: nothing in trade_scenario gates or blocks
an entry based on which zone it falls in. That keeps the "duplicate signal"
concern from before moot -- this never competes with Spring/Upthrust for
deciding whether to trade, only for showing where in the range the trade
sits.
"""

from __future__ import annotations

from app.smc.phase import PHASE_BEARISH, PHASE_BULLISH

# Top/bottom slice of the range considered "premium"/"discount" (matches
# LuxAlgo's own 0.95/0.05 split).
PREMIUM_DISCOUNT_BAND_PCT = 0.05
# Half-width of the equilibrium band around the midpoint (matches LuxAlgo's
# 0.525/0.475 split -- 0.025 either side of the exact midpoint).
EQUILIBRIUM_HALF_BAND_PCT = 0.025


def _strong_weak_labels(phase: str) -> tuple[str, str]:
    """Returns (high_label, low_label). A trending phase calls the extreme
    that's HOLDING the trend "strong" and the one most likely to break
    next "weak"; Ranging has no established trend to favor either side."""
    if phase == PHASE_BULLISH:
        return "Weak High", "Strong Low"
    if phase == PHASE_BEARISH:
        return "Strong High", "Weak Low"
    return "Weak High", "Weak Low"


def compute_zones(support: float, resistance: float, phase: str) -> dict:
    range_size = resistance - support
    midpoint = (support + resistance) / 2
    high_label, low_label = _strong_weak_labels(phase)
    return {
        "premium_low": resistance - PREMIUM_DISCOUNT_BAND_PCT * range_size,
        "discount_high": support + PREMIUM_DISCOUNT_BAND_PCT * range_size,
        "equilibrium_low": midpoint - EQUILIBRIUM_HALF_BAND_PCT * range_size,
        "equilibrium_high": midpoint + EQUILIBRIUM_HALF_BAND_PCT * range_size,
        "high_label": high_label,
        "low_label": low_label,
    }
