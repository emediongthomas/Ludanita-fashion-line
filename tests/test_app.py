import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import Order, User, create_app, db


class TrackerAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username="staff")
        user.set_password("test-password-123")
        db.session.add(user)
        admin = User(username="admin", role="admin")
        admin.set_password("admin-password-123")
        db.session.add(admin)
        db.session.commit()
        self.client = self.app.test_client()
        self.admin_client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.temp_dir.cleanup()

    def sign_in(self):
        return self.client.post("/login", data={
            "username": "staff",
            "password": "test-password-123",
        }, follow_redirects=True)

    def sign_in_admin(self):
        return self.admin_client.post("/login", data={
            "username": "admin",
            "password": "admin-password-123",
        }, follow_redirects=True)

    def reset_app_context(self):
        self.context.pop()
        self.context = self.app.app_context()
        self.context.push()

    def test_staff_account_management_requires_admin(self):
        self.sign_in()
        self.assertEqual(self.client.get("/staff").status_code, 403)
        self.assertEqual(
            self.client.post("/staff/new", data={}).status_code,
            403,
        )

        self.reset_app_context()
        self.sign_in_admin()
        response = self.admin_client.post("/staff/new", data={
            "username": "new-staff",
            "password": "new-staff-password",
            "password_confirmation": "new-staff-password",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Staff login created", response.data)
        staff_user = db.session.execute(
            db.select(User).where(User.username == "new-staff")
        ).scalar_one()
        self.assertEqual(staff_user.role, "staff")
        self.assertTrue(staff_user.check_password("new-staff-password"))

        response = self.admin_client.post(
            f"/staff/{staff_user.id}/edit",
            data={
                "username": "renamed-staff",
                "password": "updated-staff-password",
                "password_confirmation": "updated-staff-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(staff_user.username, "renamed-staff")
        self.assertTrue(staff_user.check_password("updated-staff-password"))

        response = self.admin_client.post(
            f"/staff/{staff_user.id}/toggle-active",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(staff_user.is_active)

        self.reset_app_context()
        inactive_client = self.app.test_client()
        response = inactive_client.post("/login", data={
            "username": "renamed-staff",
            "password": "updated-staff-password",
        }, follow_redirects=True)
        self.assertIn(b"Username or password is incorrect.", response.data)

        self.reset_app_context()
        staff_user = db.session.execute(
            db.select(User).where(User.username == "renamed-staff")
        ).scalar_one()
        response = self.admin_client.post(
            f"/staff/{staff_user.id}/toggle-active",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(staff_user.is_active)

    def test_admin_cannot_edit_or_deactivate_another_admin(self):
        self.sign_in_admin()
        admin = db.session.execute(
            db.select(User).where(User.username == "admin")
        ).scalar_one()
        response = self.admin_client.post(
            f"/staff/{admin.id}/edit",
            data={"username": "renamed-admin"},
        )
        self.assertEqual(response.status_code, 403)
        response = self.admin_client.post(
            f"/staff/{admin.id}/toggle-active",
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(admin.is_active)

    def test_init_db_migrates_existing_user_table(self):
        database_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "CREATE TABLE user ("
                "id INTEGER PRIMARY KEY, "
                "username VARCHAR(80) NOT NULL UNIQUE, "
                "password_hash VARCHAR(255) NOT NULL)"
            )
            connection.execute(
                "INSERT INTO user (username, password_hash) VALUES (?, ?)",
                ("legacy-staff", "existing-hash"),
            )
        connection.close()

        migration_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        result = migration_app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

        with migration_app.app_context():
            user = db.session.execute(
                db.select(User).where(User.username == "legacy-staff")
            ).scalar_one()
            self.assertEqual(user.role, "staff")
            self.assertTrue(user.is_active)
            db.session.remove()
            db.engine.dispose()

    def test_staff_login_and_order_payment_workflow(self):
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BUSINESS OVERVIEW", response.data)

        response = self.client.post("/orders/new", data={
            "customer_name": "Test Customer",
            "phone": "",
            "order_date": "2026-11-02",
            "item": "Test dress",
            "size": "M",
            "color": "Blue",
            "quantity": "2",
            "unit_price": "25.00",
            "expected_delivery": "",
            "notes": "",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        order = db.session.execute(db.select(Order)).scalar_one()
        self.assertEqual(order.order_id, "ORD-00001")
        self.assertEqual(str(order.order_total), "50.00")

        response = self.client.post("/payments/new", data={
            "order_id": order.order_id,
            "payment_date": "2026-11-02",
            "amount": "20.00",
            "method": "Cash",
            "reference": "receipt-1",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PAY-00001", response.data)
        self.assertIn(b"20.00", response.data)

        response = self.client.post(
            f"/orders/{order.order_id}/status",
            data={"status": "In Production"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(order.status, "In Production")

    def test_payment_cannot_exceed_balance(self):
        self.sign_in()
        response = self.client.post("/orders/new", data={
            "customer_name": "Test Customer",
            "order_date": "2026-11-02",
            "item": "Test dress",
            "quantity": "1",
            "unit_price": "10.00",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        order = db.session.execute(db.select(Order)).scalar_one()
        response = self.client.post("/payments/new", data={
            "order_id": order.order_id,
            "payment_date": "2026-11-02",
            "amount": "10.01",
            "method": "Cash",
        }, follow_redirects=True)
        self.assertIn(b"cannot exceed", response.data)
        self.assertEqual(order.payments, [])

    def test_csv_import_is_repeatable_and_keeps_existing_ids(self):
        orders_file = Path(self.temp_dir.name) / "orders.csv"
        payments_file = Path(self.temp_dir.name) / "payments.csv"
        with orders_file.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=[
                "order_id", "customer_name", "phone", "order_date", "item",
                "size", "color", "quantity", "unit_price", "order_total",
                "expected_delivery", "status", "notes",
            ])
            writer.writeheader()
            writer.writerow({
                "order_id": "ORD-00041", "customer_name": "Imported Customer",
                "phone": "", "order_date": date(2026, 11, 2).isoformat(),
                "item": "Imported item", "size": "", "color": "", "quantity": "1",
                "unit_price": "12.00", "order_total": "12.00",
                "expected_delivery": "", "status": "Pending", "notes": "",
            })
        with payments_file.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=[
                "payment_id", "order_id", "payment_date", "amount", "method",
                "reference",
            ])
            writer.writeheader()

        runner = self.app.test_cli_runner()
        args = [
            "import-csv", "--orders", str(orders_file),
            "--payments", str(payments_file),
        ]
        first = runner.invoke(args=args)
        second = runner.invoke(args=args)
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("Imported 0 orders and 0 payments", second.output)
        self.assertEqual(db.session.query(Order).count(), 1)


if __name__ == "__main__":
    unittest.main()
