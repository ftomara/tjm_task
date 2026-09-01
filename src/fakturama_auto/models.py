"""Domain models for the order document extracted from the source image.

There are deliberately *two* layers here:

``Raw*``
    What the LLM is asked to fill in. Every monetary value is a **string**,
    because JSON numbers are IEEE-754 doubles and we later assert that the
    line totals reconcile to the cent. ``"0.1"`` survives that round trip;
    ``0.1`` does not.

``OrderDoc`` and friends
    The domain model the UI automation actually drives. Money is ``Decimal``
    and dates are ``date``.

:func:`normalise` is the only bridge between the two, so every coercion,
trim and default lives in exactly one place.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Layer 1: what the LLM returns
# --------------------------------------------------------------------------


class RawAddress(BaseModel):
    company: str | None = Field(None, description="Company line of the address.")
    street: str | None = Field(None, description="Street and house number.")
    zip_code: str | None = Field(None, description="Postal code.")
    city: str | None = None
    country: str | None = None
    address_extra: str | None = Field(
        None,
        description=(
            "Any additional line (c/o, district, address specification). "
            "Null unless the image actually shows one."
        ),
    )


class RawParty(BaseModel):
    company: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    alias: str | None = Field(None, description="Customer alias / short code, if shown.")
    customer_id: str | None = None
    email: str | None = None
    phone: str | None = None
    billing: RawAddress
    delivery: RawAddress | None = Field(
        None, description="Null when the document shows no separate delivery address."
    )


class RawPayment(BaseModel):
    method: str = Field(description="Payment method exactly as printed, e.g. 'Bank Transfer'.")
    status: Literal["PAID", "UNPAID"]
    payment_date: str | None = Field(
        None, description="ISO yyyy-mm-dd. Null unless the document states a payment date."
    )


class RawLineItem(BaseModel):
    position: int = Field(description="1-based row number as printed.")
    sku: str
    description: str
    quantity: str
    unit: str | None = None
    unit_net_price: str = Field(description="Net price for ONE unit, before discount.")
    discount_percent: str = Field("0", description="Line discount percent. '0' when blank.")
    vat_percent: str
    line_total_net: str = Field(description="Net line total as printed on the document.")


class RawTotals(BaseModel):
    net_total: str
    vat_total: str
    gross_total: str


class RawOrderExtraction(BaseModel):
    """Exactly the shape the vision model is asked to produce."""

    order_date: str = Field(description="ISO yyyy-mm-dd.")
    external_reference: str
    currency: str
    customer: RawParty
    payment: RawPayment
    items: list[RawLineItem]
    totals: RawTotals
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Dotted paths of any field that was blurry, ambiguous or guessed, "
            "e.g. 'items[0].sku'. Empty when everything was legible."
        ),
    )


# --------------------------------------------------------------------------
# Layer 2: the domain model the automation drives
# --------------------------------------------------------------------------


class Address(BaseModel):
    company: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    country: str | None = None
    address_extra: str | None = None

    def is_same_place_as(self, other: Address | None) -> bool:
        """Whether the two addresses point at the same physical location.

        Used for step 2.8: if billing and delivery match, the Main address
        gets *both* role checkboxes and we do not create a second address.
        Company differs between the two on the sample document ("...GmbH" vs
        "...Warehouse"), so the company line is deliberately not compared.
        """
        if other is None:
            return True
        return (
            _norm(self.street) == _norm(other.street)
            and _norm(self.zip_code) == _norm(other.zip_code)
            and _norm(self.city) == _norm(other.city)
            and _norm(self.country) == _norm(other.country)
        )


class Party(BaseModel):
    company: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    alias: str | None = None
    customer_id: str | None = None
    email: str | None = None
    phone: str | None = None
    billing: Address
    delivery: Address | None = None

    @property
    def delivery_matches_billing(self) -> bool:
        return self.billing.is_same_place_as(self.delivery)


class Payment(BaseModel):
    method: str
    is_paid: bool
    payment_date: date | None = None


class LineItem(BaseModel):
    position: int
    sku: str
    description: str
    quantity: Decimal
    unit: str | None = None
    unit_net_price: Decimal
    discount_percent: Decimal
    vat_percent: Decimal
    line_total_net: Decimal

    @property
    def computed_line_total_net(self) -> Decimal:
        """qty x unit price x (1 - discount/100), rounded to 2dp."""
        gross_of_discount = self.quantity * self.unit_net_price
        after_discount = gross_of_discount * (Decimal(1) - self.discount_percent / Decimal(100))
        return _round2(after_discount)

    @property
    def product_master_gross_price(self) -> Decimal:
        """Step 3.9: unit net x (1 + VAT/100), 2dp.

        The transaction-line discount is deliberately NOT applied - this is the
        product master price, not the order line price.
        """
        return _round2(self.unit_net_price * (Decimal(1) + self.vat_percent / Decimal(100)))

    @property
    def vat_rate_name(self) -> str:
        """Fakturama VAT record name, e.g. 'VAT 19%'."""
        return f"VAT {_percent_str(self.vat_percent)}%"


class Totals(BaseModel):
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal


class OrderDoc(BaseModel):
    """Normalised, validated order ready to be typed into Fakturama."""

    order_date: date
    external_reference: str
    currency: str
    customer: Party
    payment: Payment
    items: list[LineItem]
    totals: Totals
    low_confidence_fields: list[str] = Field(default_factory=list)

    @property
    def computed_net_total(self) -> Decimal:
        return _round2(sum((i.computed_line_total_net for i in self.items), Decimal(0)))

    @property
    def computed_vat_total(self) -> Decimal:
        return _round2(
            sum(
                (i.computed_line_total_net * i.vat_percent / Decimal(100) for i in self.items),
                Decimal(0),
            )
        )

    @property
    def computed_gross_total(self) -> Decimal:
        return _round2(self.computed_net_total + self.computed_vat_total)

    @property
    def distinct_vat_rates(self) -> list[Decimal]:
        """Unique VAT percentages, in first-seen order (each needs a Fakturama VAT record)."""
        seen: list[Decimal] = []
        for item in self.items:
            if item.vat_percent not in seen:
                seen.append(item.vat_percent)
        return seen


# --------------------------------------------------------------------------
# Normalisation: Raw -> domain
# --------------------------------------------------------------------------


class NormalisationError(ValueError):
    """A Raw field could not be coerced into its domain type."""


def normalise(raw: RawOrderExtraction) -> OrderDoc:
    """Coerce a raw LLM extraction into the domain model.

    Raises :class:`NormalisationError` rather than silently defaulting, so a
    misread number surfaces here instead of halfway through typing it into
    the UI.
    """
    return OrderDoc(
        order_date=_to_date(raw.order_date, "order_date"),
        external_reference=raw.external_reference.strip(),
        currency=raw.currency.strip().upper(),
        customer=Party(
            company=_clean(raw.customer.company),
            first_name=_clean(raw.customer.first_name),
            last_name=_clean(raw.customer.last_name),
            alias=_clean(raw.customer.alias),
            customer_id=_clean(raw.customer.customer_id),
            email=_clean(raw.customer.email),
            phone=_clean(raw.customer.phone),
            billing=_to_address(raw.customer.billing),
            delivery=_to_address(raw.customer.delivery) if raw.customer.delivery else None,
        ),
        payment=Payment(
            method=raw.payment.method.strip(),
            is_paid=raw.payment.status == "PAID",
            payment_date=(
                _to_date(raw.payment.payment_date, "payment.payment_date")
                if raw.payment.payment_date
                else None
            ),
        ),
        items=[
            LineItem(
                position=item.position,
                sku=item.sku.strip(),
                description=item.description.strip(),
                quantity=_to_decimal(item.quantity, f"items[{idx}].quantity"),
                unit=_clean(item.unit),
                unit_net_price=_to_decimal(item.unit_net_price, f"items[{idx}].unit_net_price"),
                discount_percent=_to_decimal(
                    item.discount_percent, f"items[{idx}].discount_percent"
                ),
                vat_percent=_to_decimal(item.vat_percent, f"items[{idx}].vat_percent"),
                line_total_net=_to_decimal(item.line_total_net, f"items[{idx}].line_total_net"),
            )
            for idx, item in enumerate(raw.items)
        ],
        totals=Totals(
            net_total=_to_decimal(raw.totals.net_total, "totals.net_total"),
            vat_total=_to_decimal(raw.totals.vat_total, "totals.vat_total"),
            gross_total=_to_decimal(raw.totals.gross_total, "totals.gross_total"),
        ),
        low_confidence_fields=list(raw.low_confidence_fields),
    )


# --------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _norm(value: str | None) -> str:
    """Casefolded, whitespace-collapsed form for comparing address lines."""
    return " ".join((value or "").split()).casefold()


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _percent_str(value: Decimal) -> str:
    """19 -> '19', 7.5 -> '7.5'. Keeps VAT record names tidy."""
    normalised = value.normalize()
    return format(normalised, "f")


def _to_decimal(value: str, field: str) -> Decimal:
    """Parse money/quantity text into Decimal.

    Tolerates the punctuation an OCR or vision pass tends to emit: currency
    symbols, thousands separators, a trailing percent sign, and the German
    decimal comma.
    """
    text = (value or "").strip()
    for token in ("EUR", "€", "%", "$"):
        text = text.replace(token, "")
    text = text.strip()

    if "," in text and "." in text:
        # "1.234,56" (de) vs "1,234.56" (en) - the rightmost separator wins.
        text = (
            text.replace(".", "").replace(",", ".")
            if text.rfind(",") > text.rfind(".")
            else text.replace(",", "")
        )
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise NormalisationError(f"{field}: cannot parse {value!r} as a number") from exc


def _to_date(value: str, field: str) -> date:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise NormalisationError(
            f"{field}: expected ISO yyyy-mm-dd, got {value!r}"
        ) from exc


def _to_address(raw: RawAddress) -> Address:
    return Address(
        company=_clean(raw.company),
        street=_clean(raw.street),
        zip_code=_clean(raw.zip_code),
        city=_clean(raw.city),
        country=_clean(raw.country),
        address_extra=_clean(raw.address_extra),
    )
