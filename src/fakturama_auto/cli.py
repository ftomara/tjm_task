"""Command line entry point.

    fakturama-auto extract            # image -> OrderDoc, validated and printed
    fakturama-auto extract --save-fixture
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import DEFAULT_FIXTURE, DEFAULT_ORDER_IMAGE, load_settings
from .errors import AutomationError
from .extract import PROVIDERS, build_extractor, save, validate
from .models import OrderDoc

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fakturama-auto",
        description="Turn an order image into a saved, verified Fakturama Order and Invoice.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract_cmd = sub.add_parser("extract", help="Extract and validate the order image.")
    extract_cmd.add_argument(
        "--image", type=Path, default=DEFAULT_ORDER_IMAGE, help="Source order image."
    )
    extract_cmd.add_argument(
        "--provider", choices=PROVIDERS, default="gemini", help="Extraction provider."
    )
    extract_cmd.add_argument(
        "--fixture", type=Path, default=DEFAULT_FIXTURE, help="Fixture path to read or write."
    )
    extract_cmd.add_argument(
        "--save-fixture",
        action="store_true",
        help="Write the extraction to --fixture so later runs can replay it offline.",
    )
    extract_cmd.set_defaults(func=cmd_extract)

    dump_cmd = sub.add_parser(
        "dump-tree", help="Inspect the live UIA tree (use this before writing locators)."
    )
    dump_cmd.add_argument(
        "--list", action="store_true", help="List top-level windows instead of dumping a tree."
    )
    dump_cmd.add_argument(
        "--window",
        default=r".*[Ff]akturama.*",
        help="Regex matching the top-level window title to dump.",
    )
    dump_cmd.add_argument("--depth", type=int, default=12, help="Maximum tree depth.")
    dump_cmd.add_argument("--out", type=Path, help="Write the dump to a file as well.")
    dump_cmd.set_defaults(func=cmd_dump_tree)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AutomationError as exc:
        console.print(f"[bold red]{type(exc).__name__}[/]: {exc}")
        return 1


def cmd_extract(args: argparse.Namespace) -> int:
    settings = load_settings()

    required_key = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(args.provider)
    have_key = {"gemini": settings.gemini_api_key, "anthropic": settings.anthropic_api_key}.get(
        args.provider
    )
    if required_key and not have_key:
        console.print(
            f"[yellow]No {required_key} found.[/] Copy .env.example to .env and add a key, "
            "or use --provider fixture."
        )

    extractor = build_extractor(args.provider, settings, args.fixture)
    console.print(f"Extracting [cyan]{args.image}[/] via [cyan]{extractor.name}[/] ...")

    doc = extractor.extract(args.image)
    report = validate(doc)

    _render(doc)
    console.print(
        Panel(
            report.render(),
            title="Reconciliation" + ("" if report.ok else " - FAILED"),
            border_style="green" if report.ok else "red",
        )
    )

    out = settings.run_dir / "extraction.json"
    save(doc, out)
    console.print(f"Wrote [green]{out}[/]")

    if args.save_fixture:
        save(doc, args.fixture)
        console.print(f"Wrote fixture [green]{args.fixture}[/]")

    return 0 if report.ok else 2


def cmd_dump_tree(args: argparse.Namespace) -> int:
    from .uia import dump_window, list_top_level_windows, write_dump

    if args.list:
        console.print(list_top_level_windows())
        return 0

    try:
        text = dump_window(args.window, max_depth=args.depth)
    except LookupError as exc:
        console.print(f"[red]{exc}[/]")
        console.print("Run [cyan]fakturama-auto dump-tree --list[/] to see what is open.")
        return 1

    console.print(text, highlight=False)
    if args.out:
        console.print(f"Wrote [green]{write_dump(text, args.out)}[/]")
    return 0


def _render(doc: OrderDoc) -> None:
    header = Table.grid(padding=(0, 2))
    header.add_column(style="dim")
    header.add_column()
    header.add_row("External ref", doc.external_reference)
    header.add_row("Order date", doc.order_date.isoformat())
    header.add_row("Currency", doc.currency)
    header.add_row("Company", doc.customer.company or "-")
    header.add_row(
        "Contact", " ".join(filter(None, [doc.customer.first_name, doc.customer.last_name])) or "-"
    )
    header.add_row("Alias", doc.customer.alias or "-")
    header.add_row("Email", doc.customer.email or "-")
    header.add_row("Phone", doc.customer.phone or "-")
    header.add_row(
        "Billing",
        _format_address(doc.customer.billing),
    )
    header.add_row(
        "Delivery",
        "same as billing"
        if doc.customer.delivery_matches_billing
        else _format_address(doc.customer.delivery),
    )
    header.add_row("Payment", doc.payment.method)
    header.add_row(
        "Paid",
        "yes"
        + (f" on {doc.payment.payment_date.isoformat()}" if doc.payment.payment_date else "")
        if doc.payment.is_paid
        else "no",
    )
    console.print(Panel(header, title="Order", border_style="cyan"))

    items = Table(show_edge=False, header_style="bold")
    for column in ("#", "SKU", "Description", "Qty", "Unit net", "Disc %", "VAT %", "Line net"):
        items.add_column(column, justify="right" if column not in ("SKU", "Description") else "left")
    for item in doc.items:
        items.add_row(
            str(item.position),
            item.sku,
            item.description,
            f"{item.quantity:g}",
            f"{item.unit_net_price:.2f}",
            f"{item.discount_percent:g}",
            f"{item.vat_percent:g}",
            f"{item.line_total_net:.2f}",
        )
    console.print(Panel(items, title="Items", border_style="cyan"))

    totals = Table.grid(padding=(0, 2))
    totals.add_column(style="dim")
    totals.add_column(justify="right")
    totals.add_column(style="dim")
    totals.add_row("Net", f"{doc.totals.net_total:.2f}", f"(computed {doc.computed_net_total:.2f})")
    totals.add_row("VAT", f"{doc.totals.vat_total:.2f}", f"(computed {doc.computed_vat_total:.2f})")
    totals.add_row(
        "Gross", f"{doc.totals.gross_total:.2f}", f"(computed {doc.computed_gross_total:.2f})"
    )
    console.print(Panel(totals, title="Totals", border_style="cyan"))


def _format_address(address) -> str:
    if address is None:
        return "-"
    parts = [
        address.company,
        address.address_extra,
        address.street,
        " ".join(filter(None, [address.zip_code, address.city])),
        address.country,
    ]
    return "\n".join(p for p in parts if p) or "-"


if __name__ == "__main__":
    sys.exit(main())
