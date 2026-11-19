from decimal import Decimal
import json
import sys
from pydantic import BaseModel


class ChecksumError(Exception):
    pass


class LineItem(BaseModel):
    sku: str
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal


class Invoice(BaseModel):
    vendor: str
    invoice_id: str
    items: list[LineItem]
    tax: Decimal
    credits: Decimal
    total: Decimal


def validate_and_reconcile(
    inv: Invoice, contract_rates: dict[str, Decimal], prev_total: Decimal
) -> dict:
    for r in inv.items:
        expected_row = (r.qty * r.unit_price).quantize(Decimal("0.01"))
        if expected_row != r.line_total:
            raise ChecksumError(
                f"Row mismatch on {r.sku}: {expected_row} != {r.line_total}"
            )

    computed = (
        sum((r.line_total for r in inv.items), Decimal("0.00"))
        + inv.tax
        - inv.credits
    )
    if abs(computed - inv.total) > Decimal("0.01"):
        raise ChecksumError(
            f"Total mismatch on {inv.vendor} ({inv.invoice_id}): "
            f"parsed={computed} vs footer={inv.total} -> quarantine & trigger Devin Bot"
        )

    anomalies = []
    for r in inv.items:
        base = contract_rates.get(r.sku)
        if base is None:
            anomalies.append(f"NEW_FEE:{r.sku}:${r.line_total}")
        elif r.unit_price > base:
            anomalies.append(f"RATE_HIKE:{r.sku}:${base}->${r.unit_price}")

    delta_pct = (inv.total - prev_total) / prev_total if prev_total else Decimal("0")
    if anomalies or delta_pct > Decimal("0.05"):
        return {
            "action": "QUEUE_GMAIL_DRAFT",
            "anomalies": anomalies,
            "delta_pct": f"{delta_pct:.4f}",
        }
    return {"action": "AUTO_FILE_SILENT", "delta_pct": f"{delta_pct:.4f}"}


if __name__ == "__main__":
    fixture_path = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/vendor_pagebreak_fail.json"
    with open(fixture_path) as f:
        payload = json.load(f)
    inv = Invoice(**payload["parsed_invoice"])
    rates = {k: Decimal(v) for k, v in payload["contract_rates"].items()}
    print(validate_and_reconcile(inv, rates, Decimal(payload["prev_total"])))
