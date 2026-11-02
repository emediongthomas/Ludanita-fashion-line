"""Order and payment tracker for Ludanita Fashion Line."""

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent
ORDERS_FILE = DATA_DIR / "ludanita_orders.csv"
PAYMENTS_FILE = DATA_DIR / "ludanita_payments.csv"

ORDER_FIELDS = [
    "order_id",
    "customer_name",
    "phone",
    "order_date",
    "item",
    "size",
    "color",
    "quantity",
    "unit_price",
    "order_total",
    "expected_delivery",
    "status",
    "notes",
]
PAYMENT_FIELDS = [
    "payment_id",
    "order_id",
    "payment_date",
    "amount",
    "method",
    "reference",
]
ORDER_STATUSES = ("Pending", "In Production", "Ready", "Delivered")
PAYMENT_METHODS = ("Cash", "Bank Transfer", "Card", "Mobile Money", "Other")
CENT = Decimal("0.01")


def ensure_data_files():
    for path, fields in (
        (ORDERS_FILE, ORDER_FIELDS),
        (PAYMENTS_FILE, PAYMENT_FIELDS),
    ):
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as file:
                csv.DictWriter(file, fieldnames=fields).writeheader()


def read_records(path, fields):
    ensure_data_files()
    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        file_fields = reader.fieldnames
        if file_fields is None:
            with path.open("w", newline="", encoding="utf-8") as fresh_file:
                csv.DictWriter(fresh_file, fieldnames=fields).writeheader()
            return []
        normalized_fields = [field.strip() if field is not None else field for field in file_fields]
        if normalized_fields != fields:
            raise ValueError(f"{path.name} has an unexpected or missing header.")
        return list(reader)


def append_record(path, fields, record):
    ensure_data_files()
    with path.open("a", newline="", encoding="utf-8") as file:
        csv.DictWriter(file, fieldnames=fields).writerow(record)


def next_id(prefix, records, field):
    numbers = []
    for record in records:
        value = record[field]
        if value.startswith(f"{prefix}-") and value[len(prefix) + 1 :].isdigit():
            numbers.append(int(value[len(prefix) + 1 :]))
    return f"{prefix}-{max(numbers, default=0) + 1:05d}"


def prompt_text(label, required=True):
    while True:
        value = input(f"{label}: ").strip()
        if value or not required:
            return value
        print("This field is required.")


def prompt_date(label, default_today=False, optional=False):
    today = date.today().isoformat()
    suffix = f" [{today}]" if default_today else " (YYYY-MM-DD)"
    if optional:
        suffix += " (leave blank if unknown)"
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and optional:
            return ""
        if not value and default_today:
            return today
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            print("Enter a valid date in YYYY-MM-DD format.")


def prompt_amount(label, allow_zero=False):
    while True:
        value = input(f"{label}: ").strip()
        try:
            amount = Decimal(value)
            if not amount.is_finite():
                raise InvalidOperation
            if amount < 0 or (amount == 0 and not allow_zero):
                raise InvalidOperation
            rounded = amount.quantize(CENT)
            if rounded != amount:
                raise InvalidOperation
            return rounded
        except (InvalidOperation, ValueError):
            qualifier = "zero or a positive" if allow_zero else "positive"
            print(f"Enter a {qualifier} amount with no more than 2 decimal places.")


def money(value):
    return f"{Decimal(value):,.2f}"


def get_order_totals(orders, payments):
    paid_by_order = {}
    for payment in payments:
        order_id = payment["order_id"]
        paid_by_order[order_id] = (
            paid_by_order.get(order_id, Decimal("0.00"))
            + Decimal(payment["amount"])
        )
    totals = {}
    for order in orders:
        total = Decimal(order["order_total"])
        paid = paid_by_order.get(order["order_id"], Decimal("0.00"))
        totals[order["order_id"]] = (paid, total - paid)
    return totals


def print_orders(orders, payments):
    if not orders:
        print("\nNo orders have been added yet.")
        return

    totals = get_order_totals(orders, payments)
    headings = ("ORDER ID", "CUSTOMER", "ITEM", "DATE", "TOTAL", "PAID", "DUE", "STATUS")
    rows = [
        (
            order["order_id"],
            order["customer_name"],
            order["item"],
            order["order_date"],
            money(order["order_total"]),
            money(totals[order["order_id"]][0]),
            money(totals[order["order_id"]][1]),
            order["status"],
        )
        for order in orders
    ]
    widths = [
        max(len(heading), *(len(row[index]) for row in rows))
        for index, heading in enumerate(headings)
    ]
    print()
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headings)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def add_order():
    print("\nNEW CUSTOMER ORDER")
    customer_name = prompt_text("Customer name")
    phone = prompt_text("Phone number", required=False)
    order_date = prompt_date("Order date", default_today=True)
    item = prompt_text("Item / style")
    size = prompt_text("Size", required=False)
    color = prompt_text("Color", required=False)
    while True:
        quantity_text = prompt_text("Quantity")
        try:
            quantity = int(quantity_text)
            if quantity > 0:
                break
        except ValueError:
            pass
        print("Quantity must be a whole number greater than zero.")
    unit_price = prompt_amount("Unit price")
    order_total = (unit_price * quantity).quantize(CENT)
    expected_delivery = prompt_date("Expected delivery date", optional=True)
    notes = prompt_text("Notes", required=False)

    orders = read_records(ORDERS_FILE, ORDER_FIELDS)
    order_id = next_id("ORD", orders, "order_id")
    record = {
        "order_id": order_id,
        "customer_name": customer_name,
        "phone": phone,
        "order_date": order_date,
        "item": item,
        "size": size,
        "color": color,
        "quantity": str(quantity),
        "unit_price": str(unit_price),
        "order_total": str(order_total),
        "expected_delivery": expected_delivery,
        "status": "Pending",
        "notes": notes,
    }
    append_record(ORDERS_FILE, ORDER_FIELDS, record)
    print(f"\nOrder {order_id} saved. Order total: {money(order_total)}")


def record_payment():
    orders = read_records(ORDERS_FILE, ORDER_FIELDS)
    payments = read_records(PAYMENTS_FILE, PAYMENT_FIELDS)
    if not orders:
        print("\nAdd an order before recording a payment.")
        return

    print_orders(orders, payments)
    order_ids = {order["order_id"] for order in orders}
    while True:
        order_id = prompt_text("\nOrder ID").upper()
        if order_id in order_ids:
            break
        print("That order ID was not found.")

    paid, balance = get_order_totals(orders, payments)[order_id]
    if balance <= 0:
        print("This order is already fully paid.")
        return
    print(f"Outstanding balance: {money(balance)}")
    while True:
        amount = prompt_amount("Payment amount")
        if amount <= balance:
            break
        print(f"Payment cannot exceed the outstanding balance of {money(balance)}.")
    payment_date = prompt_date("Payment date", default_today=True)
    print("Payment methods: " + ", ".join(PAYMENT_METHODS))
    while True:
        method = prompt_text("Payment method")
        match = next(
            (option for option in PAYMENT_METHODS if option.casefold() == method.casefold()),
            None,
        )
        if match:
            method = match
            break
        print("Choose one of the listed payment methods.")
    reference = prompt_text("Reference / receipt number", required=False)

    payment_id = next_id("PAY", payments, "payment_id")
    append_record(
        PAYMENTS_FILE,
        PAYMENT_FIELDS,
        {
            "payment_id": payment_id,
            "order_id": order_id,
            "payment_date": payment_date,
            "amount": str(amount),
            "method": method,
            "reference": reference,
        },
    )
    print(f"\nPayment {payment_id} saved. Remaining balance: {money(balance - amount)}")


def show_payments():
    payments = read_records(PAYMENTS_FILE, PAYMENT_FIELDS)
    orders = {order["order_id"]: order for order in read_records(ORDERS_FILE, ORDER_FIELDS)}
    if not payments:
        print("\nNo payments have been recorded yet.")
        return

    headings = ("PAYMENT ID", "DATE", "ORDER ID", "CUSTOMER", "AMOUNT", "METHOD", "REFERENCE")
    rows = [
        (
            payment["payment_id"],
            payment["payment_date"],
            payment["order_id"],
            orders[payment["order_id"]]["customer_name"],
            money(payment["amount"]),
            payment["method"],
            payment["reference"],
        )
        for payment in payments
    ]
    widths = [
        max(len(heading), *(len(row[index]) for row in rows))
        for index, heading in enumerate(headings)
    ]
    print()
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headings)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def update_order_status():
    orders = read_records(ORDERS_FILE, ORDER_FIELDS)
    payments = read_records(PAYMENTS_FILE, PAYMENT_FIELDS)
    if not orders:
        print("\nNo orders are available to update.")
        return

    print_orders(orders, payments)
    order_ids = {order["order_id"] for order in orders}
    while True:
        order_id = prompt_text("\nOrder ID").upper()
        if order_id in order_ids:
            break
        print("That order ID was not found.")
    print("Statuses: " + ", ".join(ORDER_STATUSES))
    while True:
        status = prompt_text("New status")
        match = next(
            (option for option in ORDER_STATUSES if option.casefold() == status.casefold()),
            None,
        )
        if match:
            status = match
            break
        print("Choose one of the listed statuses.")

    for order in orders:
        if order["order_id"] == order_id:
            order["status"] = status
            break
    with ORDERS_FILE.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ORDER_FIELDS)
        writer.writeheader()
        writer.writerows(orders)
    print(f"\nOrder {order_id} status updated to {status}.")


def show_dashboard():
    orders = read_records(ORDERS_FILE, ORDER_FIELDS)
    payments = read_records(PAYMENTS_FILE, PAYMENT_FIELDS)
    totals = get_order_totals(orders, payments)
    order_value = sum(
        (Decimal(order["order_total"]) for order in orders), Decimal("0.00")
    )
    amount_collected = sum(
        (Decimal(payment["amount"]) for payment in payments), Decimal("0.00")
    )
    outstanding = sum((balance for _, balance in totals.values()), Decimal("0.00"))
    print("\nLUDANITA FASHION LINE | BUSINESS SUMMARY")
    print("-" * 46)
    print(f"Orders recorded:      {len(orders)}")
    print(f"Order value:          {money(order_value)}")
    print(f"Payments collected:   {money(amount_collected)}")
    print(f"Outstanding balance:  {money(outstanding)}")
    print("\nOrders by status:")
    for status in ORDER_STATUSES:
        count = sum(order["status"] == status for order in orders)
        print(f"  {status:<16} {count}")


def main():
    ensure_data_files()
    actions = {
        "1": ("Business summary", show_dashboard),
        "2": ("Add customer order", add_order),
        "3": ("Record payment", record_payment),
        "4": ("View orders", lambda: print_orders(
            read_records(ORDERS_FILE, ORDER_FIELDS),
            read_records(PAYMENTS_FILE, PAYMENT_FIELDS),
        )),
        "5": ("View payment history", show_payments),
        "6": ("Update order status", update_order_status),
    }
    print("LUDANITA FASHION LINE")
    print("Customer orders and payments")
    while True:
        print("\n" + "\n".join(f"{key}. {label}" for key, (label, _) in actions.items()))
        print("0. Exit")
        choice = input("\nSelect an option: ").strip()
        if choice == "0":
            print("Goodbye.")
            return
        action = actions.get(choice)
        if action is None:
            print("Choose a number from the menu.")
            continue
        action[1]()


if __name__ == "__main__":
    try:
        main()
    except (OSError, csv.Error, ValueError) as error:
        print(f"\nUnable to continue: {error}")
        raise SystemExit(1) from error