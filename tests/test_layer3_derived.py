from datetime import datetime, timezone, timedelta
import pytest
from wayfinder.derived.expertise import ExpertiseCalculator

def test_decay_formula(monkeypatch):
    import wayfinder.derived.expertise as expertise_module
    frozen = datetime.now(timezone.utc)
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen
    monkeypatch.setattr(expertise_module, 'datetime', FrozenClock)
    calc = ExpertiseCalculator()
    now = frozen

    # Event today -> decay ~ 1.0
    today_iso = now.isoformat()
    assert abs(calc.compute_decay(today_iso) - 1.0) < 0.05

    # Event 90 days ago -> decay ~ 0.5 (half life)
    days_90_iso = (now - timedelta(days=90)).isoformat()
    decay_90 = calc.compute_decay(days_90_iso)
    assert abs(decay_90 - 0.5) < 0.05

    # Event 180 days ago -> decay ~ 0.25 (two half lives)
    days_180_iso = (now - timedelta(days=180)).isoformat()
    decay_180 = calc.compute_decay(days_180_iso)
    assert abs(decay_180 - 0.25) < 0.05

    # Event > 180 days ago -> decay = 0.0 (outside window)
    days_200_iso = (now - timedelta(days=200)).isoformat()
    assert calc.compute_decay(days_200_iso) == 0.0
