"""毒蘑菇暴露留观领域事件校验。

两层校验：

- validate_event(record)：单事件是否符合 contracts/domain.schema.json
  （信封、事件枚举、按 event_type 分派的 payload 结构）。
- validate_stream(events)：跨事件不变量——留观状态机（症状暂缓≠医学解除）、
  危急值必须由医生确认、样本封签与拆分谱系、转院资料/样本/待办的逐项确认、
  多院重复上报合并后编号统一等。

不依赖第三方库：内置一个只覆盖本契约所用特性的 JSON Schema 子集校验器。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "exposure_id",
    "occurred_at",
    "version",
    "summary",
    "payload",
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "domain.schema.json"


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


SCHEMA = _load_schema()


# ---------------------------------------------------------------------------
# 极简 JSON Schema 子集校验器（type / required / properties / items / enum /
# const / $ref(#/$defs/...) / minLength / minItems / minimum / additionalProperties /
# format:date-time）
# ---------------------------------------------------------------------------

def _type_ok(value: Any, json_type: str) -> bool:
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    return True


def _check(node: Any, schema: dict, path: str, errors: list[str], root: dict) -> None:
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            errors.append(f"{path}：不支持的引用 {ref}")
            return
        _check(node, root["$defs"][ref.rsplit("/", 1)[-1]], path, errors, root)
        # 允许 $ref 与同级约束并存（本契约里没有，但保持直觉）
        schema = {k: v for k, v in schema.items() if k != "$ref"}
        if not schema:
            return

    expected_type = schema.get("type")
    if expected_type and not _type_ok(node, expected_type):
        errors.append(f"{path}：应为 {expected_type}")
        return

    if "enum" in schema and node not in schema["enum"]:
        errors.append(f"{path}：取值 {node!r} 不在允许枚举内")
    if "const" in schema and node != schema["const"]:
        errors.append(f"{path}：必须等于 {schema['const']!r}")
    if isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            errors.append(f"{path}：字符串长度不足")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(node)
            except ValueError:
                errors.append(f"{path}：不是合法的 date-time")
    if isinstance(node, int) and not isinstance(node, bool) and "minimum" in schema and node < schema["minimum"]:
        errors.append(f"{path}：小于最小值 {schema['minimum']}")

    if isinstance(node, dict):
        for name in schema.get("required", []):
            if name not in node:
                errors.append(f"{path}：缺少字段 {name}")
        props = schema.get("properties", {})
        for name, value in node.items():
            if name in props:
                _check(value, props[name], f"{path}.{name}", errors, root)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}：不允许的额外字段")

    if isinstance(node, list):
        if "minItems" in schema and len(node) < schema["minItems"]:
            errors.append(f"{path}：至少需要 {schema['minItems']} 项")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(node):
                _check(item, item_schema, f"{path}[{i}]", errors, root)


def validate_event(record: dict) -> list[str]:
    """校验单个领域事件，返回中文错误信息列表；空列表表示通过。"""
    errors: list[str] = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if errors:
        return errors

    if not isinstance(record, dict):
        return ["记录必须是对象"]

    _check(record, {k: SCHEMA[k] for k in ("type", "required", "properties", "additionalProperties")
                    if k in SCHEMA}, "$", errors, SCHEMA)

    event_type = record.get("event_type")
    for clause in SCHEMA.get("allOf", []):
        cond = clause["if"]
        wanted = cond.get("properties", {}).get("event_type", {}).get("const")
        if wanted == event_type:
            then = clause["then"]
            if "aggregate_type" in then["properties"]:
                _check(record["aggregate_type"], then["properties"]["aggregate_type"],
                       "$.aggregate_type", errors, SCHEMA)
            _check(record.get("payload"), then["properties"]["payload"], "$.payload", errors, SCHEMA)
    return errors


# ---------------------------------------------------------------------------
# 事件流（跨事件）不变量
# ---------------------------------------------------------------------------

def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _err(event_id: str | None, code: str, message: str) -> str:
    prefix = f"[{event_id}] " if event_id else ""
    return f"{prefix}{code}：{message}"


def validate_stream(events: list[dict]) -> list[str]:
    """按时间顺序校验整条事件流的业务不变量。

    事件可乱序传入，内部按 occurred_at 排序。返回错误信息列表（空=通过）。
    """
    errors: list[str] = []

    # 先过单事件结构
    for ev in events:
        eid = ev.get("event_id", "?")
        for msg in validate_event(ev):
            errors.append(_err(eid, "SCHEMA", msg))
    if errors:
        return errors  # 结构不合法时跨事件规则没有意义

    ordered = sorted(events, key=lambda e: _parse_ts(e["occurred_at"]))

    seen_event_ids: set[str] = set()
    for ev in ordered:
        if ev["event_id"] in seen_event_ids:
            errors.append(_err(ev["event_id"], "EVENT_ID_DUP", "event_id 重复"))
        seen_event_ids.add(ev["event_id"])
        if ev.get("causation_id") and ev["causation_id"] not in seen_event_ids \
                and not any(e["event_id"] == ev["causation_id"] for e in ordered):
            errors.append(_err(ev["event_id"], "CAUSATION_MISSING",
                               f"causation_id {ev['causation_id']} 在流中不存在"))

    # 同一聚合内 version 随时间单调递增
    agg_versions: dict[str, list[tuple[datetime, int, str]]] = {}
    for ev in ordered:
        agg_versions.setdefault(ev["aggregate_id"], []).append(
            (_parse_ts(ev["occurred_at"]), ev["version"], ev["event_id"]))
    for agg_id, rows in agg_versions.items():
        last_v = 0
        for _, v, eid in rows:
            if v <= last_v:
                errors.append(_err(eid, "VERSION_ORDER",
                                   f"聚合 {agg_id} 的 version 必须随 occurred_at 单调递增"))
            last_v = v

    _check_exposure_lifecycle(ordered, errors)
    _check_observation_state_machine(ordered, errors)
    _check_critical_alerts(ordered, errors)
    _check_specimen_lineage(ordered, errors)
    _check_handoffs(ordered, errors)
    _check_followups(ordered, errors)
    _check_relations(ordered, errors)
    _check_referential_links(ordered, errors)
    return errors


# ---- 暴露事件：多院上报与合并 ---------------------------------------------

def _check_exposure_lifecycle(events: list[dict], errors: list[str]) -> None:
    reports: dict[str, dict] = {}  # exposure_id -> EXPOSURE_REPORTED
    for ev in events:
        if ev["event_type"] == "EXPOSURE_REPORTED":
            if ev["exposure_id"] in reports:
                errors.append(_err(ev["event_id"], "EXPOSURE_DUP_REPORT",
                                   "同一 exposure_id 出现两次 EXPOSURE_REPORTED；"
                                   "多院重复上报应先各自编号再 EXPOSURE_MERGED"))
            reports[ev["exposure_id"]] = ev
            if ev["aggregate_id"] != ev["exposure_id"]:
                errors.append(_err(ev["event_id"], "AGGREGATE_ID_MISMATCH",
                                   "EXPOSURE_REPORTED 的 aggregate_id 应等于 exposure_id"))

    merged_away_at: dict[str, datetime] = {}  # 被并入他号的 exposure_id -> 合并时间
    for ev in events:
        if ev["event_type"] != "EXPOSURE_MERGED":
            continue
        p = ev["payload"]
        surviving = p["surviving_exposure_id"]
        if surviving not in reports:
            errors.append(_err(ev["event_id"], "MERGE_SURVIVOR_MISSING",
                               f"留存事件 {surviving} 没有 EXPOSURE_REPORTED"))
        for mid in p["merged_exposure_ids"]:
            if mid not in reports:
                errors.append(_err(ev["event_id"], "MERGE_SOURCE_MISSING",
                                   f"被合并事件 {mid} 没有自己的 EXPOSURE_REPORTED（原始记录必须保留）"))
            if mid == surviving:
                errors.append(_err(ev["event_id"], "MERGE_SELF", "合并源不能等于留存事件"))
            merged_away_at[mid] = _parse_ts(p["merged_at"])
        if ev["exposure_id"] != surviving:
            errors.append(_err(ev["event_id"], "MERGE_EXPOSURE_ID",
                               "EXPOSURE_MERGED 自身必须挂在 surviving_exposure_id 下"))

    # 合并之后，不允许再以被注销编号发事件
    for ev in events:
        dead_at = merged_away_at.get(ev["exposure_id"])
        if dead_at and _parse_ts(ev["occurred_at"]) > dead_at:
            errors.append(_err(ev["event_id"], "MERGED_ID_REUSED",
                               f"exposure_id {ev['exposure_id']} 已合并注销，"
                               "合并后事件必须使用留存编号"))


# ---- 留观状态机：症状暂缓 ≠ 医学解除 --------------------------------------

# 观察中状态
_OBSERVING = {"REQUIRED", "UNDER_OBSERVATION", "EXTENDED", "RETURNED_TO_OBSERVATION"}
_TERMINAL = {"MEDICALLY_DISCHARGED", "LEFT_AGAINST_ADVICE"}

_OBS_EVENTS = {
    "OBSERVATION_REQUIRED", "OBSERVATION_EXTENDED",
    "OBSERVATION_DISCHARGED_MEDICALLY", "OBSERVATION_LEFT_AGAINST_ADVICE",
    "OBSERVATION_RETURNED",
}


def _check_observation_state_machine(events: list[dict], errors: list[str]) -> None:
    # patient_id -> 留观状态事件
    by_patient: dict[str, list[dict]] = {}
    for ev in events:
        if ev["event_type"] in _OBS_EVENTS:
            by_patient.setdefault(ev["payload"]["patient_id"], []).append(ev)

    # patient_id -> 全部携带 patient_id 的事件
    all_by_patient: dict[str, list[dict]] = {}
    # patient_id -> 最晚一条假愈期窗口截止时间（症状暂缓）
    paused_until: dict[str, datetime] = {}
    for ev in events:
        p = ev.get("payload") or {}
        pid = p.get("patient_id")
        if pid:
            all_by_patient.setdefault(pid, []).append(ev)
        if ev["event_type"] == "SYMPTOM_PAUSED":
            until_ts = _parse_ts(p["expected_resurgence_window"]["until"])
            if pid not in paused_until or until_ts > paused_until[pid]:
                paused_until[pid] = until_ts

    # 症状暂缓只能发生在留观进行中；暂缓不是离院或结案
    for pid, evs in all_by_patient.items():
        for ev in evs:
            if ev["event_type"] != "SYMPTOM_PAUSED":
                continue
            cur_at = _state_at(by_patient.get(pid, []), _parse_ts(ev["occurred_at"]))
            if cur_at not in _OBSERVING:
                errors.append(_err(ev["event_id"], "SYMPTOM_PAUSED_STATE",
                                   f"症状暂缓记录时患者不在观察中（{cur_at}）；"
                                   "暂缓不是离院或结案"))

    state: dict[str, str] = {}
    until: dict[str, datetime] = {}  # 当前留观要求截止时间

    for pid, obs_events in by_patient.items():
        obs_events.sort(key=lambda e: _parse_ts(e["occurred_at"]))
        for ev in obs_events:
            p = ev["payload"]
            ts = _parse_ts(ev["occurred_at"])
            cur = state.get(pid)

            if ev["event_type"] == "OBSERVATION_REQUIRED":
                if cur is not None:
                    errors.append(_err(ev["event_id"], "OBS_REOPENED",
                                       "留观已建立，不能重复 OBSERVATION_REQUIRED"))
                state[pid] = p["state"]  # REQUIRED / UNDER_OBSERVATION
                until[pid] = _parse_ts(p["required_until"])

            elif ev["event_type"] == "OBSERVATION_EXTENDED":
                if cur not in _OBSERVING:
                    errors.append(_err(ev["event_id"], "OBS_TRANSITION",
                                       f"延长留观前必须处于观察中（当前 {cur}），"
                                       "症状暂缓不产生留观状态"))
                if _parse_ts(p["extended_until"]) <= _parse_ts(p["previous_until"]):
                    errors.append(_err(ev["event_id"], "OBS_EXTEND_TIME",
                                       "extended_until 必须晚于 previous_until"))
                state[pid] = "EXTENDED"
                until[pid] = _parse_ts(p["extended_until"])

            elif ev["event_type"] == "OBSERVATION_DISCHARGED_MEDICALLY":
                if cur not in _OBSERVING:
                    errors.append(_err(ev["event_id"], "OBS_TRANSITION",
                                       f"医学解除留观前必须处于观察中（当前 {cur}）；"
                                       "自行离院者须先 OBSERVATION_RETURNED"))
                if ts < until.get(pid, ts):
                    errors.append(_err(ev["event_id"], "OBS_TIME_NOT_MET",
                                       "不得早于留观要求截止时间医学解除（自觉好转不能提前结案）"))
                if pid in paused_until and ts < paused_until[pid]:
                    errors.append(_err(ev["event_id"], "FALSE_RECOVERY_WINDOW_OPEN",
                                       "假愈期观察窗口未结束，不能医学解除留观"))
                state[pid] = "MEDICALLY_DISCHARGED"

            elif ev["event_type"] == "OBSERVATION_LEFT_AGAINST_ADVICE":
                if cur not in _OBSERVING:
                    errors.append(_err(ev["event_id"], "OBS_TRANSITION",
                                       f"自行离院记录只能发生在观察中（当前 {cur}）"))
                state[pid] = "LEFT_AGAINST_ADVICE"

            elif ev["event_type"] == "OBSERVATION_RETURNED":
                if cur != "LEFT_AGAINST_ADVICE":
                    errors.append(_err(ev["event_id"], "OBS_TRANSITION",
                                       "OBSERVATION_RETURNED 只能接在 LEFT_AGAINST_ADVICE 之后"))
                state[pid] = "RETURNED_TO_OBSERVATION"

    # 终局后不允许再写入急性期临床事件（化验面板与复诊除外：
    # 解除留观后的复查化验由 FOLLOWUP_* 串联，属于正常业务）
    for pid, evs in all_by_patient.items():
        terminal_at = _terminal_since(by_patient.get(pid, []))
        if not terminal_at:
            continue
        for ev in evs:
            if ev["event_type"] in ("FOLLOWUP_SCHEDULED", "FOLLOWUP_COMPLETED",
                                    "LAB_PANEL_RECORDED"):
                continue
            if ev["event_type"] in _OBS_EVENTS:
                continue
            if ev["event_type"] in ("SYMPTOM_RECORDED", "SYMPTOM_PAUSED",
                                    "TREATMENT_ORDERED", "CRITICAL_ALERT_RAISED") \
                    and _parse_ts(ev["occurred_at"]) > terminal_at[0] \
                    and terminal_at[1] == "MEDICALLY_DISCHARGED":
                errors.append(_err(ev["event_id"], "AFTER_DISCHARGE_WRITE",
                                   "医学解除留观后不得再写入该病程急性期事件；"
                                   "重新收治须另开病程"))


def _state_at(obs_events: list[dict], ts: datetime) -> str | None:
    """复现患者在 ts 时刻的留观状态。"""
    cur: str | None = None
    for ev in sorted(obs_events, key=lambda e: _parse_ts(e["occurred_at"])):
        if _parse_ts(ev["occurred_at"]) > ts:
            break
        cur = ev["payload"]["state"]
    return cur


def _terminal_since(obs_events: list[dict]) -> tuple[datetime, str] | None:
    for ev in sorted(obs_events, key=lambda e: _parse_ts(e["occurred_at"])):
        st = ev["payload"]["state"]
        if st in _TERMINAL:
            return _parse_ts(ev["occurred_at"]), st
    return None


# ---- 危急值：触发及时提醒，医生确认后方能收口 ------------------------------

def _check_critical_alerts(events: list[dict], errors: list[str]) -> None:
    panels: dict[str, dict] = {}       # aggregate_id -> panel event
    critical_panels: set[str] = set()
    for ev in events:
        if ev["event_type"] == "LAB_PANEL_RECORDED":
            panels[ev["aggregate_id"]] = ev
            if any(m.get("critical") for m in ev["payload"]["metrics"]):
                critical_panels.add(ev["aggregate_id"])

    alerts: dict[str, dict] = {}       # alert aggregate_id -> raised
    alerted_panels: set[str] = set()
    for ev in events:
        if ev["event_type"] == "CRITICAL_ALERT_RAISED":
            if not ev["payload"].get("auto_close_forbidden"):
                errors.append(_err(ev["event_id"], "ALERT_AUTOCLOSE",
                                   "危急值提醒必须声明 auto_close_forbidden=true"))
            alerts[ev["aggregate_id"]] = ev
            panel_id = ev["payload"].get("lab_panel_id")
            if panel_id:
                if panel_id not in panels:
                    errors.append(_err(ev["event_id"], "ALERT_PANEL_MISSING",
                                       f"提醒引用的化验面板 {panel_id} 不存在"))
                else:
                    alerted_panels.add(panel_id)
                    if _parse_ts(ev["occurred_at"]) < _parse_ts(panels[panel_id]["occurred_at"]):
                        errors.append(_err(ev["event_id"], "ALERT_ORDER",
                                           "危急值提醒不能早于对应化验时间"))
            if ev.get("causation_id") and ev["causation_id"] in (e["event_id"] for e in events):
                pass

    for panel_id in critical_panels - alerted_panels:
        errors.append(_err(panels[panel_id]["event_id"], "CRITICAL_NO_ALERT",
                           "出现 critical=true 的指标但没有 CRITICAL_ALERT_RAISED"))

    acks: dict[str, dict] = {}
    for ev in events:
        if ev["event_type"] == "CRITICAL_ALERT_ACKNOWLEDGED":
            acks[ev["payload"]["alert_id"]] = ev
            raised = alerts.get(ev["payload"]["alert_id"])
            if not raised:
                errors.append(_err(ev["event_id"], "ACK_ALERT_MISSING",
                                   "确认记录找不到对应的 CRITICAL_ALERT_RAISED"))
                continue
            if _parse_ts(ev["occurred_at"]) < _parse_ts(raised["occurred_at"]):
                errors.append(_err(ev["event_id"], "ACK_ORDER", "确认时间早于提醒时间"))

    for alert_id, raised in alerts.items():
        if alert_id not in acks:
            errors.append(_err(raised["event_id"], "ALERT_UNACKNOWLEDGED",
                               "危急值提醒必须由医生 CRITICAL_ALERT_ACKNOWLEDGED 并给出临床决策，"
                               "系统不得代为结案"))


# ---- 样本：采集 → 拆分 → 封签核对 → 转运 → 接收 → 结果谱系 -----------------

def _collect_specimens(events: list[dict]) -> set[str]:
    known: set[str] = set()
    for ev in events:
        t = ev["event_type"]
        p = ev.get("payload", {})
        if t == "SPECIMEN_COLLECTED":
            known.add(p["specimen_id"])
        elif t == "SPECIMEN_SPLIT_PREPARED":
            known.update(c["specimen_id"] for c in p["child_specimens"])
    return known


def _check_specimen_lineage(events: list[dict], errors: list[str]) -> None:
    seal_of: dict[str, str] = {}      # specimen_id -> 当前封签
    parent_of: dict[str, str] = {}    # specimen_id -> parent
    known: set[str] = set()
    pending_transfers: dict[str, dict] = {}  # 指纹 specimen_id -> transfer event

    for ev in sorted(events, key=lambda e: _parse_ts(e["occurred_at"])):
        t = ev["event_type"]
        p = ev.get("payload", {})

        if t == "SPECIMEN_COLLECTED":
            sid = p["specimen_id"]
            if sid in known:
                errors.append(_err(ev["event_id"], "SPECIMEN_DUP", f"样本 {sid} 重复采集登记"))
            known.add(sid)
            seal_of[sid] = p["seal"]["seal_id"]
            if p.get("parent_specimen_id"):
                parent_of[sid] = p["parent_specimen_id"]

        elif t == "SPECIMEN_SPLIT_PREPARED":
            parent = p["parent_specimen_id"]
            if parent not in known:
                errors.append(_err(ev["event_id"], "SPLIT_PARENT_MISSING",
                                   f"拆分母体 {parent} 未采集登记"))
            child_ids = [c["specimen_id"] for c in p["child_specimens"]]
            if len(set(child_ids)) != len(child_ids):
                errors.append(_err(ev["event_id"], "SPLIT_CHILD_DUP", "拆分子样本编号重复"))
            for child in p["child_specimens"]:
                cid = child["specimen_id"]
                if cid in known:
                    errors.append(_err(ev["event_id"], "SPECIMEN_DUP", f"子样本 {cid} 已存在"))
                known.add(cid)
                seal_of[cid] = child["seal"]["seal_id"]
                parent_of[cid] = parent

        elif t == "SPECIMEN_SEAL_VERIFIED":
            if p["specimen_id"] not in known:
                errors.append(_err(ev["event_id"], "SEAL_UNKNOWN_SPECIMEN",
                                   f"封签核对的样本 {p['specimen_id']} 不存在"))
            elif seal_of.get(p["specimen_id"]) != p["seal_id"]:
                errors.append(_err(ev["event_id"], "SEAL_MISMATCH",
                                   f"样本 {p['specimen_id']} 封签 {p['seal_id']} "
                                   f"与谱系登记 {seal_of.get(p['specimen_id'])} 不一致"))
            if not p["intact"] and "discrepancy" not in p:
                errors.append(_err(ev["event_id"], "SEAL_BROKEN_NO_DISCREPANCY",
                                   "封签不完整必须登记 discrepancy"))

        elif t == "SPECIMEN_TRANSFERRED":
            for sid in p["specimen_ids"]:
                if sid not in known:
                    errors.append(_err(ev["event_id"], "TRANSFER_UNKNOWN_SPECIMEN",
                                       f"转运样本 {sid} 不在谱系中"))
            expected_seals = {seal_of.get(sid) for sid in p["specimen_ids"]}
            if set(p["seal_ids"]) != expected_seals:
                errors.append(_err(ev["event_id"], "TRANSFER_SEAL_SET",
                                   f"转运封签集合 {sorted(p['seal_ids'])} 与样本当前封签 "
                                   f"{sorted(s for s in expected_seals if s)} 不一致"))
            for sid in p["specimen_ids"]:
                pending_transfers[sid] = ev

        elif t == "SPECIMEN_RECEIVED":
            received_ids = set(p["specimen_ids"])
            for check in p["seal_checks"]:
                sid = check["specimen_id"]
                if sid not in known:
                    errors.append(_err(ev["event_id"], "RECEIVE_UNKNOWN_SPECIMEN",
                                       f"接收样本 {sid} 不在谱系中"))
                elif seal_of.get(sid) != check["seal_id"]:
                    errors.append(_err(ev["event_id"], "RECEIVE_SEAL_MISMATCH",
                                       f"接收时样本 {sid} 封签与谱系不符"))
                transfer = pending_transfers.get(sid)
                if not transfer:
                    errors.append(_err(ev["event_id"], "RECEIVE_WITHOUT_TRANSFER",
                                       f"样本 {sid} 没有在途的 SPECIMEN_TRANSFERRED"))
                elif p["receiving_facility_id"] != transfer["payload"]["to_facility_id"]:
                    errors.append(_err(ev["event_id"], "RECEIVE_FACILITY",
                                       f"样本 {sid} 的接收方与转运目的地不一致"))
            for sid in received_ids:
                pending_transfers.pop(sid, None)

        elif t == "TOXICOLOGY_RESULTED":
            sid = p["specimen_id"]
            if sid not in known:
                errors.append(_err(ev["event_id"], "TOX_UNKNOWN_SPECIMEN",
                                   f"出结果的样本 {sid} 不在谱系中"))
            lineage = p.get("lineage")
            if lineage:
                root = lineage["root_specimen_id"]
                node = sid
                chain = {node}
                while node in parent_of:
                    node = parent_of[node]
                    chain.add(node)
                if root not in chain:
                    errors.append(_err(ev["event_id"], "TOX_LINEAGE_ROOT",
                                       f"结果谱系根 {root} 不是样本 {sid} 的祖先"))
                if lineage.get("parent_specimen_id") and lineage["parent_specimen_id"] != parent_of.get(sid):
                    errors.append(_err(ev["event_id"], "TOX_LINEAGE_PARENT",
                                       "lineage.parent_specimen_id 与拆检记录不符"))
                if seal_of.get(sid) != lineage["seal_id"]:
                    errors.append(_err(ev["event_id"], "TOX_LINEAGE_SEAL",
                                       f"结果封签 {lineage['seal_id']} 与样本当前封签不符"))

    for sid, transfer in pending_transfers.items():
        errors.append(_err(transfer["event_id"], "TRANSFER_OPEN",
                           f"样本 {sid} 已转出但流中无 SPECIMEN_RECEIVED，谱系未闭环"))


# ---- 转院：发起 → 接收方逐项确认资料、样本、待办 ---------------------------

def _check_handoffs(events: list[dict], errors: list[str]) -> None:
    known_refs = {e["event_id"] for e in events} | {e["aggregate_id"] for e in events}
    known_specimens = _collect_specimens(events)
    initiated: dict[str, dict] = {}
    for ev in events:
        if ev["event_type"] == "HANDOFF_INITIATED":
            initiated[ev["payload"]["handoff_id"]] = ev
            for item in ev["payload"]["package"]["records"]:
                if item["event_id"] not in known_refs:
                    errors.append(_err(ev["event_id"], "HANDOFF_RECORD_MISSING",
                                       f"交接资料 {item['kind']}/{item['event_id']} "
                                       "在事件流中不存在"))
            for item in ev["payload"]["package"]["specimens"]:
                if item["specimen_id"] not in known_specimens:
                    errors.append(_err(ev["event_id"], "HANDOFF_SPECIMEN_MISSING",
                                       f"交接样本 {item['specimen_id']} 不在谱系中"))

    for ev in events:
        if ev["event_type"] != "HANDOFF_ACKNOWLEDGED":
            continue
        p = ev["payload"]
        init = initiated.get(p["handoff_id"])
        if not init:
            errors.append(_err(ev["event_id"], "HANDOFF_INIT_MISSING",
                               f"确认找不到交接 {p['handoff_id']} 的发起记录"))
            continue
        ip = init["payload"]
        if p["acknowledged_by"]["receiving_facility_id"] != ip["to_facility_id"]:
            errors.append(_err(ev["event_id"], "HANDOFF_RECEIVER",
                               "确认方不是交接发起时指定的接收医院"))
        if _parse_ts(p["acknowledged_at"]) < _parse_ts(ip["initiated_at"]):
            errors.append(_err(ev["event_id"], "HANDOFF_ORDER", "确认时间早于交接发起时间"))

        conf = p["confirmations"]
        # 资料逐项确认
        rec_rows = {(r["kind"], r["event_id"]): r for r in conf["records_received"]}
        for item in ip["package"]["records"]:
            key = (item["kind"], item["event_id"])
            row = rec_rows.get(key)
            if row is None:
                errors.append(_err(ev["event_id"], "HANDOFF_RECORD_UNCONFIRMED",
                                   f"资料 {item['kind']}/{item['event_id']} 未被接收方逐项确认"))
            elif not row["present"] and not row.get("missing_reason"):
                errors.append(_err(ev["event_id"], "HANDOFF_RECORD_MISSING_REASON",
                                   f"资料 {item['event_id']} 标记缺失但没有 missing_reason"))

        # 样本逐项确认（含封签完整性）
        sp_rows = {(r["specimen_id"], r["seal_id"]): r for r in conf["specimens_accounted"]}
        for item in ip["package"]["specimens"]:
            key = (item["specimen_id"], item["seal_id"])
            row = sp_rows.get(key)
            if row is None:
                errors.append(_err(ev["event_id"], "HANDOFF_SPECIMEN_UNCONFIRMED",
                                   f"样本 {item['specimen_id']} 封签 {item['seal_id']} 未被逐项确认"))
            else:
                if not row["accounted"] and not row.get("note"):
                    errors.append(_err(ev["event_id"], "HANDOFF_SPECIMEN_NOTE",
                                       f"样本 {item['specimen_id']} 未点收且没有说明"))
                if row["accounted"] and not row["seal_intact"]:
                    errors.append(_err(ev["event_id"], "HANDOFF_SEAL_BROKEN",
                                       f"样本 {item['specimen_id']} 点收但封签不完整，"
                                       "须按封签异常处置不得直接接收"))

        # 待办逐项接受/拒绝
        act_rows = {r["action_code"]: r for r in conf["open_actions_accepted"]}
        for item in ip["package"]["open_actions"]:
            row = act_rows.get(item["action_code"])
            if row is None:
                errors.append(_err(ev["event_id"], "HANDOFF_ACTION_UNCONFIRMED",
                                   f"待办 {item['action_code']} 未被接收方逐项确认"))
            elif not row["accepted"] and not row.get("decline_reason"):
                errors.append(_err(ev["event_id"], "HANDOFF_ACTION_DECLINE",
                                   f"待办 {item['action_code']} 被拒绝但没有 decline_reason"))

    for hid, init in initiated.items():
        if not any(e["event_type"] == "HANDOFF_ACKNOWLEDGED"
                   and e["payload"]["handoff_id"] == hid for e in events):
            errors.append(_err(init["event_id"], "HANDOFF_UNACKNOWLEDGED",
                               f"交接 {hid} 已发起但接收方未明确确认资料、样本与待办"))


# ---- 复诊 -----------------------------------------------------------------

def _check_followups(events: list[dict], errors: list[str]) -> None:
    scheduled: dict[str, dict] = {}
    for ev in events:
        if ev["event_type"] == "FOLLOWUP_SCHEDULED":
            scheduled[ev["aggregate_id"]] = ev
    for ev in events:
        if ev["event_type"] == "FOLLOWUP_COMPLETED":
            fuid = ev["payload"]["follow_up_id"]
            sch = scheduled.get(fuid)
            if not sch:
                errors.append(_err(ev["event_id"], "FOLLOWUP_NOT_SCHEDULED",
                                   f"复诊 {fuid} 没有 FOLLOWUP_SCHEDULED"))
            elif _parse_ts(ev["payload"]["completed_at"]) < _parse_ts(sch["payload"]["scheduled_for"]):
                errors.append(_err(ev["event_id"], "FOLLOWUP_ORDER", "复诊完成早于预约时间"))
        if ev["event_type"] == "OBSERVATION_DISCHARGED_MEDICALLY":
            fuid = ev["payload"].get("follow_up_id")
            if fuid and fuid not in scheduled:
                errors.append(_err(ev["event_id"], "DISCHARGE_FOLLOWUP_MISSING",
                                   f"解除留观引用的复诊 {fuid} 未预约"))


# ---- 同桌关系：仅风险通知，不互开病历 --------------------------------------

def _check_relations(events: list[dict], errors: list[str]) -> None:
    patients: set[str] = set()
    for ev in events:
        if ev["event_type"] == "COURSE_OPENED":
            patients.add(ev["payload"]["patient"]["patient_id"])

    for ev in events:
        if ev["event_type"] != "PERSON_RELATION_LINKED":
            continue
        p = ev["payload"]
        if p["subject_patient_id"] == p["related_patient_id"]:
            errors.append(_err(ev["event_id"], "RELATION_SELF", "不能与本人建立同桌关系"))
        for pid in (p["subject_patient_id"], p["related_patient_id"]):
            if pid not in patients:
                errors.append(_err(ev["event_id"], "RELATION_PATIENT_MISSING",
                                   f"关系中的患者 {pid} 没有 COURSE_OPENED"))
        if p["consent_scope"] == "CO_DINER_ALERT_ONLY" and not p.get("alert_message_ref"):
            errors.append(_err(ev["event_id"], "RELATION_ALERT_REF",
                               "CO_DINER_ALERT_ONLY 关联必须指向风险通知模板，"
                               "不得借关联传递病历内容"))


# ---- 一般引用完整性 --------------------------------------------------------

def _check_referential_links(events: list[dict], errors: list[str]) -> None:
    known_specimens: set[str] = set()
    known_panels: set[str] = set()
    known_tox: set[str] = set()
    known_courses: set[str] = set()
    for ev in events:
        t = ev["event_type"]
        p = ev.get("payload", {})
        if t == "SPECIMEN_COLLECTED":
            known_specimens.add(p["specimen_id"])
        elif t == "SPECIMEN_SPLIT_PREPARED":
            known_specimens.update(c["specimen_id"] for c in p["child_specimens"])
        elif t == "LAB_PANEL_RECORDED":
            known_panels.add(ev["aggregate_id"])
        elif t == "TOXICOLOGY_RESULTED":
            known_tox.add(ev["aggregate_id"])
        elif t == "COURSE_OPENED":
            known_courses.add(ev["aggregate_id"])

    for ev in events:
        t = ev["event_type"]
        p = ev.get("payload", {})
        if t == "LAB_PANEL_RECORDED" and p.get("specimen_id") \
                and p["specimen_id"] not in known_specimens:
            errors.append(_err(ev["event_id"], "LAB_SPECIMEN_MISSING",
                               f"化验面板引用的样本 {p['specimen_id']} 不存在"))
        if t == "TREATMENT_ORDERED":
            if p.get("based_on_lab_panel_id") and p["based_on_lab_panel_id"] not in known_panels:
                errors.append(_err(ev["event_id"], "TREATMENT_REF",
                                   "治疗依据的 lab_panel 不存在"))
            if p.get("based_on_tox_result_id") and p["based_on_tox_result_id"] not in known_tox:
                errors.append(_err(ev["event_id"], "TREATMENT_REF",
                                   "治疗依据的 tox_result 不存在"))
