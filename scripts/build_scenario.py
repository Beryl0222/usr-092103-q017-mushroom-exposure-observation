"""生成 data/scenario.json：一桌野生蘑菇餐的跨院完整联调事件流。

覆盖的关键领域规则：
- 多院重复上报先各自留存，再 EXPOSURE_MERGED 合并为同一暴露事件；
- 假愈期 SYMPTOM_PAUSED 不等于医学解除；自行离院 LAA 后症状反复再 RETURNED；
- 危急值必须 CRITICAL_ALERT_RAISED 且由医生 ACK 给出临床决策；
- 剩余蘑菇样本拆分送多家实验室，封签、转运、接收、毒理结果谱系闭环；
- 转院 HANDOFF 由接收方逐项确认资料、样本（含封签）与未完成待办；
- 医学解除留观后预约复诊并回填结果。

运行：python3 scripts/build_scenario.py
"""

from __future__ import annotations

import json
from pathlib import Path

EXP = "EXP-0920-FAM"
DUP = "EXP-0920-FAM-DUP"
F_COUNTY = "F-COUNTY-01"
F_CITY = "F-CITY-02"
F_REGIONAL = "F-REGIONAL-PC"

PA, PB, PC = "P-0920-A", "P-0920-B", "P-0920-C"
CRSE_A, CRSE_B, CRSE_C = "CRSE-0920-A", "CRSE-0920-B", "CRSE-0920-C"

events: list[dict] = []
_seq = 0


def ev(event_type: str, aggregate_type: str, aggregate_id: str, occurred_at: str,
       summary: str, payload: dict, exposure_id: str = EXP, **extra) -> dict:
    global _seq
    _seq += 1
    record = {
        "event_id": f"EV-{_seq:03d}",
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "exposure_id": exposure_id,
        "occurred_at": occurred_at,
        "version": 0,  # 全部生成后按各聚合 occurred_at 顺序统一编号
        "summary": summary,
        "payload": payload,
    }
    record.update(extra)
    events.append(record)
    return record


def metric(code, value, unit, critical, crit_high=None, low=None, high=None):
    rr = {}
    if low is not None:
        rr["low"] = low
    if high is not None:
        rr["high"] = high
    if crit_high is not None:
        rr["critical_high"] = crit_high
    m = {"code": code, "value": value, "unit": unit, "critical": critical}
    if rr:
        m["reference_range"] = rr
    return m


# ── 20:10 县医院首报（A、B 二人就诊）─────────────────────────────────────────
ev("EXPOSURE_REPORTED", "exposure_event", EXP, "2026-09-20T20:10:00+08:00",
   "县医院上报家庭聚餐疑似毒蘑菇中毒，4 人暴露",
   {
       "meal": {
           "meal_time": "2026-09-20T12:00:00+08:00",
           "location": "某省某市某镇团结村3组张家",
           "dish_names": ["自采野生菌炒肉", "野生菌汤"],
       },
       "reporter_facility_id": F_COUNTY,
       "suspected_species": {"name": "疑似鹅膏属（白毒伞）", "confidence": "SUSPECTED"},
       "source_trace": {
           "gathering_type": "FAMILY_MEAL",
           "exposed_count_reported": 4,
           "purchase_site": "村后山林自采",
           "region_code": "XX-YY-ZZZ",
       },
   }, source_facility_id=F_COUNTY, report_batch_id="BATCH-0920-COUNTY")

# ── 20:32 市医院重复上报（孩子 C 被另一家医院收治），先挂疑似编号独立留存 ──
ev("EXPOSURE_REPORTED", "exposure_event", DUP, "2026-09-20T20:32:00+08:00",
   "市医院上报一同聚餐儿童中毒，疑似同一餐次",
   {
       "meal": {
           "meal_time": "2026-09-20T12:00:00+08:00",
           "location": "某省某市某镇团结村3组张家",
           "dish_names": ["自采野生菌炒肉", "野生菌汤"],
       },
       "reporter_facility_id": F_CITY,
       "suspected_species": {"name": "白毒伞待排查", "confidence": "UNKNOWN"},
       "source_trace": {"gathering_type": "FAMILY_MEAL", "exposed_count_reported": 4},
       "duplicate_of_exposure_id": EXP,
   }, exposure_id=DUP, source_facility_id=F_CITY, report_batch_id="BATCH-0920-CITY")

# ── 21:05 疾控判定同一事件，合并；两条原始上报均保留 ─────────────────────────
ev("EXPOSURE_MERGED", "exposure_event", EXP, "2026-09-20T21:05:00+08:00",
   "市医院重复上报并入同一暴露事件",
   {
       "surviving_exposure_id": EXP,
       "merged_exposure_ids": [DUP],
       "merge_basis": [
           {"kind": "SAME_MEAL_TIME_PLACE", "detail": "两院上报餐次均为 2026-09-20 12:00 团结村3组张家"},
           {"kind": "SAME_PATIENT_IDENTITY", "detail": f"市医院患儿 {PC} 与县医院登记的同桌人员为同一人"},
       ],
       "merged_at": "2026-09-20T21:05:00+08:00",
       "decided_by": "STAFF-CDC-LI",
   }, source_facility_id="CDC-REGIONAL")

# ── 病程建立 ────────────────────────────────────────────────────────────────
ev("COURSE_OPENED", "patient_course", CRSE_A, "2026-09-20T20:15:00+08:00",
   "患者 A（张某，男 52）县医院建档",
   {
       "patient": {"patient_id": PA, "initials": "张*", "age": 52, "sex": "M"},
       "facility_id": F_COUNTY,
       "role_in_meal": "DINER",
       "ate_mushroom_dish": True,
       "first_bite_at": "2026-09-20T12:00:00+08:00",
       "latent_period_minutes": 390,
   }, source_facility_id=F_COUNTY)

ev("COURSE_OPENED", "patient_course", CRSE_B, "2026-09-20T20:18:00+08:00",
   "患者 B（李某，女 48）县医院建档",
   {
       "patient": {"patient_id": PB, "initials": "李*", "age": 48, "sex": "F"},
       "facility_id": F_COUNTY,
       "role_in_meal": "DINER",
       "ate_mushroom_dish": True,
       "first_bite_at": "2026-09-20T12:00:00+08:00",
       "latent_period_minutes": 420,
   }, source_facility_id=F_COUNTY)

ev("COURSE_OPENED", "patient_course", CRSE_C, "2026-09-20T20:40:00+08:00",
   "患儿 C（张某之子，10 岁）市医院建档（合并前挂在重复编号下，记录保留）",
   {
       "patient": {"patient_id": PC, "initials": "张*某", "age": 10, "sex": "M"},
       "facility_id": F_CITY,
       "role_in_meal": "DINER",
       "ate_mushroom_dish": True,
       "first_bite_at": "2026-09-20T12:10:00+08:00",
       "latent_period_minutes": 400,
   }, exposure_id=DUP, source_facility_id=F_CITY)

# ── 同桌关系：仅用于本次风险通知，不互开病历 ─────────────────────────────────
def relation(agg, ts, subj, other, no):
    ev("PERSON_RELATION_LINKED", "patient_course", agg, ts,
        f"同桌风险通知关联（{no}）：仅告警，不共享病历",
        {
            "subject_patient_id": subj,
            "related_patient_id": other,
            "relation": "CO_DINER",
            "consent_scope": "CO_DINER_ALERT_ONLY",
            "alert_message_ref": "TPL-CO-DINER-MUSHROOM-ALERT-V1",
        })

relation(CRSE_A, "2026-09-20T20:45:00+08:00", PA, PB, "A-B")
relation(CRSE_A, "2026-09-20T20:46:00+08:00", PA, PC, "A-C")
relation(CRSE_B, "2026-09-20T20:47:00+08:00", PB, PC, "B-C")

# ── 首发症状（胃肠炎期）──────────────────────────────────────────────────────
ev("SYMPTOM_RECORDED", "patient_course", CRSE_A, "2026-09-20T20:20:00+08:00",
   "A：18:30 起呕吐、腹泻",
   {"symptoms": [
       {"name": "VOMITING", "status": "ACTIVE", "onset_at": "2026-09-20T18:30:00+08:00"},
       {"name": "DIARRHEA", "status": "ACTIVE", "onset_at": "2026-09-20T18:40:00+08:00"},
   ], "recorded_by": "STAFF-COUNTY-WANG"})

ev("SYMPTOM_RECORDED", "patient_course", CRSE_B, "2026-09-20T20:25:00+08:00",
   "B：19:00 起呕吐",
   {"symptoms": [
       {"name": "VOMITING", "status": "ACTIVE", "onset_at": "2026-09-20T19:00:00+08:00"},
   ], "recorded_by": "STAFF-COUNTY-WANG"})

ev("SYMPTOM_RECORDED", "patient_course", CRSE_C, "2026-09-20T20:42:00+08:00",
   "C：18:50 起恶心呕吐",
   {"symptoms": [
       {"name": "NAUSEA", "status": "ACTIVE", "onset_at": "2026-09-20T18:50:00+08:00"},
       {"name": "VOMITING", "status": "ACTIVE", "onset_at": "2026-09-20T19:10:00+08:00"},
   ], "recorded_by": "STAFF-CITY-ZHAO"}, exposure_id=DUP)

# ── 留观要求：潜伏期 >6h，按鹅膏毒肽风险至少留观 48h ────────────────────────
ev("OBSERVATION_REQUIRED", "patient_course", CRSE_A, "2026-09-20T20:30:00+08:00",
   "A：建立留观，要求至 09-22 12:00",
   {"patient_id": PA, "state": "UNDER_OBSERVATION",
    "required_until": "2026-09-22T12:00:00+08:00",
    "observing_facility_id": F_COUNTY,
    "rationale": ["LATENT_OVER_6H_RISK", "SUSPECTED_AMANITIN", "CO_DINER_SEVERE_CASE"],
    "set_by": "STAFF-COUNTY-WANG"})

ev("OBSERVATION_REQUIRED", "patient_course", CRSE_B, "2026-09-20T20:35:00+08:00",
   "B：建立留观，要求至 09-22 12:00",
   {"patient_id": PB, "state": "UNDER_OBSERVATION",
    "required_until": "2026-09-22T12:00:00+08:00",
    "observing_facility_id": F_COUNTY,
    "rationale": ["LATENT_OVER_6H_RISK", "SUSPECTED_AMANITIN"],
    "set_by": "STAFF-COUNTY-WANG"})

ev("OBSERVATION_REQUIRED", "patient_course", CRSE_C, "2026-09-20T20:50:00+08:00",
   "C：市医院建立留观，要求至 09-22 12:00",
   {"patient_id": PC, "state": "UNDER_OBSERVATION",
    "required_until": "2026-09-22T12:00:00+08:00",
    "observing_facility_id": F_CITY,
    "rationale": ["LATENT_OVER_6H_RISK", "SUSPECTED_AMANITIN"],
    "set_by": "STAFF-CITY-ZHAO"}, exposure_id=DUP)

# ── 样本：剩余蘑菇 + 血样/呕吐物，封签入库 ──────────────────────────────────
ev("SPECIMEN_COLLECTED", "specimen_chain", "SP-MUSH-01", "2026-09-20T20:15:00+08:00",
   "家属送来的剩余野生蘑菇",
   {"specimen_id": "SP-MUSH-01", "patient_id": PA, "material": "LEFTOVER_MUSHROOM",
    "collected_at": "2026-09-20T20:15:00+08:00", "facility_id": F_COUNTY,
    "seal": {"seal_id": "SEAL-M1", "sealed_by": "STAFF-COUNTY-NURSE1",
             "seal_intact_at_collection": True, "photo_ref": "MEDIA/SEAL-M1.jpg"}})

ev("SPECIMEN_COLLECTED", "specimen_chain", "SP-BLOOD-A-1", "2026-09-20T20:40:00+08:00",
   "A 首管血样",
   {"specimen_id": "SP-BLOOD-A-1", "patient_id": PA, "material": "BLOOD",
    "collected_at": "2026-09-20T20:40:00+08:00", "facility_id": F_COUNTY,
    "seal": {"seal_id": "SEAL-A1", "sealed_by": "STAFF-COUNTY-NURSE1",
             "seal_intact_at_collection": True}})

ev("SPECIMEN_COLLECTED", "specimen_chain", "SP-VOM-B-1", "2026-09-20T20:45:00+08:00",
   "B 呕吐物（与剩余蘑菇同源留样）",
   {"specimen_id": "SP-VOM-B-1", "patient_id": PB, "material": "VOMITUS",
    "collected_at": "2026-09-20T20:45:00+08:00", "facility_id": F_COUNTY,
    "seal": {"seal_id": "SEAL-B1", "sealed_by": "STAFF-COUNTY-NURSE1",
             "seal_intact_at_collection": True},
    "parent_specimen_id": "SP-MUSH-01"})

ev("SPECIMEN_COLLECTED", "specimen_chain", "SP-BLOOD-C-1", "2026-09-20T20:55:00+08:00",
   "C 首管血样（市医院）",
   {"specimen_id": "SP-BLOOD-C-1", "patient_id": PC, "material": "BLOOD",
    "collected_at": "2026-09-20T20:55:00+08:00", "facility_id": F_CITY,
    "seal": {"seal_id": "SEAL-C1", "sealed_by": "STAFF-CITY-NURSE2",
             "seal_intact_at_collection": True}}, exposure_id=DUP)

# ── 21:10 剩余蘑菇一拆三：本院化验 / 区域毒理 / 疾控留样，分别加封 ─────────
ev("SPECIMEN_SPLIT_PREPARED", "specimen_chain", "SP-MUSH-01", "2026-09-20T21:10:00+08:00",
   "剩余蘑菇拆分送检：本院、区域毒理实验室、疾控各一支",
   {"parent_specimen_id": "SP-MUSH-01",
    "child_specimens": [
        {"specimen_id": "SP-MUSH-01-LOCAL", "purpose": "LOCAL_LAB",
         "seal": {"seal_id": "SEAL-M1L", "sealed_by": "STAFF-COUNTY-WANG"},
         "volume_or_mass": "约20g"},
        {"specimen_id": "SP-MUSH-01-TOX", "purpose": "REGIONAL_TOX_LAB",
         "seal": {"seal_id": "SEAL-M1T", "sealed_by": "STAFF-COUNTY-WANG"},
         "volume_or_mass": "约30g"},
        {"specimen_id": "SP-MUSH-01-CDC", "purpose": "CDC_REFERENCE",
         "seal": {"seal_id": "SEAL-M1C", "sealed_by": "STAFF-CDC-LI"},
         "volume_or_mass": "约30g"},
    ],
    "split_at": "2026-09-20T21:10:00+08:00",
    "split_by": "STAFF-COUNTY-WANG"},
  correlation_id="SPLIT-0920-01")

# ── 毒理分支转运 → 接收 → 封签核对 ─────────────────────────────────────────
ev("SPECIMEN_TRANSFERRED", "specimen_chain", "XFER-0920-01", "2026-09-20T21:20:00+08:00",
   "毒理分支由疾控冷链寄送区域中心",
   {"specimen_ids": ["SP-MUSH-01-TOX"],
    "from_facility_id": F_COUNTY, "to_facility_id": F_REGIONAL,
    "transferred_at": "2026-09-20T21:20:00+08:00", "transferred_by": "STAFF-COUNTY-WANG",
    "seal_ids": ["SEAL-M1T"],
    "carrier": {"type": "CDC_COURIER", "tracking_no": "CDC-COLD-7741"}},
  correlation_id="SPLIT-0920-01")

ev("SPECIMEN_RECEIVED", "specimen_chain", "XFER-0920-01", "2026-09-20T21:50:00+08:00",
   "区域中心签收毒理分支，封签完好",
   {"specimen_ids": ["SP-MUSH-01-TOX"],
    "receiving_facility_id": F_REGIONAL,
    "received_at": "2026-09-20T21:50:00+08:00", "received_by": "STAFF-REGIONAL-LAB1",
    "seal_checks": [
        {"specimen_id": "SP-MUSH-01-TOX", "seal_id": "SEAL-M1T", "intact": True},
    ]}, correlation_id="SPLIT-0920-01")

ev("SPECIMEN_SEAL_VERIFIED", "specimen_chain", "SP-MUSH-01-TOX", "2026-09-20T21:55:00+08:00",
   "区域中心独立复核封签与拆检谱系一致",
   {"specimen_id": "SP-MUSH-01-TOX", "seal_id": "SEAL-M1T", "intact": True,
    "verified_at": "2026-09-20T21:55:00+08:00",
    "verified_by": "STAFF-REGIONAL-LAB1"}, correlation_id="SPLIT-0920-01")

# ── A 入院首轮化验：基本正常（胃肠炎期）──────────────────────────────────────
ev("LAB_PANEL_RECORDED", "lab_panel", "LP-A-1", "2026-09-20T21:00:00+08:00",
   "A 入院首轮肝肾指标基本正常",
   {"patient_id": PA, "specimen_id": "SP-BLOOD-A-1",
    "drawn_at": "2026-09-20T20:45:00+08:00", "facility_id": F_COUNTY,
    "metrics": [
        metric("ALT", 32, "U/L", False, 400, 7, 40),
        metric("AST", 28, "U/L", False, 400, 13, 35),
        metric("Cr", 70, "umol/L", False, 300, 57, 111),
    ]})

# ── B 进入假愈期：症状暂缓，登记反复窗口，明确告知不等于痊愈 ────────────────
ev("SYMPTOM_PAUSED", "patient_course", CRSE_B, "2026-09-21T02:00:00+08:00",
   "B：呕吐暂缓（疑似假愈期），留观不解除",
   {"patient_id": PB,
    "paused_symptoms": ["VOMITING"],
    "expected_resurgence_window": {"from": "2026-09-21T02:00:00+08:00",
                                   "until": "2026-09-21T20:00:00+08:00"},
    "recorded_by": "STAFF-COUNTY-WANG",
    "warning_given": True})

# ── B 自觉好转坚持离院：不是结案，留观与复诊责任保持开放 ────────────────────
ev("OBSERVATION_LEFT_AGAINST_ADVICE", "patient_course", CRSE_B, "2026-09-21T08:30:00+08:00",
   "B 自觉症状消失，自行签字离院；留观责任不闭环",
   {"patient_id": PB, "state": "LEFT_AGAINST_ADVICE",
    "left_at": "2026-09-21T08:30:00+08:00",
    "warning_delivered": {
        "contents": ["FALSE_RECOVERY_EXPLAINED", "RETURN_IF_SYMPTOMS_RETURN",
                     "LIVER_KIDNEY_RISK", "FOLLOWUP_OBLIGATION"],
        "delivered_by": "STAFF-COUNTY-WANG",
        "signed_ref": "FORM-LAA-0920-B",
    },
    "open_liability": {
        "observation_still_due": True,
        "followup_still_due": True,
        "contact_plan": {"channel": "SMS",
                         "next_attempt_at": "2026-09-21T12:00:00+08:00"},
    }})

# ── A 凌晨出现黄疸，06:00 复查 ALT/AST 破危急值 ─────────────────────────────
ev("SYMPTOM_RECORDED", "patient_course", CRSE_A, "2026-09-21T05:30:00+08:00",
   "A：出现皮肤巩膜黄染、乏力",
   {"symptoms": [
       {"name": "JAUNDICE", "status": "ACTIVE", "onset_at": "2026-09-21T05:00:00+08:00"},
       {"name": "FATIGUE", "status": "ACTIVE"},
   ], "recorded_by": "STAFF-COUNTY-NIGHT2"})

lp_a2 = ev("LAB_PANEL_RECORDED", "lab_panel", "LP-A-2", "2026-09-21T06:00:00+08:00",
           "A 复查肝酶急升，ALT/AST 达危急值",
           {"patient_id": PA, "specimen_id": "SP-BLOOD-A-1",
            "drawn_at": "2026-09-21T05:45:00+08:00", "facility_id": F_COUNTY,
            "metrics": [
                metric("ALT", 420, "U/L", True, 400, 7, 40),
                metric("AST", 510, "U/L", True, 400, 13, 35),
                metric("Cr", 92, "umol/L", False, 300, 57, 111),
            ]})

alert_a = ev("CRITICAL_ALERT_RAISED", "critical_alert", "ALERT-A-1",
             "2026-09-21T06:05:00+08:00",
             "A：ALT 420 超危急阈值，提醒主治与中毒中心值班",
             {"patient_id": PA, "lab_panel_id": "LP-A-2",
              "metric_code": "ALT", "value": 420, "unit": "U/L", "threshold": 400,
              "raised_at": "2026-09-21T06:05:00+08:00",
              "deliver_to_roles": ["ATTENDING_PHYSICIAN", "ON_CALL_POISON_CENTER"],
              "auto_close_forbidden": True},
             causation_id=lp_a2["event_id"])

ev("CRITICAL_ALERT_ACKNOWLEDGED", "critical_alert", "ALERT-A-1", "2026-09-21T06:12:00+08:00",
   "县医院主治确认危急值，决定升级救治并联系区域中心",
   {"alert_id": "ALERT-A-1", "acknowledged_by": "STAFF-COUNTY-WANG",
    "acknowledged_at": "2026-09-21T06:12:00+08:00",
    "clinical_decision": {"decision": "ESCALATE_CARE",
                          "note": "肝损伤进展快，启动水飞蓟素，申请转区域中毒救治中心"}},
   causation_id=alert_a["event_id"])

ev("TREATMENT_ORDERED", "patient_course", CRSE_A, "2026-09-21T06:20:00+08:00",
   "A：活性炭、补液、水飞蓟素",
   {"patient_id": PA,
    "orders": [
        {"code": "ACTIVATED_CHARCOAL", "status": "STARTED",
         "started_at": "2026-09-21T06:15:00+08:00", "dose": "50g"},
        {"code": "IV_FLUID", "status": "STARTED",
         "started_at": "2026-09-21T06:10:00+08:00"},
        {"code": "SILIBININ", "status": "ORDERED", "dose": "按方案"},
    ],
    "ordered_by": "STAFF-COUNTY-WANG",
    "ordered_at": "2026-09-21T06:20:00+08:00",
    "based_on_lab_panel_id": "LP-A-2"})

ev("OBSERVATION_EXTENDED", "patient_course", CRSE_A, "2026-09-21T06:25:00+08:00",
   "A：肝酶危急，留观延长至 09-23 12:00",
   {"patient_id": PA, "state": "EXTENDED",
    "previous_until": "2026-09-22T12:00:00+08:00",
    "extended_until": "2026-09-23T12:00:00+08:00",
    "reason": {"kind": "CRITICAL_ALERT", "ref_event_id": alert_a["event_id"],
               "detail": "ALT/AST 危急值，多器官损伤风险"},
    "set_by": "STAFF-COUNTY-WANG"})

# ── A 转区域中心：发起交接，血样随车 ────────────────────────────────────────
handoff_a_records = [
    ("EXPOSURE", "EV-001"),
    ("CDC_SOURCE", "EV-001"),
    ("COURSE", "EV-004"),
    ("SYMPTOM_TIMELINE", "EV-010"),
    ("LAB_PANEL", "LP-A-1"),
    ("LAB_PANEL", "LP-A-2"),
    ("TREATMENT", "EV-031"),
    ("OBSERVATION", "EV-013"),
    ("OBSERVATION", "EV-032"),
]
ev("HANDOFF_INITIATED", "care_handoff", "HO-A-1", "2026-09-21T10:00:00+08:00",
   "A：县医院发起向区域中毒救治中心的交接",
   {"handoff_id": "HO-A-1", "patient_id": PA,
    "from_facility_id": F_COUNTY, "to_facility_id": F_REGIONAL,
    "initiated_at": "2026-09-21T10:00:00+08:00",
    "reason": "SPECIALIST_CENTER",
    "package": {
        "records": [{"kind": k, "event_id": e} for k, e in handoff_a_records],
        "specimens": [
            {"specimen_id": "SP-BLOOD-A-1", "seal_id": "SEAL-A1", "status": "IN_TRANSIT"},
            {"specimen_id": "SP-MUSH-01-TOX", "seal_id": "SEAL-M1T", "status": "RESULT_PENDING"},
        ],
        "open_actions": [
            {"action_code": "CONTINUE_OBSERVATION_UNTIL",
             "due_at": "2026-09-23T12:00:00+08:00", "status": "OPEN"},
            {"action_code": "REPEAT_LIVER_KIDNEY_LABS",
             "due_at": "2026-09-21T12:30:00+08:00", "status": "OPEN"},
            {"action_code": "AWAIT_TOX_RESULT",
             "due_at": "2026-09-21T18:00:00+08:00", "status": "OPEN"},
        ],
    }})

ev("SPECIMEN_TRANSFERRED", "specimen_chain", "XFER-0921-A", "2026-09-21T10:10:00+08:00",
   "A 血样随救护车专人转送区域中心",
   {"specimen_ids": ["SP-BLOOD-A-1"],
    "from_facility_id": F_COUNTY, "to_facility_id": F_REGIONAL,
    "transferred_at": "2026-09-21T10:10:00+08:00", "transferred_by": "STAFF-COUNTY-EMS1",
    "seal_ids": ["SEAL-A1"],
    "carrier": {"type": "STAFF_HANDCARRY", "tracking_no": "AMB-226"}})

ev("SPECIMEN_RECEIVED", "specimen_chain", "XFER-0921-A", "2026-09-21T11:05:00+08:00",
   "区域中心签收 A 血样，封签完好",
   {"specimen_ids": ["SP-BLOOD-A-1"],
    "receiving_facility_id": F_REGIONAL,
    "received_at": "2026-09-21T11:05:00+08:00", "received_by": "STAFF-REGIONAL-NURSE3",
    "seal_checks": [
        {"specimen_id": "SP-BLOOD-A-1", "seal_id": "SEAL-A1", "intact": True},
    ]})

ev("HANDOFF_ACKNOWLEDGED", "care_handoff", "HO-A-1", "2026-09-21T11:20:00+08:00",
   "区域中心主治逐项确认资料、样本与待办，接收 A",
   {"handoff_id": "HO-A-1",
    "acknowledged_at": "2026-09-21T11:20:00+08:00",
    "acknowledged_by": {"receiving_facility_id": F_REGIONAL,
                        "physician_id": "STAFF-REGIONAL-CHEN"},
    "confirmations": {
        "records_received": [
            {"kind": k, "event_id": e, "present": True} for k, e in handoff_a_records
        ],
        "specimens_accounted": [
            {"specimen_id": "SP-BLOOD-A-1", "seal_id": "SEAL-A1",
             "seal_intact": True, "accounted": True},
            {"specimen_id": "SP-MUSH-01-TOX", "seal_id": "SEAL-M1T",
             "seal_intact": True, "accounted": True},
        ],
        "open_actions_accepted": [
            {"action_code": "CONTINUE_OBSERVATION_UNTIL", "accepted": True,
             "due_at": "2026-09-23T12:00:00+08:00"},
            {"action_code": "REPEAT_LIVER_KIDNEY_LABS", "accepted": True,
             "due_at": "2026-09-21T12:30:00+08:00"},
            {"action_code": "AWAIT_TOX_RESULT", "accepted": True,
             "due_at": "2026-09-21T18:00:00+08:00"},
        ],
    }})

# ── 毒理结果：α-鹅膏毒肽检出（从结果可回溯母体与封签）──────────────────────
tox = ev("TOXICOLOGY_RESULTED", "tox_result", "TOX-M1T-1", "2026-09-21T14:00:00+08:00",
         "区域毒理实验室：剩余蘑菇检出 α-鹅膏毒肽",
         {"specimen_id": "SP-MUSH-01-TOX",
          "results": [
              {"analyte": "AMATOXIN_ALPHA_AMANITIN", "detected": True,
               "concentration": "检出（定量见附件）", "method": "LC_MS_MS",
               "result_status": "CONFIRMED"},
              {"analyte": "AMATOXIN_BETA_AMANITIN", "detected": True,
               "concentration": "检出", "method": "LC_MS_MS",
               "result_status": "CONFIRMED"},
          ],
          "reported_at": "2026-09-21T14:00:00+08:00",
          "lab_facility_id": F_REGIONAL,
          "lineage": {"root_specimen_id": "SP-MUSH-01",
                      "parent_specimen_id": "SP-MUSH-01",
                      "seal_id": "SEAL-M1T",
                      "split_batch_correlation_id": "SPLIT-0920-01"}},
         correlation_id="SPLIT-0920-01")

ev("TREATMENT_ORDERED", "patient_course", CRSE_A, "2026-09-21T14:30:00+08:00",
   "A：毒理确诊后继续水飞蓟素并安排血液净化评估",
   {"patient_id": PA,
    "orders": [
        {"code": "SILIBININ", "status": "STARTED",
         "started_at": "2026-09-21T14:30:00+08:00", "dose": "按方案维持"},
        {"code": "BLOOD_PURIFICATION", "status": "ORDERED"},
        {"code": "LIVER_TRANSPLANT_EVAL", "status": "ORDERED"},
    ],
    "ordered_by": "STAFF-REGIONAL-CHEN",
    "ordered_at": "2026-09-21T14:30:00+08:00",
    "based_on_tox_result_id": "TOX-M1T-1"})

# ── B 症状反复被召回，重新留观，复查再达危急值 ─────────────────────────────
ev("SYMPTOM_RECORDED", "patient_course", CRSE_B, "2026-09-21T15:40:00+08:00",
   "B：居家再次呕吐、尿色加深，短信召回后返院",
   {"symptoms": [
       {"name": "VOMITING", "status": "ACTIVE", "onset_at": "2026-09-21T15:00:00+08:00"},
       {"name": "OLIGURIA", "status": "ACTIVE", "onset_at": "2026-09-21T15:20:00+08:00"},
   ], "recorded_by": "STAFF-COUNTY-WANG"})

ev("OBSERVATION_RETURNED", "patient_course", CRSE_B, "2026-09-21T16:20:00+08:00",
   "B：症状反复返院，恢复留观",
   {"patient_id": PB, "state": "RETURNED_TO_OBSERVATION",
    "returned_at": "2026-09-21T16:20:00+08:00", "facility_id": F_COUNTY,
    "trigger": {"kind": "SYMPTOM_RESURGENCE"}})

lp_b1 = ev("LAB_PANEL_RECORDED", "lab_panel", "LP-B-1", "2026-09-21T16:40:00+08:00",
           "B 复查 ALT 470，达危急值",
           {"patient_id": PB,
            "drawn_at": "2026-09-21T16:30:00+08:00", "facility_id": F_COUNTY,
            "metrics": [
                metric("ALT", 470, "U/L", True, 400, 7, 40),
                metric("AST", 380, "U/L", False, 400, 13, 35),
                metric("Cr", 132, "umol/L", False, 300, 57, 111),
            ]})

alert_b = ev("CRITICAL_ALERT_RAISED", "critical_alert", "ALERT-B-1",
             "2026-09-21T16:45:00+08:00",
             "B：ALT 470 危急，提醒主治与中毒中心",
             {"patient_id": PB, "lab_panel_id": "LP-B-1",
              "metric_code": "ALT", "value": 470, "unit": "U/L", "threshold": 400,
              "raised_at": "2026-09-21T16:45:00+08:00",
              "deliver_to_roles": ["ATTENDING_PHYSICIAN", "ON_CALL_POISON_CENTER"],
              "auto_close_forbidden": True},
             causation_id=lp_b1["event_id"])

ev("CRITICAL_ALERT_ACKNOWLEDGED", "critical_alert", "ALERT-B-1", "2026-09-21T16:50:00+08:00",
   "医生确认 B 的危急值，决定延长留观并转区域中心",
   {"alert_id": "ALERT-B-1", "acknowledged_by": "STAFF-COUNTY-WANG",
    "acknowledged_at": "2026-09-21T16:50:00+08:00",
    "clinical_decision": {"decision": "TRANSFER",
                          "note": "假愈期后肝损明确，转区域中心接续治疗"}},
   causation_id=alert_b["event_id"])

ev("OBSERVATION_EXTENDED", "patient_course", CRSE_B, "2026-09-21T16:52:00+08:00",
   "B：危急值 + 毒理阳性，留观延长至 09-23 18:00",
   {"patient_id": PB, "state": "EXTENDED",
    "previous_until": "2026-09-22T12:00:00+08:00",
    "extended_until": "2026-09-23T18:00:00+08:00",
    "reason": {"kind": "TOX_POSITIVE", "ref_event_id": tox["event_id"],
               "detail": "同桌剩余蘑菇 α-鹅膏毒肽阳性，本人肝酶危急"},
    "set_by": "STAFF-COUNTY-WANG"})

# ── B 转区域中心：呕吐物样本留存县医院，同样须逐项点收确认 ──────────────────
handoff_b_records = [
    ("EXPOSURE", "EV-001"),
    ("COURSE", "EV-005"),
    ("SYMPTOM_TIMELINE", "EV-011"),
    ("OBSERVATION", "EV-014"),
    ("OBSERVATION", "EV-026"),
    ("OBSERVATION", "EV-044"),
    ("LAB_PANEL", "LP-B-1"),
]
ev("HANDOFF_INITIATED", "care_handoff", "HO-B-1", "2026-09-21T17:30:00+08:00",
   "B：县医院发起交接（含自行离院与召回记录）",
   {"handoff_id": "HO-B-1", "patient_id": PB,
    "from_facility_id": F_COUNTY, "to_facility_id": F_REGIONAL,
    "initiated_at": "2026-09-21T17:30:00+08:00",
    "reason": "CRITICAL_ESCALATION",
    "package": {
        "records": [{"kind": k, "event_id": e} for k, e in handoff_b_records],
        "specimens": [
            {"specimen_id": "SP-VOM-B-1", "seal_id": "SEAL-B1",
             "status": "RETAINED_AT_SENDER"},
        ],
        "open_actions": [
            {"action_code": "CONTINUE_OBSERVATION_UNTIL",
             "due_at": "2026-09-23T18:00:00+08:00", "status": "OPEN"},
            {"action_code": "AWAIT_TOX_RESULT",
             "due_at": "2026-09-22T20:00:00+08:00", "status": "OPEN",
             "detail": "呕吐物 SP-VOM-B-1 留存县医院，待区域中心安排取回送检"},
            {"action_code": "FOLLOWUP_CLINIC",
             "due_at": "2026-09-29T09:00:00+08:00", "status": "OPEN"},
        ],
    }})

ev("HANDOFF_ACKNOWLEDGED", "care_handoff", "HO-B-1", "2026-09-21T18:40:00+08:00",
   "区域中心确认 B 的资料、留存样本与待办",
   {"handoff_id": "HO-B-1",
    "acknowledged_at": "2026-09-21T18:40:00+08:00",
    "acknowledged_by": {"receiving_facility_id": F_REGIONAL,
                        "physician_id": "STAFF-REGIONAL-CHEN"},
    "confirmations": {
        "records_received": [
            {"kind": k, "event_id": e, "present": True} for k, e in handoff_b_records
        ],
        "specimens_accounted": [
            {"specimen_id": "SP-VOM-B-1", "seal_id": "SEAL-B1",
             "seal_intact": True, "accounted": True,
             "note": "留存县医院检验科冰箱 B-2，封签完好，09-22 取回"},
        ],
        "open_actions_accepted": [
            {"action_code": "CONTINUE_OBSERVATION_UNTIL", "accepted": True,
             "due_at": "2026-09-23T18:00:00+08:00"},
            {"action_code": "AWAIT_TOX_RESULT", "accepted": True,
             "due_at": "2026-09-22T20:00:00+08:00"},
            {"action_code": "FOLLOWUP_CLINIC", "accepted": True,
             "due_at": "2026-09-29T09:00:00+08:00"},
        ],
    }})

# ── C（市医院）：轻症、指标平稳，达到留观时长后医学解除并预约复诊 ───────────
ev("LAB_PANEL_RECORDED", "lab_panel", "LP-C-1", "2026-09-21T09:00:00+08:00",
   "C 复查肝肾指标正常",
   {"patient_id": PC, "specimen_id": "SP-BLOOD-C-1",
    "drawn_at": "2026-09-21T08:30:00+08:00", "facility_id": F_CITY,
    "metrics": [
        metric("ALT", 24, "U/L", False, 400, 7, 40),
        metric("AST", 26, "U/L", False, 400, 13, 35),
        metric("Cr", 58, "umol/L", False, 300, 40, 90),
    ]})

ev("FOLLOWUP_SCHEDULED", "follow_up", "FU-C-1", "2026-09-22T10:00:00+08:00",
   "C：解除前预约 09-24 复诊复查肝肾",
   {"patient_id": PC, "scheduled_for": "2026-09-24T09:00:00+08:00",
    "facility_id": F_CITY,
    "reason_codes": ["POST_DISCHARGE", "LIVER_TREND", "KIDNEY_TREND"],
    "required_labs": ["ALT", "AST", "Cr"]})

ev("OBSERVATION_DISCHARGED_MEDICALLY", "patient_course", CRSE_C,
   "2026-09-22T13:00:00+08:00",
   "C：达到留观时长、指标平稳，医生医学解除留观",
   {"patient_id": PC, "state": "MEDICALLY_DISCHARGED",
    "discharged_at": "2026-09-22T13:00:00+08:00",
    "discharged_by": {"physician_id": "STAFF-CITY-ZHAO", "facility_id": F_CITY,
                      "e_signature_ref": "ESIGN-CITY-7788"},
    "medical_basis": {
        "lab_trend": [{"panel_id": "LP-C-1", "assessment": "NORMAL"}],
        "clinical_assessment": "潜伏期大于 6 小时但满 48 小时留观，两轮肝肾指标正常，无症状反复",
        "observation_hours_met": True,
    },
    "follow_up_id": "FU-C-1"})

# ── A、B 继续复查，达到要求后陆续医学解除 ───────────────────────────────────
ev("LAB_PANEL_RECORDED", "lab_panel", "LP-A-3", "2026-09-22T06:00:00+08:00",
   "A：肝酶仍高但较前回落",
   {"patient_id": PA,
    "drawn_at": "2026-09-22T05:45:00+08:00", "facility_id": F_REGIONAL,
    "metrics": [
        metric("ALT", 260, "U/L", False, 400, 7, 40),
        metric("AST", 210, "U/L", False, 400, 13, 35),
        metric("Cr", 88, "umol/L", False, 300, 57, 111),
    ]})

ev("LAB_PANEL_RECORDED", "lab_panel", "LP-B-2", "2026-09-23T07:00:00+08:00",
   "B：肝酶明显回落",
   {"patient_id": PB,
    "drawn_at": "2026-09-23T06:30:00+08:00", "facility_id": F_REGIONAL,
    "metrics": [
        metric("ALT", 150, "U/L", False, 400, 7, 40),
        metric("AST", 96, "U/L", False, 400, 13, 35),
        metric("Cr", 95, "umol/L", False, 300, 57, 111),
    ]})

ev("FOLLOWUP_SCHEDULED", "follow_up", "FU-A-1", "2026-09-23T16:00:00+08:00",
   "A：解除前预约 09-27 门诊复诊",
   {"patient_id": PA, "scheduled_for": "2026-09-27T09:00:00+08:00",
    "facility_id": F_REGIONAL,
    "reason_codes": ["POST_DISCHARGE", "LIVER_TREND", "KIDNEY_TREND", "POST_FALSE_RECOVERY"],
    "required_labs": ["ALT", "AST", "TBIL", "Cr", "PT", "INR"]})

ev("LAB_PANEL_RECORDED", "lab_panel", "LP-A-4", "2026-09-24T07:00:00+08:00",
   "A：肝酶接近正常、凝血稳定",
   {"patient_id": PA,
    "drawn_at": "2026-09-24T06:30:00+08:00", "facility_id": F_REGIONAL,
    "metrics": [
        metric("ALT", 68, "U/L", False, 400, 7, 40),
        metric("AST", 55, "U/L", False, 400, 13, 35),
        metric("INR", 1.1, "ratio", False, 2.0, 0.8, 1.2),
    ]})

ev("OBSERVATION_DISCHARGED_MEDICALLY", "patient_course", CRSE_A,
   "2026-09-24T10:00:00+08:00",
   "A：超过延长留观期限、指标连续改善，医生医学解除",
   {"patient_id": PA, "state": "MEDICALLY_DISCHARGED",
    "discharged_at": "2026-09-24T10:00:00+08:00",
    "discharged_by": {"physician_id": "STAFF-REGIONAL-CHEN", "facility_id": F_REGIONAL,
                      "e_signature_ref": "ESIGN-REG-3301"},
    "medical_basis": {
        "lab_trend": [
            {"panel_id": "LP-A-3", "assessment": "STABLE"},
            {"panel_id": "LP-A-4", "assessment": "IMPROVING"},
        ],
        "clinical_assessment": "鹅膏毒肽中毒经水飞蓟素与血液净化后肝酶回落、凝血稳定，无症状反复",
        "observation_hours_met": True,
    },
    "follow_up_id": "FU-A-1"})

ev("FOLLOWUP_SCHEDULED", "follow_up", "FU-B-1", "2026-09-25T09:30:00+08:00",
   "B：解除前预约 09-29 复诊（曾自行离院，需重点追踪）",
   {"patient_id": PB, "scheduled_for": "2026-09-29T09:00:00+08:00",
    "facility_id": F_REGIONAL,
    "reason_codes": ["POST_DISCHARGE", "LIVER_TREND", "LEFT_AGAINST_ADVICE_RECALL"],
    "required_labs": ["ALT", "AST", "Cr"]})

ev("LAB_PANEL_RECORDED", "lab_panel", "LP-B-3", "2026-09-25T08:00:00+08:00",
   "B：肝酶基本恢复",
   {"patient_id": PB,
    "drawn_at": "2026-09-25T07:30:00+08:00", "facility_id": F_REGIONAL,
    "metrics": [
        metric("ALT", 55, "U/L", False, 400, 7, 40),
        metric("AST", 42, "U/L", False, 400, 13, 35),
        metric("Cr", 81, "umol/L", False, 300, 57, 111),
    ]})

ev("OBSERVATION_DISCHARGED_MEDICALLY", "patient_course", CRSE_B,
   "2026-09-25T11:00:00+08:00",
   "B：达到延长留观期限且指标改善，医生医学解除（既往自行离院已闭环）",
   {"patient_id": PB, "state": "MEDICALLY_DISCHARGED",
    "discharged_at": "2026-09-25T11:00:00+08:00",
    "discharged_by": {"physician_id": "STAFF-REGIONAL-CHEN", "facility_id": F_REGIONAL,
                      "e_signature_ref": "ESIGN-REG-3302"},
    "medical_basis": {
        "lab_trend": [
            {"panel_id": "LP-B-2", "assessment": "IMPROVING"},
            {"panel_id": "LP-B-3", "assessment": "IMPROVING"},
        ],
        "clinical_assessment": "假愈期后肝损经治疗恢复，复诊追踪安排到位",
        "observation_hours_met": True,
    },
    "follow_up_id": "FU-B-1"})

# ── 复诊完成 ────────────────────────────────────────────────────────────────
ev("LAB_PANEL_RECORDED", "lab_panel", "LP-C-2", "2026-09-24T08:40:00+08:00",
   "C：复诊抽血，肝肾正常",
   {"patient_id": PC,
    "drawn_at": "2026-09-24T08:30:00+08:00", "facility_id": F_CITY,
    "metrics": [
        metric("ALT", 20, "U/L", False, 400, 7, 40),
        metric("AST", 22, "U/L", False, 400, 13, 35),
        metric("Cr", 55, "umol/L", False, 300, 40, 90),
    ]})

ev("FOLLOWUP_COMPLETED", "follow_up", "FU-C-1", "2026-09-24T09:30:00+08:00",
   "C：复诊完成，临床痊愈",
   {"follow_up_id": "FU-C-1", "patient_id": PC,
    "completed_at": "2026-09-24T09:30:00+08:00", "lab_panel_id": "LP-C-2",
    "outcome": {"status": "RESOLVED", "note": "肝肾指标正常，无不适"}})

ev("FOLLOWUP_COMPLETED", "follow_up", "FU-A-1", "2026-09-27T09:40:00+08:00",
   "A：复诊完成，肝酶恢复，临床痊愈",
   {"follow_up_id": "FU-A-1", "patient_id": PA,
    "completed_at": "2026-09-27T09:40:00+08:00",
    "outcome": {"status": "RESOLVED", "note": "ALT 40，凝血正常，继续门诊随访建议"}})

ev("FOLLOWUP_COMPLETED", "follow_up", "FU-B-1", "2026-09-29T09:30:00+08:00",
   "B：复诊完成，指标恢复，失访风险解除",
   {"follow_up_id": "FU-B-1", "patient_id": PB,
    "completed_at": "2026-09-29T09:30:00+08:00",
    "outcome": {"status": "RESOLVED", "note": "ALT 36，Cr 正常，本人已能正常进食"}})

# 按各聚合 occurred_at 顺序统一编 version
_by_agg: dict[str, list[dict]] = {}
for r in events:
    _by_agg.setdefault(r["aggregate_id"], []).append(r)
for _rows in _by_agg.values():
    for _i, _r in enumerate(sorted(_rows, key=lambda r: r["occurred_at"]), 1):
        _r["version"] = _i

out = Path(__file__).resolve().parents[1] / "data" / "scenario.json"
out.write_text(json.dumps({"events": events}, ensure_ascii=False, indent=2) + "\n",
               encoding="utf-8")
print(f"wrote {len(events)} events to {out}")
