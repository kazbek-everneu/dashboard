from app.screenshots.capture import config as cfg
from tests.capture.fakes import make_config


def test_stabilization_retry_window_exceeds_base_cap() -> None:
    config = make_config(timeout=30000)

    assert cfg.stabilization_timeout_ms(config) == 25000
    assert cfg.stabilization_timeout_ms(config, retry=True) == 37500


def test_stabilization_window_has_a_floor() -> None:
    config = make_config(timeout=1000)

    assert cfg.stabilization_timeout_ms(config) == 8000
    assert cfg.stabilization_timeout_ms(config, retry=True) == 12000
