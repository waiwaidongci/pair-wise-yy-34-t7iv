import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES

class RecurrenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Repository(str(Path(self.tmp.name)/"test.db")); self.service=Service(self.repo)
    def tearDown(self): self.repo.close(); self.tmp.cleanup()
    def _close_item(self, workstation, cause, ref, title, severity="moderate"):
        item=self.service.create_item({"title":title,"description":"accident","severity":severity,"quantity":1,"threshold":10,"external_ref":ref,"workstation":workstation,"injury_cause":cause},"creator","reporter")
        self.assertEqual(item["status"],"reported")
        self.service.add_record(item["id"],{"kind":"corrective_action","detail":"加装防护罩并复训","status":"closed","external_ref":ref+"-CA"},"recorder","investigator")
        current=item
        for target in STATES[STATES.index(item["status"])+1:]:
            current=self.service.transition(current["id"],target,current["version"],"reviewer",TRANSITION_ROLES[target][0])
        self.assertEqual(current["status"],"closed")
        return current
    def test_recurrence_chain_raises_risk_and_links(self):
        first=self._close_item("W-07","机械夹伤","RC-1","第一次夹伤")
        second=self.service.create_item({"title":"再次夹伤","description":"accident","severity":"minor","quantity":1,"threshold":10,"external_ref":"RC-2","workstation":"W-07","injury_cause":"机械夹伤"},"creator","reporter")
        rec=second["recurrence"]
        self.assertEqual(rec["count"],1)
        self.assertEqual(rec["linked_items"][0]["id"],first["id"])
        self.assertEqual(rec["last_item_id"],first["id"])
        self.assertIn("防护罩",rec["last_corrective_summary"])
        self.assertIn("W-07",rec["basis"])
        self.assertEqual(second["severity"],"minor")
        self.assertEqual(second["risk_level"],"moderate")
        detail=self.service.get_item(second["id"],"viewer")
        self.assertEqual(detail["recurrence"]["count"],1)
        listed=[i for i in self.service.list_items("viewer") if i["id"]==second["id"]][0]
        self.assertEqual(listed["recurrence"]["last_item_id"],first["id"])
        origin=self.service.get_item(first["id"],"viewer")
        self.assertEqual(origin["status"],"closed")
        self.assertEqual(origin["severity"],"moderate")
        self.assertEqual(len(self.service.list_records(first["id"],"viewer")),1)
        third=self.service.create_item({"title":"第三次夹伤","description":"accident","severity":"serious","quantity":1,"threshold":10,"external_ref":"RC-3","workstation":"W-07","injury_cause":"机械夹伤"},"creator","reporter")
        self.assertEqual(third["recurrence"]["count"],1)
        self.assertEqual(third["risk_level"],"fatal")
    def test_pending_link_until_filled(self):
        item=self.service.create_item({"title":"缺信息事故","description":"accident","severity":"minor","quantity":0,"threshold":1,"external_ref":"PL-1"},"creator","reporter")
        self.assertEqual(item["status"],"pending_link")
        self.assertEqual(item["recurrence"]["count"],0)
        with self.assertRaises(ConflictError): self.service.transition(item["id"],"investigating",item["version"],"op","investigator")
        with self.assertRaises(ConflictError): self.service.transition(item["id"],"reported",item["version"],"op","reporter")
        partial=self.service.link_item(item["id"],{"workstation":"W-11"},"op","reporter")
        self.assertEqual(partial["status"],"pending_link")
        self.assertEqual(partial["workstation"],"W-11")
        full=self.service.link_item(item["id"],{"injury_cause":"割伤"},"op","reporter")
        self.assertEqual(full["status"],"reported")
        with self.assertRaises(ConflictError): self.service.link_item(item["id"],{"workstation":"W-12"},"op","reporter")
        nxt=self.service.transition(full["id"],"investigating",full["version"],"op","investigator")
        self.assertEqual(nxt["status"],"investigating")
    def test_window_and_mismatch_not_counted(self):
        old=self._close_item("W-09","烫伤","RC-9","旧烫伤")
        with self.repo._lock, self.repo.conn:
            self.repo.conn.execute("UPDATE items SET updated_at=? WHERE id=?",("2020-01-01T00:00:00+00:00",old["id"]))
        stale=self.service.create_item({"title":"新烫伤","description":"accident","severity":"minor","quantity":1,"threshold":10,"external_ref":"RC-10","workstation":"W-09","injury_cause":"烫伤"},"creator","reporter")
        self.assertEqual(stale["recurrence"]["count"],0)
        self.assertEqual(stale["risk_level"],"minor")
        self._close_item("W-10","烫伤","RC-11","他工位烫伤")
        other_ws=self.service.create_item({"title":"本工位烫伤","description":"accident","severity":"minor","quantity":1,"threshold":10,"external_ref":"RC-12","workstation":"W-09","injury_cause":"烫伤"},"creator","reporter")
        self.assertEqual(other_ws["recurrence"]["count"],0)
        other_cause=self.service.create_item({"title":"本工位夹伤","description":"accident","severity":"minor","quantity":1,"threshold":10,"external_ref":"RC-13","workstation":"W-09","injury_cause":"机械夹伤"},"creator","reporter")
        self.assertEqual(other_cause["recurrence"]["count"],0)
if __name__=="__main__": unittest.main()
