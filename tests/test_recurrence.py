import tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES, priority_score


def iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


class RecurrenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)

    def tearDown(self):
        self.repo.close(); self.tmp.cleanup()

    def _create(self, title, workstation="WS-07", injury_cause="机械伤害", **extra):
        payload = {"title": title, "description": "recurrence scenario",
                   "severity": "moderate", "quantity": 1, "threshold": 10,
                   "workstation": workstation, "injury_cause": injury_cause}
        payload.update(extra)
        return self.service.create_item(payload, "creator", "reporter")

    def _close(self, item, corrective=None):
        if corrective:
            self.service.add_record(item["id"], {"kind": "corrective_action",
                                                 "detail": corrective, "status": "closed"},
                                    "recorder", "investigator")
        current = item
        for target in STATES[1:]:
            current = self.service.transition(current["id"], target, current["version"],
                                              "reviewer", TRANSITION_ROLES[target][0])
        return current

    def test_pending_until_linked_then_recurrence_detected(self):
        first = self._create("first accident")
        self.assertEqual(first["link_status"], "linked")
        self.assertEqual(first["recurrence"]["count"], 0)
        first = self._close(first, "加装防护罩并复训")

        second = self._create("second accident", injury_cause=None)
        self.assertEqual(second["link_status"], "pending")
        self.assertIsNone(second["recurrence"])
        with self.assertRaises(ConflictError):
            self.service.transition(second["id"], STATES[1], second["version"],
                                    "reviewer", TRANSITION_ROLES[STATES[1]][0])

        linked = self.service.update_link(second["id"], {
            "injury_cause": "机械伤害", "expected_version": second["version"]},
            "creator", "reporter")
        self.assertEqual(linked["link_status"], "linked")
        recurrence = linked["recurrence"]
        self.assertEqual(recurrence["count"], 1)
        self.assertEqual(recurrence["linked_items"][0]["id"], first["id"])
        self.assertIn("防护罩", recurrence["last_corrective_summary"])
        self.assertIn("WS-07", recurrence["basis"])
        self.assertIn("机械伤害", recurrence["basis"])
        self.assertGreater(linked["priority"],
                           priority_score("moderate", 1, 10, recurrence_count=0))
        self.assertTrue(linked["escalation_required"])

        current = self.service.transition(linked["id"], STATES[1], linked["version"],
                                          "reviewer", TRANSITION_ROLES[STATES[1]][0])
        self.assertEqual(current["status"], STATES[1])

        original = self.service.get_item(first["id"], "viewer")
        self.assertEqual(original["status"], "closed")
        self.assertEqual(original["recurrence"]["count"], 0)
        self.assertEqual(len(self.service.list_records(first["id"], "viewer")), 1)

    def test_window_excludes_accidents_older_than_180_days(self):
        old = self._close(self._create("old accident"))
        recent = self._close(self._create("recent accident"), "更换连锁开关")
        with self.repo.conn:
            self.repo.conn.execute("UPDATE items SET closed_at=? WHERE id=?",
                                   (iso_days_ago(200), old["id"]))

        fresh = self._create("fresh accident")
        recurrence = fresh["recurrence"]
        self.assertEqual(recurrence["count"], 1)
        self.assertEqual([entry["id"] for entry in recurrence["linked_items"]],
                         [recent["id"]])
        self.assertIn("连锁开关", recurrence["last_corrective_summary"])

    def test_recurrence_count_accumulates_and_list_shows_basis(self):
        self._close(self._create("accident one"), "措施一")
        self._close(self._create("accident two"), "措施二")
        third = self._create("accident three")
        self.assertEqual(third["recurrence"]["count"], 2)
        self.assertEqual(len(third["recurrence"]["linked_items"]), 2)
        self.assertIn("措施二", third["recurrence"]["last_corrective_summary"])

        listed = {item["id"]: item for item in self.service.list_items("viewer")}
        self.assertEqual(listed[third["id"]]["recurrence"]["count"], 2)
        self.assertIn("已结案事故2起", listed[third["id"]]["recurrence"]["basis"])
        self.assertEqual(listed[third["id"]]["link_status"], "linked")

    def test_update_link_guards(self):
        item = self._create("guards", workstation=None, injury_cause=None)
        with self.assertRaises(PermissionDenied):
            self.service.update_link(item["id"], {"workstation": "WS-07",
                                                  "expected_version": item["version"]},
                                     "intruder", "viewer")
        with self.assertRaises(ValidationError):
            self.service.update_link(item["id"], {"expected_version": item["version"]},
                                     "creator", "reporter")
        with self.assertRaises(ConflictError):
            self.service.update_link(item["id"], {"workstation": "WS-07",
                                                  "expected_version": 99},
                                     "creator", "reporter")
        partial = self.service.update_link(item["id"], {
            "workstation": "WS-07", "expected_version": item["version"]},
            "creator", "reporter")
        self.assertEqual(partial["link_status"], "pending")
        self.assertIsNone(partial["recurrence"])
        with self.assertRaises(ConflictError):
            self.service.transition(partial["id"], STATES[1], partial["version"],
                                    "reviewer", TRANSITION_ROLES[STATES[1]][0])
        done = self.service.update_link(partial["id"], {
            "injury_cause": "机械伤害", "expected_version": partial["version"]},
            "creator", "investigator")
        self.assertEqual(done["link_status"], "linked")
        investigating = self.service.transition(done["id"], STATES[1], done["version"],
                                                "reviewer", TRANSITION_ROLES[STATES[1]][0])
        with self.assertRaises(ConflictError):
            self.service.update_link(done["id"], {"workstation": "WS-09",
                                                  "expected_version": investigating["version"]},
                                     "creator", "reporter")


if __name__ == "__main__":
    unittest.main()
