# Fakturama Image-to-Cash Automation

Turns a photographed/rendered sales order into a saved, linked, paid **Order → Invoice** pair inside [Fakturama](https://www.fakturama.info/) (an Eclipse RCP / SWT desktop accounting app), driven entirely through Windows UI Automation — no hardcoded screen coordinates anywhere on the path.

**Demo:** [demo.mp4](https://drive.google.com/file/d/1MZW8toXVQ-JoqdYCHUqcD3YkswINFEvZ/view?usp=drive_link) — a full recorded run against a wiped workspace.
## What it does

1. **Reads the order image** with a vision LLM (Gemini) and validates the result arithmetically — every line's `qty × price × (1 − discount%)` and the printed net/VAT/gross totals all have to agree with each other before anything gets typed into the UI.
2. **Drives Fakturama** to create the Debtor, the Products, the VAT rate, the Order (header, address, line items, discounts), the linked follow-up Invoice, and the payment status — reading every value back after writing it, not just clicking and hoping.
3. **Verifies** by reading the totals off the saved Order and the paid state off the saved Invoice, and prints a summary.

See [`docs/design.md`](docs/design.md) for the full design rationale (Part 1 of the assessment), and [`docs/challenges.md`](docs/challenges.md) for a real-time log of every bug this project's own live testing surfaced, with root cause and fix for each — genuinely the most substantial document in the repo, and the best place to see what actually went wrong.

## Setup

Requires Windows, Python 3.10+, and a local Fakturama 2.x install.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` (never commit it — it's gitignored) and add your key:

```
GEMINI_API_KEY=...
# FAKTURAMA_EXE=C:\Program Files\Fakturama2\Fakturama.exe   # only if it's not in a usual install location
```

## Usage

The console script is `fakturama-auto` (installed by `pip install -e .`; use `.venv\Scripts\fakturama-auto.exe` if you haven't activated the venv).

**Run the whole flow, extracting live from an image:**

```
fakturama-auto run --image assets\test_orders\image.png --launch
```

- `--launch` starts Fakturama if it isn't already running; omit it to attach to an already-open instance instead (the default — an RCP cold start plus workspace/database init is slow, so attaching to a running app is the faster loop while iterating).
- `--provider gemini` picks the vision model (`gemini` is the only one and the default).
- The extraction is kept in memory and also saved as a timestamped backup under `artifacts/<run-id>/extraction.json` for reference — never as the trusted fixture, so nothing is silently replayed on the next run by accident.

**Replay a previously-saved extraction instead of calling the API** (useful once you trust an extraction, or if you're rate-limited):

```
fakturama-auto run --fixture assets\extraction.fixture.json --launch
```

**Just extract and validate, without touching Fakturama at all:**

```
fakturama-auto extract --image assets\test_orders\image.png --save-fixture
```

**Inspect the live UIA tree** (what this project used, constantly, to ground every locator against the real widget tree rather than guessing):

```
fakturama-auto dump-tree --list          # see what top-level windows exist
fakturama-auto dump-tree --window Fakturama --depth 12
```

### Starting from a clean slate

Fakturama's workspace/database lives in `FData/` next to the project (gitignored — it's live app state, not source). To reset to a pristine workspace:

```powershell
Remove-Item -Recurse -Force FData
```

The next launch recreates it from scratch. Note that a from-scratch workspace is missing some master data a normal Fakturama install seeds automatically (notably a default Shipping method) — the automation handles this itself; see `docs/challenges.md`.

## How it's put together

```
src/fakturama_auto/
  extract/     image -> validated OrderDoc (vision LLM + arithmetic reconciliation gate)
  uia/         the grounding layer: Locator (find-by-identity-or-structure), waits, session
  app/         one page object per Fakturama editor (Order, Contact, Invoice, Product, VAT, Shipping, PaymentTerm)
  flow/        orchestrates the page objects into the end-to-end brief
  models.py    the two-layer Pydantic schema (Raw* strings from the LLM -> Decimal domain model)
  cli.py       `fakturama-auto extract|run|dump-tree`
```

The `flow` layer follows an **order-first** shape: the Order opens first and stays open for the run; the Debtor and every Product are resolved through the Order's own selectors, and each is created only on a genuine miss — never spec­ulatively upfront. The one deliberate exception, and a couple of other real Fakturama quirks that shaped this design, are explained inline in `flow/order_flow.py`'s own docstrings and in `docs/challenges.md`.

Every write is read back and compared before the flow moves on; every ambiguous state (a search with no match, a value that still doesn't verify after a retry) raises a typed `ManualReviewRequired` rather than guessing.

## Testing

```
pytest
```

43 tests, all fake-object based — none require a running Fakturama. Most are regression tests written *after* a live bug was found and fixed, reproducing the exact failing shape (a misleading combobox read-back, a traversal-order bug, a `TypeError` a native call swallowed as a timeout, and so on) so the fix can't silently regress. See `docs/challenges.md` for which test corresponds to which bug.

## Known limitations

- **Coordinate-offset clicks on two genuinely opaque grids.** The Items grid and the address/product selector dialogs render their rows with no corresponding UIA nodes at all — confirmed live, not assumed. Rows are reached by a coordinate offset from a grounded anchor (a nearby labeled control's rectangle), which is the closest this specific widget allows to real grounding, not a literal screen coordinate — but it's the one place in this codebase that isn't pure identity/structure matching.
- **A handful of cold-start races.** The very first UI action of a session (right after Fakturama launches) can occasionally need a retry that a warm session never does — documented and mitigated in `docs/challenges.md`, but not eliminated at the source since it's Fakturama's own startup behaviour, not something this project controls.
- **Only the bundled sample order image has been run fully end to end and independently verified** (via a genuine app relaunch reading the saved data back from disk, not just from the widget). Five additional synthetic test images covering other currencies, payment methods, and VAT rates are in `assets/test_orders/` but haven't all been run through to completion.
- **Currency is cosmetic.** Fakturama's own amount fields render with a fixed `$`-style format regardless of the extracted currency; nothing in this project sets a per-order currency.

## With three more hours

- Track down the exact Country-combobox mismatch a later manual run surfaced (an extracted country string not matching Fakturama's dropdown value exactly) - the same class of "value looks right, doesn't match the widget's actual option list" issue already fixed for a couple of other combos, just not yet chased down for this one.
- Run all five synthetic test images (different currency, payment method, and VAT-rate combinations) through to a fully verified finish, not just the original.
- Replace the coordinate-offset row clicks on the two opaque grids with the OCR-based rung the design doc describes as the fallback for a genuinely blind UIA tree, so nothing in the flow depends on a fixed row height holding across Fakturama versions.
    - Longer-term, upstream a fix for the opaque NatTable/grid accessibility gap into pywinauto (or SWT's own accessibility bridge) instead of working around it locally - turns a one-off hardcoded-offset hack into a real fix any future SWT automation project could reuse instead of re-solving the same blind-grid problem.
- Add a real integration-style smoke test that launches Fakturama in CI-like conditions (or a recorded-session replay) rather than relying entirely on fake-object unit tests plus manual runs for the UI-driving code.
- Turn this into a standing service instead of a one-shot CLI run: a watched input folder plus a filesystem listener that picks up each new order image and queues it through the automation automatically. Worth doing properly once more than one worker instance can run concurrently - needs the same handling any queue-consumer needs (a claim/lock step per job, e.g. a DB row lock or an atomic move out of the watched folder) so two workers can't grab and double-process the same image.

    ```mermaid
    flowchart LR
        FOLDER["Input invoices\nfolder"]
        OBSERVER["Folder-changes\nobserver"]
        QUEUE[["images Q"]]
        AUTO["Fakturama\nImage-to-Cash\nAutomation"]

        OBSERVER -->|"observe folder changes\nfor newly added images"| FOLDER
        OBSERVER -->|"queue images for\nextraction and adding\nto fakturama"| QUEUE
        QUEUE --> AUTO

        style OBSERVER fill:#123a2e,stroke:#2f9e6f,color:#c9f2df
        style QUEUE fill:#1c2541,stroke:#5b7fff,color:#c9d6f2
    ```

- Port the `uia/` grounding layer to Mac/Linux accessibility APIs (AXUIElement / AT-SPI) so the automation isn't Windows-only. The extraction and orchestration layers already carry over unchanged thanks to the layering in §7 - it's specifically `uia/` and every locator in `app/` that would need rebuilding and re-grounding against a different accessibility tree.
