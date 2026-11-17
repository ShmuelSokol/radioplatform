"""FFmpeg prints -inf for digital silence; auto-enhance must not fail on it."""
from app.services.enhance_service import _db_value


def test_db_value_reads_a_number():
    assert _db_value(r"Noise floor dB:\s*([-\d.]+)", "Noise floor dB: -63.5\n", -60.0) == -63.5


def test_db_value_minus_inf_falls_back():
    assert _db_value(r"Noise floor dB:\s*([-\d.]+)", "Noise floor dB: -inf\n", -60.0) == -60.0


def test_db_value_missing_falls_back():
    assert _db_value(r"Crest factor:\s*([-\d.]+)", "nothing here", 10.0) == 10.0
