from pathlib import Path

import pytest

from situation_hub import config


def test_example_config_loads():
    data = config.load(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert data["presence"]["away_hold_minutes"] >= data["presence"]["home_hold_minutes"]
    assert all("domain" in signal for signal in data["signals"])


def test_rejects_two_signals_using_the_same_source(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        """
signals:
  - {id: first, kind: ping, domain: network, prob_given_home: 0.8, prob_given_away: 0.1, entity_id: binary_sensor.example_ping}
  - {id: second, kind: device_active, entity_id: binary_sensor.example_ping}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="distinct source entity IDs"):
        config.load(path)


def test_rejects_unsupported_calendar_signal(tmp_path):
    path = tmp_path / "calendar.yaml"
    path.write_text(
        "signals: [{id: events, kind: calendar, entity_id: calendar.example_events}]",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unsupported signal kind"):
        config.load(path)

@pytest.mark.parametrize("domain", ["", ", domain: ''", ", domain: [network]"])
def test_requires_a_domain_slug(tmp_path, domain):
    path = tmp_path / "domain.yaml"
    path.write_text(
        "signals: [{id: ping, kind: ping, entity_id: binary_sensor.example_ping" + domain + "}]",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Expected a signal domain slug"):
        config.load(path)
