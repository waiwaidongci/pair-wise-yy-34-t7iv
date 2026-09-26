from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_severity,
                     require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, LINK_ROLES,
                    PENDING_LINK_STATE, RECORD_ROLES, RECURRENCE_WINDOW_DAYS,
                    TITLE, VIEW_ROLES, completion_blockers,
                    escalation_required, initial_status, priority_score,
                    raised_severity, response_deadline_hours,
                    role_for_transition, validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        workstation = payload.get("workstation")
        if workstation is not None:
            workstation = require_text(workstation, "workstation", 100)
        injury_cause = payload.get("injury_cause")
        if injury_cause is not None:
            injury_cause = require_text(injury_cause, "injury_cause", 200)
        status = initial_status(workstation, injury_cause)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, workstation,
                                           injury_cause, status, actor)
        enriched = self.enrich(item)
        recurrence = enriched["recurrence"]
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "workstation": workstation, "injury_cause": injury_cause,
            "status": status, "risk_level": enriched["risk_level"],
            "recurrence_count": recurrence["count"],
            "linked_ids": [m["id"] for m in recurrence["linked_items"]],
            "priority": enriched["priority"],
        })
        return enriched

    def link_item(self, item_id: int, payload: Dict[str, Any], actor: str,
                  role: str) -> Dict[str, Any]:
        ensure_role(role, LINK_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] != PENDING_LINK_STATE:
            raise ConflictError("仅待关联事故可补齐工位编号和伤害原因")
        workstation = payload.get("workstation")
        if workstation is not None:
            workstation = require_text(workstation, "workstation", 100)
        else:
            workstation = item.get("workstation")
        injury_cause = payload.get("injury_cause")
        if injury_cause is not None:
            injury_cause = require_text(injury_cause, "injury_cause", 200)
        else:
            injury_cause = item.get("injury_cause")
        if (workstation == item.get("workstation")
                and injury_cause == item.get("injury_cause")):
            raise ConflictError("未提供需要补齐的工位编号或伤害原因")
        status = initial_status(workstation, injury_cause)
        updated = self.repository.update_link(item_id, workstation, injury_cause,
                                              status, actor)
        enriched = self.enrich(updated)
        recurrence = enriched["recurrence"]
        self.repository.append_audit("link", ENTITY, item_id, actor, {
            "workstation": workstation, "injury_cause": injury_cause,
            "status": status, "risk_level": enriched["risk_level"],
            "recurrence_count": recurrence["count"],
            "linked_ids": [m["id"] for m in recurrence["linked_items"]],
        })
        return enriched

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        if not item.get("workstation") or not item.get("injury_cause"):
            if item["status"] == PENDING_LINK_STATE:
                raise ConflictError("工位编号和伤害原因补齐后才能解除待关联")
            if target == "investigating":
                raise ConflictError("工位编号和伤害原因补齐后才能进入调查")
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        })
        return self.enrich(updated)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        return [self.enrich(item) for item in self.repository.list_items(status)]

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        recurrence = self._recurrence_info(item)
        result["recurrence"] = recurrence
        risk_level = recurrence["risk_level"]
        result["risk_level"] = risk_level
        result["priority"] = priority_score(
            risk_level, item["quantity"], item["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            risk_level, item["quantity"], item["threshold"])
        result["escalation_required"] = escalation_required(
            risk_level, item["quantity"], item["threshold"])
        return result

    def _recurrence_info(self, item: Dict[str, Any]) -> Dict[str, Any]:
        workstation = (item.get("workstation") or "").strip()
        injury_cause = (item.get("injury_cause") or "").strip()
        severity = item["severity"]
        empty = {"count": 0, "basis": None, "linked_items": [],
                 "last_item_id": None, "last_item_title": None,
                 "last_corrective_summary": None, "last_corrective_actions": [],
                 "risk_level": severity}
        if not workstation or not injury_cause:
            return dict(empty, basis="工位编号或伤害原因未填，留待关联")
        since = (datetime.now(timezone.utc)
                 - timedelta(days=RECURRENCE_WINDOW_DAYS)).replace(microsecond=0).isoformat()
        matches = self.repository.find_closed_matches(
            workstation, injury_cause, item["id"], since)
        count = len(matches)
        risk_level = raised_severity(severity, count)
        if not matches:
            return dict(empty, risk_level=risk_level,
                        basis=f"近{RECURRENCE_WINDOW_DAYS}天内无同工位同原因已结案事故")
        last = matches[0]
        records = self.repository.list_records(last["id"])
        corrective = [r for r in records if r["kind"] == "corrective_action"] or records
        actions = [r["detail"] for r in corrective]
        linked = [{"id": m["id"], "title": m["title"], "severity": m["severity"],
                   "closed_at": m["updated_at"]} for m in matches]
        return {
            "count": count,
            "basis": (f"近{RECURRENCE_WINDOW_DAYS}天内同工位({workstation})同原因"
                      f"({injury_cause})已结案事故{count}起，"
                      f"最近为#{last['id']}《{last['title']}》"),
            "linked_items": linked,
            "last_item_id": last["id"],
            "last_item_title": last["title"],
            "last_corrective_summary": "；".join(actions)[:500],
            "last_corrective_actions": actions,
            "risk_level": risk_level,
        }
