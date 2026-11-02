"""Shared web app for Ludanita Fashion Line orders and payments."""

import csv
import os
from functools import wraps
from datetime import date
from decimal import Decimal, InvalidOperation
from getpass import getpass
from pathlib import Path
from uuid import uuid4

import click
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, inspect, text
from werkzeug.security import check_password_hash, generate_password_hash


DATA_DIR = Path(__file__).resolve().parent
ORDER_FIELDS = [
    "order_id", "customer_name", "phone", "order_date", "item", "size",
    "color", "quantity", "unit_price", "order_total", "expected_delivery",
    "status", "notes",
]
PAYMENT_FIELDS = [
    "payment_id", "order_id", "payment_date", "amount", "method", "reference",
]
ORDER_STATUSES = ("Pending", "In Production", "Ready", "Delivered")
PAYMENT_METHODS = ("Cash", "Bank Transfer", "Card", "Mobile Money", "Other")
CENT = Decimal("0.01")

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")
    active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def is_active(self):
        return self.active

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


def ensure_user_schema():
    inspector = inspect(db.engine)
    if not inspector.has_table(User.__tablename__):
        return

    columns = {column["name"] for column in inspector.get_columns(User.__tablename__)}
    table_name = db.engine.dialect.identifier_preparer.quote(User.__tablename__)
    with db.engine.begin() as connection:
        if "role" not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} "
                "ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'staff'"
            ))
        if "active" not in columns:
            connection.execute(text(
                f"ALTER TABLE {table_name} "
                "ADD COLUMN active BOOLEAN NOT NULL DEFAULT TRUE"
            ))


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(32), unique=True, nullable=True)
    customer_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(60), nullable=False, default="")
    order_date = db.Column(db.Date, nullable=False)
    item = db.Column(db.String(160), nullable=False)
    size = db.Column(db.String(60), nullable=False, default="")
    color = db.Column(db.String(80), nullable=False, default="")
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    order_total = db.Column(db.Numeric(12, 2), nullable=False)
    expected_delivery = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="Pending")
    notes = db.Column(db.Text, nullable=False, default="")
    payments = db.relationship("Payment", back_populates="order")


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.String(32), unique=True, nullable=True)
    order_id = db.Column(
        db.String(32), db.ForeignKey("order.order_id"), nullable=False, index=True
    )
    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(40), nullable=False)
    reference = db.Column(db.String(160), nullable=False, default="")
    order = db.relationship("Order", back_populates="payments")


def create_app(test_config=None):
    application = Flask(__name__)
    database_path = (DATA_DIR / "ludanita.sqlite3").as_posix()
    database_url = os.environ.get(
        "DATABASE_URL", f"sqlite:///{database_path}"
    )
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    application.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-key-change-me"),
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("APP_ENV") == "production",
    )
    if test_config:
        application.config.update(test_config)
    if (
        os.environ.get("APP_ENV") == "production"
        and application.config["SECRET_KEY"] == "local-development-key-change-me"
    ):
        raise RuntimeError("Set a unique SECRET_KEY before running in production.")

    db.init_app(application)
    login_manager.init_app(application)
    login_manager.login_view = "login"
    login_manager.login_message = "Please sign in to continue."
    csrf.init_app(application)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @application.template_filter("money")
    def format_money(value):
        return f"{Decimal(value):,.2f}"

    @application.context_processor
    def inject_template_values():
        return {
            "order_statuses": ORDER_STATUSES,
            "payment_methods": PAYMENT_METHODS,
        }

    @application.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @application.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = db.session.execute(
                db.select(User).where(User.username == username)
            ).scalar_one_or_none()
            if user and user.is_active and user.check_password(password):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Username or password is incorrect.", "error")
        return render_template("login.html")

    @application.post("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @application.get("/staff")
    @admin_required
    def staff_users():
        users = db.session.execute(
            db.select(User).order_by(User.username)
        ).scalars().all()
        return render_template("staff.html", users=users)

    @application.route("/staff/new", methods=["GET", "POST"])
    @admin_required
    def new_staff_user():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")

            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "error")
            elif db.session.execute(
                db.select(User).where(User.username == username)
            ).scalar_one_or_none():
                flash("That username already exists.", "error")
            elif len(password) < 12:
                flash("Use a password with at least 12 characters.", "error")
            elif password != confirmation:
                flash("Passwords did not match.", "error")
            else:
                user = User(username=username, role="staff")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(f"Staff login created for {username}.", "success")
                return redirect(url_for("staff_users"))

        return render_template("staff_form.html", staff_user=None)

    @application.route("/staff/<int:user_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_staff_user(user_id):
        user = db.get_or_404(User, user_id)
        if user.is_admin:
            abort(403)

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            duplicate = db.session.execute(
                db.select(User).where(
                    User.username == username,
                    User.id != user.id,
                )
            ).scalar_one_or_none() if username else None

            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "error")
            elif duplicate:
                flash("That username already exists.", "error")
            elif bool(password) != bool(confirmation):
                flash("Enter and confirm the new password, or leave both blank.", "error")
            elif password and len(password) < 12:
                flash("Use a password with at least 12 characters.", "error")
            elif password and password != confirmation:
                flash("Passwords did not match.", "error")
            else:
                user.username = username
                if password:
                    user.set_password(password)
                db.session.commit()
                flash(f"Staff login updated for {username}.", "success")
                return redirect(url_for("staff_users"))

        return render_template("staff_form.html", staff_user=user)

    @application.post("/staff/<int:user_id>/toggle-active")
    @admin_required
    def toggle_staff_user(user_id):
        user = db.get_or_404(User, user_id)
        if user.is_admin:
            abort(403)
        user.active = not user.active
        db.session.commit()
        state = "activated" if user.active else "deactivated"
        flash(f"Staff login {user.username} {state}.", "success")
        return redirect(url_for("staff_users"))

    @application.get("/dashboard")
    @login_required
    def dashboard():
        orders = db.session.execute(
            db.select(Order).order_by(Order.id.desc())
        ).scalars().all()
        totals = payment_totals(orders)
        order_value = sum((order.order_total for order in orders), Decimal("0.00"))
        amount_collected = db.session.execute(
            db.select(func.coalesce(func.sum(Payment.amount), Decimal("0.00")))
        ).scalar_one()
        return render_template(
            "dashboard.html",
            orders=orders[:8],
            totals=totals,
            order_count=len(orders),
            order_value=order_value,
            amount_collected=amount_collected,
            outstanding=sum((due for _, due in totals.values()), Decimal("0.00")),
            status_counts={
                status: sum(order.status == status for order in orders)
                for status in ORDER_STATUSES
            },
            auto_refresh=True,
        )

    @application.get("/orders")
    @login_required
    def orders():
        statement = db.select(Order).order_by(Order.id.desc())
        query = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        if query:
            statement = statement.where(
                db.or_(
                    Order.customer_name.ilike(f"%{query}%"),
                    Order.order_id.ilike(f"%{query}%"),
                    Order.item.ilike(f"%{query}%"),
                )
            )
        if status in ORDER_STATUSES:
            statement = statement.where(Order.status == status)
        order_list = db.session.execute(statement).scalars().all()
        return render_template(
            "orders.html",
            orders=order_list,
            totals=payment_totals(order_list),
            selected_status=status,
            search_query=query,
            auto_refresh=True,
        )

    @application.route("/orders/new", methods=["GET", "POST"])
    @login_required
    def new_order():
        if request.method == "POST":
            try:
                quantity = int(request.form.get("quantity", ""))
                unit_price = parse_amount(request.form.get("unit_price", ""))
                order_date = parse_date(request.form.get("order_date", ""))
                expected_text = request.form.get("expected_delivery", "").strip()
                expected_delivery = parse_date(expected_text) if expected_text else None
                if quantity <= 0:
                    raise ValueError("Quantity must be a whole number above zero.")
                customer_name = request.form.get("customer_name", "").strip()
                item = request.form.get("item", "").strip()
                if not customer_name or not item:
                    raise ValueError("Customer name and item are required.")
                order = Order(
                    customer_name=customer_name,
                    phone=request.form.get("phone", "").strip(),
                    order_date=order_date,
                    item=item,
                    size=request.form.get("size", "").strip(),
                    color=request.form.get("color", "").strip(),
                    quantity=quantity,
                    unit_price=unit_price,
                    order_total=(unit_price * quantity).quantize(CENT),
                    expected_delivery=expected_delivery,
                    notes=request.form.get("notes", "").strip(),
                )
                db.session.add(order)
                db.session.flush()
                order.order_id = generated_record_id(Order, "order_id", "ORD", order.id)
                db.session.commit()
                flash(f"Order {order.order_id} was saved.", "success")
                return redirect(url_for("orders"))
            except (ValueError, InvalidOperation) as error:
                db.session.rollback()
                flash(str(error), "error")
        return render_template("order_form.html", today=date.today().isoformat())

    @application.post("/orders/<string:order_id>/status")
    @login_required
    def update_status(order_id):
        order = get_order_or_404(order_id)
        status = request.form.get("status", "")
        if status not in ORDER_STATUSES:
            abort(400, description="Choose a valid order status.")
        order.status = status
        db.session.commit()
        flash(f"{order.order_id} updated to {status}.", "success")
        return redirect(url_for("orders"))

    @application.get("/payments")
    @login_required
    def payments():
        payment_list = db.session.execute(
            db.select(Payment).order_by(Payment.id.desc())
        ).scalars().all()
        return render_template(
            "payments.html", payments=payment_list, auto_refresh=True
        )

    @application.route("/payments/new", methods=["GET", "POST"])
    @login_required
    def new_payment():
        orders_list = db.session.execute(
            db.select(Order).order_by(Order.id.desc())
        ).scalars().all()
        totals = payment_totals(orders_list)
        if request.method == "POST":
            order_id = request.form.get("order_id", "")
            order = db.session.execute(
                db.select(Order)
                .where(Order.order_id == order_id)
                .with_for_update()
            ).scalar_one_or_none()
            if order is None:
                abort(404)
            try:
                amount = parse_amount(request.form.get("amount", ""))
                paid = db.session.execute(
                    db.select(func.coalesce(func.sum(Payment.amount), 0))
                    .where(Payment.order_id == order.order_id)
                ).scalar_one()
                balance = order.order_total - Decimal(paid)
                if amount <= 0:
                    raise ValueError("Payment must be greater than zero.")
                if amount > balance:
                    raise ValueError(
                        f"Payment cannot exceed the outstanding balance of {balance:,.2f}."
                    )
                method = request.form.get("method", "")
                if method not in PAYMENT_METHODS:
                    raise ValueError("Choose a valid payment method.")
                payment = Payment(
                    order_id=order.order_id,
                    payment_date=parse_date(request.form.get("payment_date", "")),
                    amount=amount,
                    method=method,
                    reference=request.form.get("reference", "").strip(),
                )
                db.session.add(payment)
                db.session.flush()
                payment.payment_id = generated_record_id(
                    Payment, "payment_id", "PAY", payment.id
                )
                db.session.commit()
                flash(f"Payment {payment.payment_id} was saved.", "success")
                return redirect(url_for("payments"))
            except (ValueError, InvalidOperation) as error:
                db.session.rollback()
                flash(str(error), "error")
        available_orders = [
            order for order in orders_list if totals[order.order_id][1] > 0
        ]
        return render_template(
            "payment_form.html",
            orders=available_orders,
            balances=totals,
            today=date.today().isoformat(),
        )

    @application.cli.command("init-db")
    def init_db_command():
        """Create the database tables."""
        with application.app_context():
            db.create_all()
            ensure_user_schema()
        click.echo("Database tables are ready.")

    @application.cli.command("create-user")
    @click.argument("username")
    @click.option(
        "--admin",
        "is_admin",
        is_flag=True,
        help="Create an administrator instead of a staff account.",
    )
    def create_user_command(username, is_admin):
        """Create an individual staff login."""
        username = username.strip()
        if not username:
            raise click.ClickException("Username cannot be empty.")
        with application.app_context():
            db.create_all()
            ensure_user_schema()
            if db.session.execute(
                db.select(User).where(User.username == username)
            ).scalar_one_or_none():
                raise click.ClickException("That username already exists.")
            password = getpass("Password (at least 12 characters): ")
            confirmation = getpass("Confirm password: ")
            if len(password) < 12:
                raise click.ClickException("Use a password with at least 12 characters.")
            if password != confirmation:
                raise click.ClickException("Passwords did not match.")
            user = User(username=username, role="admin" if is_admin else "staff")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        account_type = "Administrator" if is_admin else "Staff"
        click.echo(f"{account_type} login created for {username}.")

    @application.cli.command("import-csv")
    @click.option("--orders", "orders_path", type=click.Path(path_type=Path),
                  default=DATA_DIR / "ludanita_orders.csv", show_default=True)
    @click.option("--payments", "payments_path", type=click.Path(path_type=Path),
                  default=DATA_DIR / "ludanita_payments.csv", show_default=True)
    def import_csv_command(orders_path, payments_path):
        """Import existing tracker CSV files without replacing database records."""
        with application.app_context():
            order_rows = read_csv(orders_path, ORDER_FIELDS)
            payment_rows = read_csv(payments_path, PAYMENT_FIELDS)
            added_orders = 0
            added_payments = 0
            try:
                known_order_ids = set(
                    db.session.execute(db.select(Order.order_id)).scalars()
                )
                for row in order_rows:
                    if row["order_id"] in known_order_ids:
                        continue
                    order = Order(
                        order_id=row["order_id"],
                        customer_name=row["customer_name"],
                        phone=row["phone"] or "",
                        order_date=parse_date(row["order_date"]),
                        item=row["item"],
                        size=row["size"] or "",
                        color=row["color"] or "",
                        quantity=int(row["quantity"]),
                        unit_price=parse_amount(row["unit_price"]),
                        order_total=parse_amount(row["order_total"]),
                        expected_delivery=(
                            parse_date(row["expected_delivery"])
                            if row["expected_delivery"] else None
                        ),
                        status=row["status"],
                        notes=row["notes"] or "",
                    )
                    if order.quantity <= 0 or order.status not in ORDER_STATUSES:
                        raise ValueError(f"Invalid order data for {row['order_id']}.")
                    db.session.add(order)
                    known_order_ids.add(order.order_id)
                    added_orders += 1
                db.session.flush()
                known_payment_ids = set(
                    db.session.execute(db.select(Payment.payment_id)).scalars()
                )
                for row in payment_rows:
                    if row["payment_id"] in known_payment_ids:
                        continue
                    if row["order_id"] not in known_order_ids:
                        raise ValueError(
                            f"Payment {row['payment_id']} references unknown order "
                            f"{row['order_id']}."
                        )
                    if row["method"] not in PAYMENT_METHODS:
                        raise ValueError(
                            f"Payment {row['payment_id']} has an unsupported method."
                        )
                    amount = parse_amount(row["amount"])
                    if amount <= 0:
                        raise ValueError(
                            f"Payment {row['payment_id']} must be greater than zero."
                        )
                    db.session.add(Payment(
                        payment_id=row["payment_id"],
                        order_id=row["order_id"],
                        payment_date=parse_date(row["payment_date"]),
                        amount=amount,
                        method=row["method"],
                        reference=row["reference"] or "",
                    ))
                    known_payment_ids.add(row["payment_id"])
                    added_payments += 1
                db.session.commit()
            except (ValueError, InvalidOperation, csv.Error, OSError) as error:
                db.session.rollback()
                raise click.ClickException(str(error)) from error
        click.echo(
            f"Imported {added_orders} orders and {added_payments} payments "
            "(existing IDs skipped)."
        )

    return application


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter dates in YYYY-MM-DD format.") from error


def parse_amount(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
        rounded = amount.quantize(CENT)
        if rounded != amount:
            raise InvalidOperation
        return rounded
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Enter a valid amount with no more than 2 decimal places.") from error


def payment_totals(orders):
    if not orders:
        return {}
    order_ids = [order.order_id for order in orders]
    paid_rows = db.session.execute(
        db.select(Payment.order_id, func.sum(Payment.amount))
        .where(Payment.order_id.in_(order_ids))
        .group_by(Payment.order_id)
    ).all()
    paid_by_order = {order_id: amount for order_id, amount in paid_rows}
    return {
        order.order_id: (
            paid_by_order.get(order.order_id, Decimal("0.00")) or Decimal("0.00"),
            order.order_total
            - (paid_by_order.get(order.order_id, Decimal("0.00")) or Decimal("0.00")),
        )
        for order in orders
    }


def generated_record_id(model, field_name, prefix, record_id):
    field = getattr(model, field_name)
    candidate = f"{prefix}-{record_id:05d}"
    exists = db.session.execute(
        db.select(model.id).where(field == candidate)
    ).first()
    if exists:
        candidate = f"{prefix}-{uuid4().hex[:16].upper()}"
    return candidate


def get_order_or_404(order_id):
    order = db.session.execute(
        db.select(Order).where(Order.order_id == order_id)
    ).scalar_one_or_none()
    if order is None:
        abort(404)
    return order


def read_csv(path, fields):
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != fields:
                raise ValueError(f"{path.name} has an unexpected or missing header.")
            return list(reader)
    except OSError as error:
        raise ValueError(f"Cannot read {path}: {error}") from error


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
