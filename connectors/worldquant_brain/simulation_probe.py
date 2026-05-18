from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SimulationProbeRequest:
    candidate_id: str
    expression: str
    region: str
    universe: str
    delay: int
    neutralization: str
    instrument_type: str = "EQUITY"
    language: str = "FASTEXPR"
    decay: int = 4
    truncation: float = 0.08
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    test_period: str = "P1Y"
    visualization: bool = False


def setting_value(candidate: dict[str, Any], settings: dict[str, Any], api_key: str, candidate_key: str, default: Any = None) -> Any:
    params = candidate.get("params", {})
    if candidate_key in params:
        return params[candidate_key]
    return settings.get(api_key, default)


def build_simulation_request(candidate: dict, settings: dict) -> SimulationProbeRequest:
    return SimulationProbeRequest(
        candidate_id=candidate["candidate_id"],
        expression=candidate["rendered_expression"],
        region=setting_value(candidate, settings, "region", "wq_region"),
        universe=setting_value(candidate, settings, "universe", "wq_universe"),
        delay=int(setting_value(candidate, settings, "delay", "wq_delay")),
        neutralization=setting_value(candidate, settings, "neutralization", "wq_neutralization"),
        instrument_type=setting_value(candidate, settings, "instrumentType", "wq_instrument_type", "EQUITY"),
        language=setting_value(candidate, settings, "language", "wq_language", "FASTEXPR"),
        decay=int(setting_value(candidate, settings, "decay", "wq_decay", 4)),
        truncation=float(setting_value(candidate, settings, "truncation", "wq_truncation", 0.08)),
        pasteurization=setting_value(candidate, settings, "pasteurization", "wq_pasteurization", "ON"),
        unit_handling=setting_value(candidate, settings, "unitHandling", "wq_unit_handling", "VERIFY"),
        nan_handling=setting_value(candidate, settings, "nanHandling", "wq_nan_handling", "OFF"),
        test_period=setting_value(candidate, settings, "testPeriod", "wq_test_period", "P1Y"),
        visualization=bool(setting_value(candidate, settings, "visualization", "wq_visualization", False)),
    )


def to_api_payload(request: SimulationProbeRequest) -> dict[str, object]:
    return {
        "regular": request.expression,
        "type": "REGULAR",
        "settings": {
            "instrumentType": request.instrument_type,
            "region": request.region,
            "universe": request.universe,
            "delay": request.delay,
            "neutralization": request.neutralization,
            "language": request.language,
            "decay": request.decay,
            "truncation": request.truncation,
            "pasteurization": request.pasteurization,
            "unitHandling": request.unit_handling,
            "nanHandling": request.nan_handling,
            "testPeriod": request.test_period,
            "visualization": request.visualization,
        },
    }
