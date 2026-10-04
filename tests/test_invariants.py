"""跨事件不变量的反例测试。

做法：载入 data/scenario.json 这条已通过的完整事件流，每次只做一处违规改动，
断言 validate_stream 必须报出对应的错误码。
"""

import copy
import json
import unittest
from pathlib import Path

from src.validator import validate_event, validate_stream

ROOT = Path(__file__).parents[1]


def load_events() -> list[dict]:
    return json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))["events"]


def find(events: list[dict], **kw) -> dict:
    for e in events:
        if all(e.get(k) == v for k, v in kw.items()):
            return e
    raise AssertionError(f"找不到事件 {kw}")


def find_payload(events: list[dict], event_type: str, **payload_kw) -> dict:
    for e in events:
        if e["event_type"] == event_type and all(e["payload"].get(k) == v for k, v in payload_kw.items()):
            return e
    raise AssertionError(f"找不到 {event_type} {payload_kw}")


def remove(events: list[dict], **kw) -> None:
    e = find(events, **kw)
    events.remove(e)


def renumber(events: list[dict]) -> None:
    """新增/删除事件后，按各聚合 occurred_at 重排 version，排除版本号噪音。"""
    by_agg: dict[str, list[dict]] = {}
    for e in events:
        by_agg.setdefault(e["aggregate_id"], []).append(e)
    for rows in by_agg.values():
        for i, e in enumerate(sorted(rows, key=lambda r: r["occurred_at"]), 1):
            e["version"] = i


def codes(errors: list[str]) -> list[str]:
    return [msg.split("：", 1)[0].split("] ", 1)[-1] for msg in errors]


class ObservationStateMachineTest(unittest.TestCase):
    def test_cannot_discharge_before_required_until(self) -> None:
        # 自觉好转不能提前结案：C 的医学解除被改到留观期限之前
        events = load_events()
        d = find_payload(events, "OBSERVATION_DISCHARGED_MEDICALLY", patient_id="P-0920-C")
        d["occurred_at"] = "2026-09-20T22:00:00+08:00"
        d["payload"]["discharged_at"] = d["occurred_at"]
        self.assertIn("OBS_TIME_NOT_MET", codes(validate_stream(events)))

    def test_cannot_discharge_inside_false_recovery_window(self) -> None:
        # 假愈期窗口未结束不得医学解除：给 C 加一条症状暂缓（窗口至 09-23）
        events = load_events()
        pause = {
            "event_id": "EV-TEST-PAUSE-C",
            "event_type": "SYMPTOM_PAUSED",
            "aggregate_type": "patient_course",
            "aggregate_id": "CRSE-0920-C",
            "exposure_id": "EXP-0920-FAM",
            "occurred_at": "2026-09-21T10:00:00+08:00",
            "version": 1,
            "summary": "C：症状暂缓测试事件",
            "payload": {
                "patient_id": "P-0920-C",
                "paused_symptoms": ["VOMITING"],
                "expected_resurgence_window": {
                    "from": "2026-09-21T10:00:00+08:00",
                    "until": "2026-09-23T10:00:00+08:00",
                },
                "recorded_by": "STAFF-CITY-ZHAO",
                "warning_given": True,
            },
        }
        events.append(pause)
        renumber(events)
        self.assertIn("FALSE_RECOVERY_WINDOW_OPEN", codes(validate_stream(events)))

    def test_left_against_advice_is_not_terminal_recovery(self) -> None:
        # 自行离院后未 RETURNED 不能直接医学解除：删掉 B 的返院与延长，终局仍是 LAA
        events = load_events()
        remove(events, **{"event_type": "OBSERVATION_RETURNED"})
        remove(events, **{"event_type": "OBSERVATION_EXTENDED",
                          "aggregate_id": "CRSE-0920-B"})
        remove(events, **{"event_type": "HANDOFF_INITIATED"})
        remove(events, **{"event_type": "HANDOFF_ACKNOWLEDGED"})
        self.assertIn("OBS_TRANSITION", codes(validate_stream(events)))


class CriticalAlertTest(unittest.TestCase):
    def test_critical_metric_must_raise_alert(self) -> None:
        events = load_events()
        remove(events, aggregate_id="ALERT-A-1", event_type="CRITICAL_ALERT_RAISED")
        remove(events, aggregate_id="ALERT-A-1", event_type="CRITICAL_ALERT_ACKNOWLEDGED")
        self.assertIn("CRITICAL_NO_ALERT", codes(validate_stream(events)))

    def test_alert_must_be_acknowledged_by_physician(self) -> None:
        events = load_events()
        remove(events, aggregate_id="ALERT-A-1", event_type="CRITICAL_ALERT_ACKNOWLEDGED")
        self.assertIn("ALERT_UNACKNOWLEDGED", codes(validate_stream(events)))

    def test_alert_cannot_auto_close(self) -> None:
        events = load_events()
        e = find(events, aggregate_id="ALERT-A-1", event_type="CRITICAL_ALERT_RAISED")
        e["payload"]["auto_close_forbidden"] = False
        self.assertIn("ALERT_AUTOCLOSE", codes(validate_stream(events)))


class SpecimenLineageTest(unittest.TestCase):
    def test_received_seal_must_match_lineage(self) -> None:
        # 接收时封签与谱系登记不一致必须报错
        events = load_events()
        e = find(events, aggregate_id="XFER-0921-A", event_type="SPECIMEN_RECEIVED")
        e["payload"]["seal_checks"][0]["seal_id"] = "SEAL-FORGED"
        self.assertIn("RECEIVE_SEAL_MISMATCH", codes(validate_stream(events)))

    def test_transfer_must_close_with_receipt(self) -> None:
        # 转出无接收 = 谱系未闭环
        events = load_events()
        remove(events, aggregate_id="XFER-0921-A", event_type="SPECIMEN_RECEIVED")
        self.assertIn("TRANSFER_OPEN", codes(validate_stream(events)))

    def test_tox_result_lineage_root_must_be_ancestor(self) -> None:
        events = load_events()
        e = find(events, aggregate_id="TOX-M1T-1")
        e["payload"]["lineage"]["root_specimen_id"] = "SP-BLOOD-A-1"  # 非其祖先
        self.assertIn("TOX_LINEAGE_ROOT", codes(validate_stream(events)))

    def test_lab_panel_must_trace_to_specimen(self) -> None:
        # 任何一次指标变化都要能回到采样
        events = load_events()
        e = find(events, aggregate_id="LP-A-1")
        e["payload"]["specimen_id"] = "SP-DOES-NOT-EXIST"
        self.assertIn("LAB_SPECIMEN_MISSING", codes(validate_stream(events)))


class HandoffTest(unittest.TestCase):
    def test_handoff_without_acknowledgement_is_open(self) -> None:
        events = load_events()
        remove(events, aggregate_id="HO-A-1", event_type="HANDOFF_ACKNOWLEDGED")
        self.assertIn("HANDOFF_UNACKNOWLEDGED", codes(validate_stream(events)))

    def test_each_open_action_must_be_confirmed(self) -> None:
        events = load_events()
        e = find(events, aggregate_id="HO-A-1", event_type="HANDOFF_ACKNOWLEDGED")
        e["payload"]["confirmations"]["open_actions_accepted"].pop()
        self.assertIn("HANDOFF_ACTION_UNCONFIRMED", codes(validate_stream(events)))

    def test_broken_seal_cannot_be_accepted(self) -> None:
        events = load_events()
        e = find(events, aggregate_id="HO-A-1", event_type="HANDOFF_ACKNOWLEDGED")
        row = e["payload"]["confirmations"]["specimens_accounted"][0]
        row["accounted"] = True
        row["seal_intact"] = False  # 点收但封签破损
        self.assertIn("HANDOFF_SEAL_BROKEN", codes(validate_stream(events)))


class ExposureMergeTest(unittest.TestCase):
    def test_merged_source_record_must_be_kept(self) -> None:
        # 合并引用的多院原始上报必须保留
        events = load_events()
        remove(events, aggregate_id="EXP-0920-FAM-DUP")
        self.assertIn("MERGE_SOURCE_MISSING", codes(validate_stream(events)))

    def test_merged_id_cannot_be_reused_after_merge(self) -> None:
        # 合并之后不得再用被注销编号发事件
        events = load_events()
        e = find(events, aggregate_id="LP-C-1")
        e["exposure_id"] = "EXP-0920-FAM-DUP"
        self.assertIn("MERGED_ID_REUSED", codes(validate_stream(events)))


class PrivacyAndReferentialTest(unittest.TestCase):
    def test_co_diner_link_cannot_carry_medical_record(self) -> None:
        events = load_events()
        e = find_payload(events, "PERSON_RELATION_LINKED", subject_patient_id="P-0920-A",
                         related_patient_id="P-0920-B")
        del e["payload"]["alert_message_ref"]
        self.assertIn("RELATION_ALERT_REF", codes(validate_stream(events)))

    def test_discharge_followup_must_exist(self) -> None:
        events = load_events()
        remove(events, aggregate_id="FU-C-1", event_type="FOLLOWUP_SCHEDULED")
        self.assertIn("DISCHARGE_FOLLOWUP_MISSING", codes(validate_stream(events)))

    def test_duplicate_event_id_rejected(self) -> None:
        events = load_events()
        events[5]["event_id"] = events[0]["event_id"]
        self.assertIn("EVENT_ID_DUP", codes(validate_stream(events)))

    def test_envelope_required_fields(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        del sample["exposure_id"]
        self.assertTrue(any("exposure_id" in m for m in validate_event(sample)))


if __name__ == "__main__":
    unittest.main()
