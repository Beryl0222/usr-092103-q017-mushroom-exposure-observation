# 毒蘑菇暴露留观领域契约

区域中毒救治中心「野生蘑菇中毒暴露留观」后端的领域词汇、事件契约与中文联调样例。
一桌共同进食者可能分散在不同医院、症状暂缓后自行离院、样本与检验结果不随转诊流动，
本契约用统一的 **exposure_id（暴露事件编号）** 把进食事件、人员关系、症状时间线、
样本保管、毒物鉴定、肝肾指标、治疗、留观责任、转院交接与复诊串成一条可追溯的事件流。

## 资料结构

- `contracts/domain.schema.json`：事件公共信封、25 种事件类型与各自 payload 结构（JSON Schema 2020-12）。
- `src/validator.py`：
  - `validate_event(record)` 单事件结构校验（内置精简 JSON Schema 校验器，无第三方依赖）；
  - `validate_stream(events)` 事件流跨事件不变量校验（状态机、危急值、样本谱系、交接确认、合并留痕等）。
- `data/sample.json`：单条最小事件样例。
- `data/scenario.json`：一次家庭聚餐跨 3 家医疗机构、3 名患者的完整联调事件流（61 个事件，由脚本生成）。
- `scripts/build_scenario.py`：生成 `scenario.json`（修改场景后重新运行）。
- `tests/`：契约正例 + 每条核心不变量的单点变异反例。

## 事件目录（按聚合分组）

| 聚合 | 事件 | 含义 |
| --- | --- | --- |
| exposure_event | `EXPOSURE_REPORTED` / `EXPOSURE_MERGED` | 进食事件上报；多院重复上报合并 |
| patient_course | `COURSE_OPENED` | 患者建档（只含最小必要身份信息） |
| patient_course | `PERSON_RELATION_LINKED` | 同桌/家属关系，授权范围分级 |
| patient_course | `SYMPTOM_RECORDED` / `SYMPTOM_PAUSED` | 症状记录；**症状暂缓（假愈期）** |
| lab_panel | `LAB_PANEL_RECORDED` | 肝肾/凝血等指标，可回溯样本 |
| critical_alert | `CRITICAL_ALERT_RAISED` / `CRITICAL_ALERT_ACKNOWLEDGED` | 危急值提醒与医生确认 |
| patient_course | `TREATMENT_ORDERED` | 治疗措施，可回溯化验/毒理依据 |
| patient_course | `OBSERVATION_REQUIRED` / `OBSERVATION_EXTENDED` | 建立/延长留观 |
| patient_course | `OBSERVATION_DISCHARGED_MEDICALLY` | **医学解除留观**（医生签署） |
| patient_course | `OBSERVATION_LEFT_AGAINST_ADVICE` / `OBSERVATION_RETURNED` | **自行离院（责任不闭环）** / 返院恢复留观 |
| specimen_chain | `SPECIMEN_COLLECTED` / `SPECIMEN_SPLIT_PREPARED` / `SPECIMEN_SEAL_VERIFIED` / `SPECIMEN_TRANSFERRED` / `SPECIMEN_RECEIVED` | 采样、拆分、封签核对、转运、签收 |
| tox_result | `TOXICOLOGY_RESULTED` | 毒理/形态学鉴定结果（带样本谱系） |
| care_handoff | `HANDOFF_INITIATED` / `HANDOFF_ACKNOWLEDGED` | 转院发起资料/样本/待办包；接收方逐项确认 |
| follow_up | `FOLLOWUP_SCHEDULED` / `FOLLOWUP_COMPLETED` | 复诊预约与结局 |

## 核心领域规则（事件流不变量）

1. **症状暂缓 ≠ 医学解除留观。**
   `SYMPTOM_PAUSED` 只登记假愈期反复窗口（`expected_resurgence_window`），不改变留观状态；
   自觉好转不能提前结案——早于 `required_until` 或仍在假愈期窗口内的
   `OBSERVATION_DISCHARGED_MEDICALLY` 一律拒绝（`OBS_TIME_NOT_MET` / `FALSE_RECOVERY_WINDOW_OPEN`）。
2. **自行离院是独立状态，不是结案。**
   `LEFT_AGAINST_ADVICE` 必须携带已履行的告知与仍开放的留观/复诊责任、联系计划；
   只有 `OBSERVATION_RETURNED` 回到观察中后，才可能进入医学解除。
3. **危急值触发提醒，处置由医生确认。**
   `critical=true` 的指标必须有 `CRITICAL_ALERT_RAISED`（`auto_close_forbidden=true`），
   且必须由医生 `CRITICAL_ALERT_ACKNOWLEDGED` 给出临床决策；系统不得代为解除
   （`CRITICAL_NO_ALERT` / `ALERT_UNACKNOWLEDGED` / `ALERT_AUTOCLOSE`）。
4. **样本拆分送检保持封签与结果谱系。**
   子样本必须来自已登记母体，转运封签集合须与谱系一致，接收须逐一核对封签，
   转出无签收不闭环；毒理结果的 `lineage` 根样本必须是受检样本的祖先
   （`SEAL_MISMATCH` / `TRANSFER_OPEN` / `TOX_LINEAGE_ROOT` 等）。
5. **多院重复上报合并为同一事件、保留各自记录。**
   重复上报先独立编号（可标 `duplicate_of_exposure_id`），`EXPOSURE_MERGED` 正式合并；
   被合并编号的原始 `EXPOSURE_REPORTED` 不得删除，合并后不得再用注销编号发事件
   （`MERGE_SOURCE_MISSING` / `MERGED_ID_REUSED`）。
6. **转院必须由接收方逐项确认。**
   交接包列出资料、样本（含封签状态）与未完成待办；`HANDOFF_ACKNOWLEDGED`
   必须对每一项给出在/缺、点收与封签是否完好、待办接受/拒绝原因；
   封签破损不得点收（`HANDOFF_*_UNCONFIRMED` / `HANDOFF_SEAL_BROKEN`）。
7. **同桌关系仅用于本次风险通知。**
   `consent_scope=CO_DINER_ALERT_ONLY` 的关联只能指向通知模板，不授权互看病历
   （`RELATION_ALERT_REF`）。疾控所需的聚集规模与来源信息走 `source_trace`，与临床视图分离。
8. **指标变化可回溯全链。**
   化验面板必须引用 `specimen_id`；治疗可挂 `based_on_lab_panel_id` /
   `based_on_tox_result_id`；未完成留观责任通过交接包 `open_actions` 传递。

## 本地检查

```bash
python3 -m unittest discover -s tests
# 重新生成联调场景：
python3 scripts/build_scenario.py
```
