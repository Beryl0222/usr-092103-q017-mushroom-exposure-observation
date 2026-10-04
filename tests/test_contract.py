import json
import unittest
from pathlib import Path

from src.validator import validate_event, validate_stream

ROOT = Path(__file__).parents[1]


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_full_scenario_stream_is_valid(self) -> None:
        scenario = json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))
        errors = validate_stream(scenario["events"])
        self.assertEqual(errors, [])

    def test_unknown_event_type_rejected(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        sample["event_type"] = "CASE_CLOSED_AUTOMATICALLY"
        errors = validate_event(sample)
        self.assertTrue(any("CASE_CLOSED" in e for e in errors))

    def test_payload_must_match_event_type(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        sample["payload"] = {"patient_id": "P1"}  # 不符合 EXPOSURE_REPORTED 的结构
        errors = validate_event(sample)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
