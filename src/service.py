from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .audit import utc_now
from .domain import (ConflictError, NotFoundError, ValidationError, ensure_role,
                     normalize_severity, optional_text, require_number,
                     require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CORRECTIVE_RECORD_KINDS, CREATE_ROLES, ENTITY,
                    LINK_ROLES, RECORD_ROLES, RECURRENCE_WINDOW_DAYS, STATES,
                    TITLE, VIEW_ROLES, completion_blockers, escalation_required,
                    investigation_blockers, link_status, priority_score,
                    response_deadline_hours, role_for_transition,
                    validate_transition)


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
        workstation = optional_text(payload.get("workstation"), "workstation", 100)
        injury_cause = optional_text(payload.get("injury_cause"), "injury_cause", 200)
        recurrence = None
        if workstation and injury_cause:
            recurrence = self._evaluate_recurrence(workstation, injury_cause)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor,
                                           workstation=workstation,
                                           injury_cause=injury_cause,
                                           recurrence=recurrence)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "workstation": workstation, "injury_cause": injury_cause,
            "recurrence_count": item["recurrence_count"],
            "priority": priority_score(severity, quantity, threshold,
                                       recurrence_count=item["recurrence_count"]),
        })
        return self.enrich(item)

    def update_link(self, item_id: int, payload: Dict[str, Any], actor: str,
                    role: str) -> Dict[str, Any]:
        ensure_role(role, LINK_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] != STATES[0]:
            raise ConflictError("仅报告阶段可补齐关联信息")
        expected = payload.get("expected_version")
        if not isinstance(expected, int) or expected < 1:
            raise ValueError("expected_version必须是正整数")
        workstation = item["workstation"]
        injury_cause = item["injury_cause"]
        provided = False
        if "workstation" in payload:
            workstation = optional_text(payload.get("workstation"), "workstation", 100)
            provided = True
        if "injury_cause" in payload:
            injury_cause = optional_text(payload.get("injury_cause"), "injury_cause", 200)
            provided = True
        if not provided:
            raise ValidationError("至少提供workstation或injury_cause之一")
        if workstation and injury_cause:
            recurrence = self._evaluate_recurrence(workstation, injury_cause)
        else:
            recurrence = {
                "count": item["recurrence_count"], "of": item["recurrence_of"],
                "links": json.loads(item["recurrence_links"] or "[]"),
                "summary": item["last_corrective_summary"],
                "evaluated_at": item["recurrence_evaluated_at"],
            }
        updated = self.repository.update_link(item_id, workstation, injury_cause,
                                              recurrence, expected, actor)
        self.repository.append_audit("link", ENTITY, item_id, actor, {
            "workstation": workstation, "injury_cause": injury_cause,
            "recurrence_count": recurrence["count"],
            "recurrence_of": recurrence["of"],
        })
        return self.enrich(updated)

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
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = investigation_blockers(item, target) + completion_blockers(
            target, self.repository.open_record_count(item_id))
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"],
                item.get("recurrence_count", 0)),
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

    def _evaluate_recurrence(self, workstation: str, injury_cause: str) -> Dict[str, Any]:
        window_start = (datetime.now(timezone.utc)
                        - timedelta(days=RECURRENCE_WINDOW_DAYS)).replace(microsecond=0).isoformat()
        matches = self.repository.find_recurrences(workstation, injury_cause, window_start)
        summary = None
        if matches:
            details = [r["detail"] for r in self.repository.list_records(matches[0]["id"])
                       if r["kind"] in CORRECTIVE_RECORD_KINDS]
            if details:
                summary = "；".join(details)
        return {
            "count": len(matches),
            "of": matches[0]["id"] if matches else None,
            "links": [m["id"] for m in matches],
            "summary": summary,
            "evaluated_at": utc_now(),
        }

    def _recurrence_view(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        evaluated_at = item.get("recurrence_evaluated_at")
        if not evaluated_at:
            return None
        count = int(item.get("recurrence_count") or 0)
        links = json.loads(item.get("recurrence_links") or "[]")
        linked_items = []
        for linked_id in links:
            try:
                source = self.repository.get_item(linked_id)
            except NotFoundError:
                continue
            linked_items.append({
                "id": source["id"], "title": source["title"],
                "closed_at": source.get("closed_at") or source.get("updated_at"),
            })
        if count:
            basis = (f"近{RECURRENCE_WINDOW_DAYS}天同工位({item['workstation']})"
                     f"同伤害原因({item['injury_cause']})已结案事故{count}起")
            if linked_items:
                basis += f"，最近为#{linked_items[0]['id']}（{linked_items[0]['closed_at']}结案）"
        else:
            basis = f"近{RECURRENCE_WINDOW_DAYS}天同工位同伤害原因无已结案事故"
        return {
            "count": count,
            "window_days": RECURRENCE_WINDOW_DAYS,
            "basis": basis,
            "last_corrective_summary": item.get("last_corrective_summary"),
            "linked_items": linked_items,
            "evaluated_at": evaluated_at,
        }

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        recurrence_count = int(item.get("recurrence_count") or 0)
        result["priority"] = priority_score(
            item["severity"], item["quantity"], item["threshold"],
            recurrence_count=recurrence_count)
        result["deadline_hours"] = response_deadline_hours(
            item["severity"], item["quantity"], item["threshold"])
        result["escalation_required"] = escalation_required(
            item["severity"], item["quantity"], item["threshold"], recurrence_count)
        result["link_status"] = link_status(item)
        result["recurrence"] = self._recurrence_view(item)
        return result
