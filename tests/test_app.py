"""
Automated tests for the Waste Collection Request System.
Run from the project folder:   python -m unittest discover -s tests -v

Uses Flask's test client and a temporary SQLite file, so the real database is never touched.
Note: responsive layout (mobile) cannot be verified by these tests; it needs manual checking.
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta

# Keep the module-level `app` (created on import) away from the real instance database.
_BOOT_DIR = tempfile.mkdtemp()
os.environ["WASTE_DB_PATH"] = os.path.join(_BOOT_DIR, "boot.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.app = app_module.create_app(
            {"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "unit-test-key"}
        )
        self.client = self.app.test_client()
        app_module._failed_logins.clear()
        with self.app.app_context():
            db = app_module.get_db()
            db.execute(
                "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("CorrectHorse1")),
            )
            db.commit()

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def token(self, client=None):
        client = client or self.client
        client.get("/request")  # creates the session + CSRF token
        with client.session_transaction() as sess:
            return sess["_csrf_token"]

    def today(self):
        with self.app.app_context():
            return app_module.today_local()

    def submit(self, **overrides):
        data = {
            "full_name": "Test Resident",
            "area": "Hostel Area",
            "collection_date": (self.today() + timedelta(days=3)).isoformat(),
            "waste_type": "Plastic Waste",
        }
        data.update(overrides)
        data["csrf_token"] = self.token()
        return self.client.post("/request", data=data)

    def login(self, password="CorrectHorse1", username="admin"):
        return self.client.post(
            "/admin/login",
            data={"username": username, "password": password, "csrf_token": self.token()},
        )

    def rows(self, sql="SELECT * FROM collection_requests ORDER BY id"):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def seed(self, name, area, offset, waste="General Waste", status="Pending", ref=None):
        ref = ref or f"WCR-T-{name[:3].upper()}{offset}"
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO collection_requests (reference_number, full_name, area, collection_date,"
            " waste_type, status) VALUES (?,?,?,?,?,?)",
            (ref, name, area, (self.today() + timedelta(days=offset)).isoformat(), waste, status),
        )
        con.commit()
        con.close()


class PublicTests(BaseCase):
    def test_T01_homepage_loads(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Request Collection", r.data)
        self.assertIn(b"Household Waste", r.data)

    def test_T02_request_form_loads(self):
        r = self.client.get("/request")
        self.assertEqual(r.status_code, 200)
        for field in (b'name="full_name"', b'name="area"', b'name="collection_date"', b'name="waste_type"'):
            self.assertIn(field, r.data)

    def test_T03_valid_submission_saves_record(self):
        r = self.submit()
        self.assertEqual(r.status_code, 302)
        saved = self.rows()
        self.assertEqual(len(saved), 1)
        row = saved[0]
        self.assertEqual(row["full_name"], "Test Resident")
        self.assertEqual(row["area"], "Hostel Area")
        self.assertEqual(row["waste_type"], "Plastic Waste")
        self.assertEqual(row["status"], "Pending")
        self.assertRegex(row["reference_number"], r"^WCR-\d{8}-[A-Z2-9]{5}$")
        page = self.client.get(r.headers["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn(row["reference_number"].encode(), page.data)

    def test_T04_missing_fields_rejected(self):
        r = self.submit(full_name="", area="", collection_date="", waste_type="")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"Please enter your full name", r.data)
        self.assertIn(b"Please enter your area", r.data)
        self.assertIn(b"Please choose a preferred collection date", r.data)
        self.assertIn(b"Please select a waste type", r.data)
        self.assertEqual(len(self.rows()), 0)

    def test_T05_past_date_rejected(self):
        r = self.submit(collection_date=(self.today() - timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"cannot be in the past", r.data)
        self.assertEqual(len(self.rows()), 0)

    def test_T06_today_is_accepted(self):
        r = self.submit(collection_date=self.today().isoformat())
        self.assertEqual(r.status_code, 302)

    def test_T07_invalid_date_rejected(self):
        for bad in ("2026-13-45", "not-a-date", "31/12/2026"):
            r = self.submit(collection_date=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(b"Enter a valid date", r.data)
        self.assertEqual(len(self.rows()), 0)

    def test_T08_invalid_waste_type_rejected(self):
        r = self.submit(waste_type="Nuclear Waste")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.rows()), 0)

    def test_T09_invalid_name_rejected(self):
        r = self.submit(full_name="<script>alert(1)</script>")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.rows()), 0)

    def test_T10_all_waste_types_accepted(self):
        for waste in app_module.WASTE_TYPES:
            self.assertEqual(self.submit(waste_type=waste).status_code, 302, waste)
        self.assertEqual(len(self.rows()), 6)

    def test_T11_references_are_unique(self):
        for _ in range(15):
            self.submit()
        refs = [r["reference_number"] for r in self.rows()]
        self.assertEqual(len(refs), len(set(refs)))

    def test_T12_csrf_missing_token_blocked(self):
        r = self.client.post("/request", data={"full_name": "X Y", "area": "Hostel Area",
                                               "collection_date": self.today().isoformat(),
                                               "waste_type": "Other"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.rows()), 0)

    def test_T13_sql_injection_stored_as_text(self):
        self.submit(area="Hostel'; DROP TABLE collection_requests;--")
        self.assertEqual(len(self.rows()), 0)  # rejected by area validation, table still exists
        self.submit(area="Block A (East) & Annex")
        self.assertEqual(len(self.rows()), 1)

    def test_T14_area_case_normalised(self):
        self.submit(area="Hostel Area")
        self.submit(area="hostel   area")
        areas = {r["area"] for r in self.rows()}
        self.assertEqual(areas, {"Hostel Area"})

    def test_T15_unknown_success_reference_404(self):
        self.assertEqual(self.client.get("/request/success/WCR-NOPE").status_code, 404)

    def test_T16_unknown_page_404(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)


class AdminTests(BaseCase):
    def test_T20_unauthorised_access_redirects(self):
        for path in ("/admin", "/admin/routes", "/admin/requests/1"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/admin/login", r.headers["Location"])

    def test_T21_unauthorised_post_blocked(self):
        self.seed("Ada", "Hostel Area", 2)
        tok = self.token()
        r = self.client.post("/admin/requests/1/status", data={"status": "Collected", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.rows()[0]["status"], "Pending")
        r = self.client.post("/admin/requests/1/delete", data={"csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.rows()), 1)

    def test_T22_login_success_and_dashboard(self):
        r = self.login()
        self.assertEqual(r.status_code, 302)
        d = self.client.get("/admin")
        self.assertEqual(d.status_code, 200)
        self.assertIn(b"Administrator dashboard", d.data)

    def test_T23_wrong_password(self):
        r = self.login(password="wrong")
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Incorrect username or password", r.data)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_T24_unknown_user(self):
        r = self.login(username="ghost")
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Incorrect username or password", r.data)

    def test_T25_empty_credentials(self):
        r = self.login(password="", username="")
        self.assertEqual(r.status_code, 400)

    def test_T26_password_is_hashed(self):
        h = self.rows("SELECT password_hash FROM admins")[0]["password_hash"]
        self.assertNotIn("CorrectHorse1", h)
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_T27_login_throttle(self):
        for _ in range(app_module.LOGIN_MAX_FAILURES):
            self.login(password="bad")
        r = self.login(password="CorrectHorse1")  # correct password but locked out
        self.assertEqual(r.status_code, 429)

    def test_T28_logout_ends_session(self):
        self.login()
        tok = self.token()
        r = self.client.post("/admin/logout", data={"csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_T29_logout_requires_post(self):
        self.login()
        self.assertEqual(self.client.get("/admin/logout").status_code, 405)

    def test_T30_open_redirect_blocked(self):
        r = self.client.post(
            "/admin/login?next=https://evil.example/",
            data={"username": "admin", "password": "CorrectHorse1", "csrf_token": self.token()},
        )
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example", r.headers["Location"])

    def test_T31_stats_and_listing(self):
        self.seed("Ada", "Hostel Area", 1, status="Pending")
        self.seed("Bola", "Hostel Area", 2, status="Scheduled")
        self.seed("Chi", "Market Road", 3, status="Collected")
        self.seed("Dan", "Market Road", 4, status="Pending")
        self.login()
        html = self.client.get("/admin").get_data(as_text=True)
        for name in ("Ada", "Bola", "Chi", "Dan"):
            self.assertIn(name, html)
        nums = re.findall(r'class="stat-value">(\d+)<', html)
        self.assertEqual(nums, ["4", "2", "1", "1"])  # total, pending, scheduled, collected

    def test_T32_status_update_persists(self):
        self.seed("Ada", "Hostel Area", 1)
        self.login()
        tok = self.token()
        r = self.client.post("/admin/requests/1/status", data={"status": "Scheduled", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.rows()[0]["status"], "Scheduled")
        self.client.post("/admin/requests/1/status", data={"status": "Collected", "csrf_token": tok})
        self.assertEqual(self.rows()[0]["status"], "Collected")

    def test_T33_invalid_status_rejected(self):
        self.seed("Ada", "Hostel Area", 1)
        self.login()
        self.client.post("/admin/requests/1/status",
                         data={"status": "Exploded", "csrf_token": self.token()})
        self.assertEqual(self.rows()[0]["status"], "Pending")

    def test_T34_status_on_missing_request_404(self):
        self.login()
        r = self.client.post("/admin/requests/999/status",
                             data={"status": "Collected", "csrf_token": self.token()})
        self.assertEqual(r.status_code, 404)

    def test_T35_request_detail_view(self):
        self.seed("Ada", "Hostel Area", 1, ref="WCR-T-DETAIL")
        self.login()
        r = self.client.get("/admin/requests/1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"WCR-T-DETAIL", r.data)
        self.assertEqual(self.client.get("/admin/requests/999").status_code, 404)

    def test_T36_delete_request(self):
        self.seed("Ada", "Hostel Area", 1)
        self.seed("Bola", "Hostel Area", 2)
        self.login()
        r = self.client.post("/admin/requests/1/delete", data={"csrf_token": self.token()})
        self.assertEqual(r.status_code, 302)
        remaining = [x["full_name"] for x in self.rows()]
        self.assertEqual(remaining, ["Bola"])

    def test_T37_search(self):
        self.seed("Ada Obi", "Hostel Area", 1, ref="WCR-T-AAA")
        self.seed("Bola Ade", "Market Road", 2, ref="WCR-T-BBB")
        self.login()
        html = self.client.get("/admin?q=Bola").get_data(as_text=True)
        self.assertIn("Bola Ade", html)
        self.assertNotIn("Ada Obi", html)
        html = self.client.get("/admin?q=WCR-T-AAA").get_data(as_text=True)
        self.assertIn("Ada Obi", html)
        self.assertNotIn("Bola Ade", html)
        # LIKE wildcards are escaped, so "%" matches nothing rather than everything
        html = self.client.get("/admin?q=%25").get_data(as_text=True)
        self.assertNotIn("Ada Obi", html)

    def test_T38_filter_area_status_date(self):
        self.seed("Ada", "Hostel Area", 1, status="Pending")
        self.seed("Bola", "Market Road", 5, status="Scheduled")
        self.seed("Chi", "Hostel Area", 9, status="Collected")
        self.login()
        html = self.client.get("/admin?area=hostel+area").get_data(as_text=True)
        self.assertIn("Ada", html); self.assertIn("Chi", html); self.assertNotIn("Bola", html)
        html = self.client.get("/admin?status=Scheduled").get_data(as_text=True)
        self.assertIn("Bola", html); self.assertNotIn("Ada", html)
        lo = (self.today() + timedelta(days=4)).isoformat()
        hi = (self.today() + timedelta(days=6)).isoformat()
        html = self.client.get(f"/admin?date_from={lo}&date_to={hi}").get_data(as_text=True)
        self.assertIn("Bola", html); self.assertNotIn("Ada", html); self.assertNotIn("Chi", html)
        html = self.client.get("/admin?area=Hostel+Area&status=Collected").get_data(as_text=True)
        self.assertIn("Chi", html); self.assertNotIn("Ada", html)

    def test_T39_invalid_filter_values_do_not_crash(self):
        self.login()
        for qs in ("status=bogus", "date_from=zzz", "page=abc", "page=-5", "sort=DROP", "page=999"):
            self.assertEqual(self.client.get("/admin?" + qs).status_code, 200, qs)

    def test_T40_sorting_and_pagination(self):
        for i in range(20):
            self.seed(f"Person{i:02d}", "Hostel Area", i + 1, ref=f"WCR-T-P{i:02d}")
        self.login()
        p1 = self.client.get("/admin?sort=date_asc").get_data(as_text=True)
        p2 = self.client.get("/admin?sort=date_asc&page=2").get_data(as_text=True)
        self.assertIn("Person00", p1); self.assertNotIn("Person19", p1)
        self.assertIn("Person19", p2); self.assertNotIn("Person00", p2)
        self.assertIn("Page 2 of 2", p2)

    def test_T41_area_route_view_groups(self):
        self.seed("Ada", "Hostel Area", 1)
        self.seed("Bola", "Market Road", 2)
        self.seed("Chi", "Hostel Area", 3)
        self.seed("Dan", "Hostel Area", 4, status="Collected")
        self.login()
        html = self.client.get("/admin/routes").get_data(as_text=True)  # default = open requests
        self.assertEqual(html.count('class="table-card area-group'), 2)
        self.assertNotIn("Dan", html)
        # Only look inside the group cards (the filter dropdown also lists area names).
        groups = html[html.index('area-group'):]
        hostel = groups.index("Hostel Area")
        market = groups.index("Market Road")
        self.assertLess(hostel, market)
        # Ada and Chi sit together inside the Hostel Area group, before the Market Road group
        self.assertTrue(hostel < groups.index("Ada") < groups.index("Chi") < market < groups.index("Bola"))
        html = self.client.get("/admin/routes?status=").get_data(as_text=True)
        self.assertIn("Dan", html)
        html = self.client.get("/admin/routes?area=Market+Road").get_data(as_text=True)
        self.assertIn("Bola", html); self.assertNotIn("Ada", html)

    def test_T42_admin_pages_not_cached(self):
        self.login()
        self.assertEqual(self.client.get("/admin").headers.get("Cache-Control"), "no-store")

    def test_T43_xss_is_escaped(self):
        self.seed("<b>Bold</b>", "Hostel Area", 1)
        self.login()
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertNotIn("<b>Bold</b>", html)
        self.assertIn("&lt;b&gt;Bold&lt;/b&gt;", html)


class PersistenceTests(BaseCase):
    def test_T50_data_persists_across_app_instances(self):
        self.submit()
        second = app_module.create_app(
            {"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "unit-test-key"}
        )
        with second.app_context():
            count = app_module.get_db().execute("SELECT COUNT(*) FROM collection_requests").fetchone()[0]
        self.assertEqual(count, 1)

    def test_T51_init_db_is_idempotent(self):
        self.submit()
        with self.app.app_context():
            app_module.init_db()
            app_module.init_db()
        self.assertEqual(len(self.rows()), 1)

    def test_T52_schema_constraints(self):
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO collection_requests (reference_number, full_name, area, collection_date, waste_type)"
                    " VALUES ('R1','A','B','2030-01-01','Other')")
        with self.assertRaises(sqlite3.IntegrityError):  # duplicate reference
            con.execute("INSERT INTO collection_requests (reference_number, full_name, area, collection_date, waste_type)"
                        " VALUES ('R1','A','B','2030-01-01','Other')")
        with self.assertRaises(sqlite3.IntegrityError):  # bad status
            con.execute("INSERT INTO collection_requests (reference_number, full_name, area, collection_date, waste_type, status)"
                        " VALUES ('R2','A','B','2030-01-01','Other','Lost')")
        with self.assertRaises(sqlite3.IntegrityError):  # bad waste type
            con.execute("INSERT INTO collection_requests (reference_number, full_name, area, collection_date, waste_type)"
                        " VALUES ('R3','A','B','2030-01-01','Toxic')")
        con.close()

    def test_T53_indexes_exist(self):
        names = {r["name"] for r in self.rows("SELECT name FROM sqlite_master WHERE type='index'")}
        for expected in ("idx_requests_status", "idx_requests_area", "idx_requests_date"):
            self.assertIn(expected, names)

    def test_T54_updated_at_changes_on_status_update(self):
        self.seed("Ada", "Hostel Area", 1)
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE collection_requests SET updated_at='2000-01-01 00:00:00'")
        con.commit(); con.close()
        self.login()
        self.client.post("/admin/requests/1/status", data={"status": "Scheduled", "csrf_token": self.token()})
        self.assertNotEqual(self.rows()[0]["updated_at"], "2000-01-01 00:00:00")


class CliTests(BaseCase):
    def test_T60_create_admin_and_login(self):
        runner = self.app.test_cli_runner()
        res = runner.invoke(args=["create-admin", "--username", "chief", "--password", "LongPass99"])
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(self.login(username="chief", password="LongPass99").status_code, 302)

    def test_T61_create_admin_rejects_short_password_and_duplicates(self):
        runner = self.app.test_cli_runner()
        res = runner.invoke(args=["create-admin", "--username", "bob", "--password", "short"])
        self.assertNotEqual(res.exit_code, 0)
        res = runner.invoke(args=["create-admin", "--username", "admin", "--password", "LongPass99"])
        self.assertNotEqual(res.exit_code, 0)
        self.assertIn("already exists", res.output)

    def test_T62_seed_and_clear_demo(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=["seed-demo"]).exit_code, 0)
        n = len(self.rows())
        self.assertEqual(n, 24)
        runner.invoke(args=["seed-demo"])  # second run must not duplicate
        self.assertEqual(len(self.rows()), 24)
        self.assertTrue(all(r["is_demo"] == 1 for r in self.rows()))
        runner.invoke(args=["clear-demo"])
        self.assertEqual(len(self.rows()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
