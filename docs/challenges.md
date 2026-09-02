# Challenges encountered during implementation

A running log, written at the time each issue was hit rather than
reconstructed afterwards. Each entry: what broke, why, how it was fixed, and
what it changed about the design. Kept separate from `design.md` (Part 1,
written to describe intent) so this stays a record of what implementation
actually surfaced.

---
## A pasted credential-shaped string in the chat transcript

**What happened.** A string with the shape of an auth/session token
(`AQ.Ab8...`) appeared inline in a chat message, immediately before "gemini
api for llm". Whether or not it was a live Gemini key, anything pasted into
a chat transcript sits in a different trust boundary than a local `.env`
file - it's stored wherever that transcript is stored, regardless of
whether the automation ever uses it.

**Fix.** No code fix possible for this one - the advice was to treat it as
compromised: rotate/revoke it at the source and set the replacement only in
`.env`, never in chat.

**Lesson.** Worth stating explicitly and early, not after the fact: secrets
go in `.env` and nowhere else, including scrollback.

---

## `field_after_label` grounded the wrong control: `.descendants()` vs `.children()`

**Symptom.** `OrderEditor.set_price_mode("Net")` failed with:

```
AutomationError: selecting 'Net' in ComboBox auto_id='657350' ...
failed after 3 attempts: ... selected 'Net' but it reads 'Close'.
Available: ['Close']
```

A combobox whose only option is `"Close"` is not a price-mode selector -
that's the shape of a window's own system menu (Restore / Move / Size /
Minimize / Maximize / **Close**).

**Root cause.** `field_after_label` walks the tree from a text label and
takes the Nth control of a given type that follows it, in document order -
the whole point being that this survives layout changes a coordinate
wouldn't. It sourced that walk from pywinauto's built-in `.descendants()`.
`uia.dump.dump_tree`, used throughout this project to inspect the live tree
by hand, does its own explicit recursive `.children()` walk instead - and
the two do not agree on ordering (and possibly not on scope) for this SWT
window. `.descendants()` appears to walk a broader or differently-ordered
view that surfaced the window's own system menu, entirely unrelated to the
subtree being searched.

The two Order-header fields that had already been validated by that point -
`No.` and `Date`'s own value field - both happened to work, because for
those specific lookups the first control of the requested type after the
label was correct under either traversal. The Net/Gross combobox was the
first lookup where the discrepancy actually changed the answer, which is
exactly the kind of failure that stays hidden until the specific case that
exposes it comes along - not something a code review would have caught
without the live app in front of it.

**Fix.** Rewrote `_ordered_descendants` in `app/base.py` to perform the same
explicit depth-first `.children()` recursion `dump_tree` uses, instead of
calling `.descendants()`. Plain `Locator`-based lookups (name/type property
matches - `Cust.Ref.`, the order-level `VAT` combobox, the toolbar button)
were unaffected, since they match by property rather than by walking order.

**Lesson.** "Two functions that both claim to enumerate the same tree" is
not a safe assumption on this application - confirmed by dumping the tree
with one method and querying it with another, and having them disagree.
Added `tests/test_grounding.py`, which fakes the exact failing shape (a
label, a same-level sibling combobox, and a nested value field) and asserts
`field_after_label` finds the sibling without ever calling `.descendants()`
- so this doesn't need a running Fakturama to catch a regression.

**A note on a theory that turned out wrong.** While chasing this, the
Order's "common data" section (holding Cust.Ref., Consultant, VAT, Addresses
and the follow-up group) was found collapsed on one of the two test tabs
open at the time - Eclipse Forms `ExpandableComposite` sections remove their
children from the accessibility tree entirely when collapsed, not just hide
them, so every field in that section briefly vanished from every dump. The
first-pass explanation logged here was that the failed price-mode selection
attempt had somehow triggered the collapse. A second Order, opened fresh
purely to retest the fix above, hit the *exact same* combobox failure
(below) without the section ever collapsing - which rules that theory out.
The real cause of the one-off collapse is unconfirmed and was not chased
further, since it never reproduced again and nothing in the brief's actual
flow interacts with that chevron. Recorded here specifically so a wrong
causal claim doesn't stand uncorrected: the theory was plausible, tested,
and wrong.

---

## `ComboBox` read-back returned three different wrong values, from three different accessors

**Symptom.** With the traversal bug above fixed, the *correct* combobox was
now being found - and its read-back still didn't match. `set_price_mode`
still failed verification, and a separately-checked `VAT_MODE` read had
already returned `"VAT"` where it should have returned `"With VAT"` - logged
as `OK` at the time, because it didn't crash. That's a real near-miss: a
plausible-looking wrong value is exactly what a read-back check exists to
catch, and it almost got through as ordinary output.

**Root cause.** `_value_of()`'s fallback chain tries, in order:
`get_value()`, `window_text()`, then a joined `.texts()`. Checked live,
against a combobox literally named `VAT` with `With VAT` selected:

| Accessor | Returns | Correct? |
|---|---|---|
| `get_value()` | raises `AttributeError` (pywinauto's `ComboBoxWrapper` never defines it) | - |
| `window_text()` | `"VAT"` - the combo's own accessible **name**, not its selection | No |
| `.texts()` | `["VAT", "VAT", "Close"]` - `"Close"` turned out to be the dropdown toggle button's own accessible name, present identically on every combobox checked (including the two working-by-name ones), never a real option | No |
| `.selected_text()` | `"With VAT"` | **Yes** |

Every fallback in the existing chain returned something plausible-shaped and
wrong. `window_text()` in particular is the dangerous one: it's truthy, so
the chain stops there and never even reaches `.texts()` - meaning *every*
named combobox in the app was silently misread as its own label.

**Fix.** `_value_of()` now tries `.selected_text()` first when the control
exposes it, before falling through to the old chain (kept as a fallback for
controls that aren't comboboxes). `combo_options()` had the same
misunderstanding in a different shape - it returned `.texts()` believing
that was the list of selectable options, when a collapsed combo's `.texts()`
never enumerates its items on this app at all. Rewrote it to actually
expand the dropdown, read the real `ListItem` children, and collapse it
again.

**Lesson.** A read-back check only catches what it can tell is wrong. Three
independently-plausible wrong answers passing through the same code path is
a sign the abstraction ("just read the control's text") was wrong for this
widget type, not that any one accessor had a simple bug. Added
`tests/test_grounding.py::test_value_of_prefers_selected_text_over_the_misleading_fallbacks`,
which fakes exactly this combobox shape (`window_text` returns the name,
`.texts()` returns the name-name-"Close" triple, `.selected_text()` returns
the truth) so a regression here fails without a running Fakturama.

---

## `name_re` locators failed with a misleading 10-second timeout - the real cause was instant

**Symptom.** The running total field's locator uses `name_re` because its
accessible name changes with the price-mode selector (`"Total Net"` /
`"Total Gross"` - see `design.md`). It failed every time with
`ControlNotFound: could not ground running net/gross total field within
10s`, even though a plain script enumerating every `Edit` by hand found the
field immediately, under the exact same name, in the exact same container.
Two other totals locators using a literal `name=` (not `name_re`) worked
perfectly in the same run - which pointed straight at `name_re` itself, not
at the field's existence or at timing.

**Root cause.** `Locator._match()` passed `name_re` straight through to
pywinauto's `descendants(title_re=...)`. On this pywinauto version and
backend, that call raises `TypeError: build_condition() got an unexpected
keyword argument 'title_re'` - immediately, on the very first call, every
time. `_match()`'s exception handling was `except Exception: return []`,
written to treat a tree that mutates mid-walk (a dialog still opening) as
"not found yet" and let the polling loop retry. That same broad `except`
caught the `TypeError` too, indistinguishable from a genuinely-empty result,
and the 10-second poll dutifully retried an operation that could never
succeed - turning an instant, deterministic bug into a slow, misleading one.

**Fix.** `name` and `name_re` are no longer pushed down as native pywinauto
kwargs at all - only `control_type`/`auto_id`/`class_name` are (the ones
confirmed to work). Both `name` and `name_re` are now matched in Python
against that fetched set, so the two use identical, version-independent
matching logic instead of depending on pywinauto's internal condition
builder to support a regex kwarg it doesn't. Separately, `_match()` now
re-raises `TypeError` instead of swallowing it - a malformed query is a bug
in the locator, not "not found yet", and should surface on the first poll,
not after the full timeout.

**Lesson.** A single broad `except Exception` doing double duty - "this is a
normal not-yet-visible state" and "this call is fundamentally broken" - will
eventually hide the second kind behind the first, and it will look exactly
like a real-app timing problem when it does. `tests/test_locator.py` fakes
the confirmed pywinauto behaviour (raises `TypeError` on `title_re`, works
on `title`/`control_type`) directly, so this doesn't need a live app to
catch a regression, plus a test confirming a genuinely transient error
(a different exception type) still degrades to "keep polling" as intended.

---

## `automation_id` hit the same `TypeError` class as `name_re` - on a different kwarg

**Symptom.** After the `name_re` fix above, a locator that grounded by
`auto_id` alone (no `name`) started raising `TypeError` from inside
`descendants()` on certain containers, in exactly the same shape as the
`title_re` failure - a native kwarg pywinauto's condition builder doesn't
support for that call.

**Root cause.** The previous fix treated `title_re` as the one unsupported
kwarg and kept pushing `auto_id`/`class_name` down natively because they'd
"been confirmed to work" - but that confirmation was only against the
containers tested at the time. Against a different container type,
`auto_id` hit the identical unsupported-kwarg `TypeError`. Same bug class,
recurring because the fix was scoped to the specific kwarg that failed
first rather than to the pattern (native kwarg support on this backend is
inconsistent across container types, full stop).

**Fix.** Extended the Python-side filtering already built for `name`/
`name_re` to `automation_id` and `class_name` as well. `_criteria()` now
only ever pushes `control_type` down to the native call; every other
predicate (`name`, `name_re`, `automation_id`, `class_name`) is matched in
Python against the fetched set via `_name_of()`, `_automation_id_of()`,
`_class_name_of()`. `_match()`'s `TypeError` re-raise from the earlier fix
already covered this once the native call was reduced to a single,
reliable kwarg.

**Lesson.** When a fix narrows scope to "kwarg X is unsupported," check
whether the actual claim is broader ("only `control_type` is reliably
supported natively on this backend") before the next kwarg in the same
family reproduces the identical failure a second time.

---

## `session.dialog()` looked in one place; some dialogs live in another

**Symptom.** Waiting for the "Select the address" dialog via
`session.dialog()` timed out even though the dialog was visibly open on
screen (confirmed by screenshot and by the user directly: "the address
window popup was open").

**Root cause.** `dialog()` was written assuming every Fakturama dialog is a
Desktop-level top-level window, matched via `Desktop(backend="uia").window(...)`.
That holds for most dialogs, but not all: "Select the address" turned out
to be a `Window`-typed descendant of the main shell window itself, not a
sibling top-level window - so a Desktop-only search never sees it,
regardless of timeout length. A separate, unrelated case (a role-assignment
popup, see below) isn't even a `Window` at all, which ruled out just
widening the Desktop query further.

**Fix.** `dialog()`/`dialog_closed()` now check both mechanisms on every
poll: Desktop top-level windows, and `main_window.descendants(control_type="Window")`,
matched against the same compiled name regex either way. Whichever
mechanism has the dialog wins; neither is assumed authoritative.

**Lesson.** "Is this a dialog" doesn't have one structural answer in this
app - some are real top-level windows, some are embedded `Window`
descendants of the shell, and (see the role-assignment case below) some
aren't windows at all. A single lookup strategy silently only covers a
subset, and the failure looks identical to "the dialog isn't open yet" from
the caller's side.

---

## `Save` button lookup scoped to the editor's own content pane - which never contains it

**Symptom.** Caught before it shipped, while writing `ContactEditor.save()`:
the Save button search was scoped to `self.root` (the editor's own content
pane), the same pattern already used successfully for every other locator
in that class.

**Root cause.** Save is a shared ribbon-toolbar button that lives outside
any individual editor's content pane - it's one button serving whichever
tab is currently active, not a per-editor control. Every other locator in
these editor classes is correctly scoped to `self.root` because those
fields genuinely live inside the editor's own pane; Save is the one
control that doesn't follow that pattern, and copying the pattern
uncritically would have grounded a search in a subtree that can never
contain the answer.

**Fix.** `save()` in `OrderEditor`, `ContactEditor`, and `PaymentTermEditor`
all search `self.session.main_window` instead of `self.root`.

**Lesson.** "Scope every lookup to the editor's own root" is the right
default and was right for every other field - but a default applied without
checking against the one exception (a genuinely shared, cross-editor
control) fails silently in a way that only shows up when the button is
actually clicked, not when the locator is defined. Worth checking new
locators against "does this control conceptually belong to more than one
editor" before assuming the usual scope applies.

---

## Inactive editor tabs tear down their entire content from the UIA tree - not just hide it

**Symptom.** `Locator(control_type="Pane", name="New Debtor").find(window, timeout=10.0)`
failed with a clean timeout even though the "New Debtor" tab had definitely
been opened moments earlier and never closed - it was simply no longer the
active tab, because a "New Term of Payment" tab had been opened on top of
it in between.

**Root cause.** Consistent with the `ExpandableComposite` collapse behaviour
already logged above, this SWT-based UI doesn't just visually hide an
inactive tab's content - it removes the entire content subtree from the
accessibility tree while a different tab has focus. A `Pane` locator scoped
correctly, with a correct name, still finds nothing if its tab isn't the
one currently on top, because from UIA's perspective that content
temporarily doesn't exist. This applies to both top-level editor tabs and
nested sub-tabs (e.g. "Main address" vs "Miscellaneous" within one editor).

**Fix.** Added `FakturamaSession.activate_tab(content_pane_name)`: finds
the `TabItem` header by name (tolerating a leading `*` for unsaved-changes
tabs), clicks it to bring it to the front, then locates and returns the now
newly-materialized `Pane` content. Every workflow step that returns to a
previously-opened tab after opening or working in another one goes through
this method rather than assuming the tab's content is still reachable.

**Lesson.** Treat "the tab is open" and "the tab's content is in the tree"
as two different facts on this app - the first does not imply the second.
Any multi-tab workflow (order editor + debtor editor + payment-term editor
all open at once, as this automation's real flow requires) has to
re-activate a tab before touching it, every time control has passed through
another tab in between.

---

## A saved Debtor's Company field read back as the literal string `"Company"` - its own placeholder text, not real data

**Symptom.** After saving a freshly-filled "New Debtor" editor (tab renamed
itself to "Marta Klein" post-save, confirming the save itself succeeded)
and reopening it via the Debtors list, the Company column showed empty in
the list, and `editor.read_text(COMPANY)` in the reopened editor instance
returned `'Company'` - not `''`, and not the expected `"Northstar Office
GmbH"`. A literal-empty-string result would have been the unsurprising
"field lost its value" case; getting back a string that exactly matches the
field's own label was the confusing part; the two were easy to conflate at
a glance.

**Root cause.** `'Company'` is this Edit field's own hint/placeholder text,
surfaced through the same accessible-text properties `_value_of()` reads
for real content - there is nothing in the accessor chain that
distinguishes "genuine content that happens to equal the placeholder" from
"showing the placeholder because the field is genuinely empty." In this
case the field actually was empty: the value had not survived whatever
happened between the first save and the reopen (most likely interacting
with the same save-then-reopen sequence used as the workaround for the
stale-payment-combo issue logged elsewhere in this doc - not confirmed
further since re-entering the value and re-saving resolved it and the
sequence wasn't worth reproducing purely to pin down the exact mechanism).

**Fix.** Re-set Company via `editor.set_company(...)` on the reopened
instance and re-verified every other field on the same editor (alias,
discount, net-or-gross, both address tabs, payment options) before saving
again - all of those had survived intact, confirming this was isolated to
the one field rather than a broader reopen-loses-everything problem. Saved
again and reread to confirm `'Northstar Office GmbH'` came back correctly.

**Lesson.** A read-back check that returns a plausible-looking string is
not automatically a real value - this is the same shape of near-miss as
the earlier `window_text()`-returns-the-combobox's-own-name bug: a
truthy, real-looking answer that is actually the widget's own scaffolding
text rather than user data. Any Edit-field read-back that happens to equal
that field's own label is worth treating as suspicious rather than
accepting at face value, the same way a combobox read-back equal to its
own accessible name already is.

**Follow-up: the root cause above was incomplete, and the real one is far
more serious.** The entry above blamed "whatever happened between the
first save and the reopen" and left it at that. It kept recurring - the
same field, plausible-looking and correct in every in-editor read-back,
silently missing again later - and a truly independent check (see the next
entry) proved the widget's read-back was never trustworthy evidence of
persistence at all on this app. The real cause, and the fix, are recorded
in full in the entry directly below. Left standing here, corrected rather
than deleted, for the same reason the price-mode/collapse retraction above
was kept: a wrong causal claim should stay visible once it's superseded,
not vanish.

---

## `ValuePattern.SetValue` writes an Edit widget that reads back correctly forever - and never reaches the model

**Symptom.** The Company saga above was not a one-off. After a full,
unrelated Fakturama crash and relaunch (heavy back-to-back UI-automation
stress, unrelated to this bug) forced a genuine reload from disk, the
Debtor's Company field came back with a *previous, already-"fixed"* value
still missing - and a second field, E-Mail, came back reading `'E-Mail'`
(its own placeholder), the exact same failure shape, on a field that had
never been touched by any of the earlier Company debugging. Meanwhile
Street, Telephone, Alias name, and Discount on the same record all came
back correct from that same disk reload. The failure was real, silent, and
inconsistent field-to-field - which is what made it worth chasing to an
actual mechanism instead of patching around one field.

**The decisive check.** Every prior "verification" of Company had only
ever read the value back from the same widget that wrote it - which the
entry above already showed can't be trusted. The `Select the address`
dialog's search box turned out to be a genuinely independent read: it
matched `Klein` correctly but showed a blank Company column for that same
row, at a moment when the open editor's Company widget was displaying
`Northstar Office GmbH`. Two different views of the same entity,
disagreeing, is what turned this from "a field looked wrong once" into "the
write path itself is broken."

**Root cause.** `Page.set_text()` tried `control.set_edit_text(value)` (UIA
`ValuePattern.SetValue`) first, on the reasoning that it is atomic and
avoids focus races - and it succeeds silently on this app's Edit widgets
essentially every time. But `ValuePattern.SetValue` sets the widget's text
through the accessibility bridge directly, bypassing the native keystroke
pipeline (`WM_CHAR`, focus/blur) that SWT's own `ModifyListener` /
`FocusOut`-driven JFace data-binding actually listens on. The widget's own
text buffer changes - `window_text()`, `.texts()`, every read-back this
codebase uses, all agree with it - but the underlying model bound to that
widget never hears about the change, because it was never told to
subscribe to raw automation-bridge writes, only to real input events. The
value is not "lost" so much as it was never delivered past the widget in
the first place, and the app only ever persists what the model holds.
`_type_into` (the fallback for controls where `set_edit_text` raises) *does*
drive real keystrokes and does not have this problem - which is exactly
why some fields on the same record (the ones where `set_edit_text`
happened to fail and fall through) persisted correctly while others,
written the "supported, atomic" way, silently didn't. Two attempted
remediations were tried and rejected before landing on the real fix:
a synthetic `{TAB}` keystroke does force the model sync (confirmed: the
tab title, which is driven by the model, updated immediately) but this
app's SWT Text widget also inserts a literal tab character while
processing it, corrupting the value in the process, regardless of whether
the keystroke is sent with `set_foreground=True` or `False`; a mouse click
on a neighbouring field also forces the sync cleanly with no keystroke
involved, but requires knowing a safe neighbour to click, which isn't
general-purpose.

**Fix.** `Page.set_text()` no longer calls `set_edit_text()`/ValuePattern at
all - every write goes through `_type_into` (click the control, `^a{DEL}`,
then type the real value as keystrokes), unconditionally. That function
was already exactly correct; it just needed to stop being the fallback
path for the one case (`set_edit_text` succeeding) that silently produced
wrong results. `_try_value_pattern` was deleted rather than kept as a
disabled option, since keeping working-but-wrong code around as a fallback
is exactly how this bug got in unnoticed the first time. The Debtor's
Company and E-Mail were both re-set through the corrected path and
re-verified.

**Lesson.** "Atomic and immune to focus races" was true and irrelevant -
the property that mattered was whether the write reaches the *model*, not
whether it reaches the *widget* cleanly. On an app whose UI is a thin
projection over a data-bound model (as most non-trivial desktop apps are),
a control's own accessible text is not proof that anything persisted - the
only proof is a read that doesn't go through that same control, which is
exactly what the address-search dialog provided here and what the
in-editor read-back never could. This is the single highest-impact bug
found this session: it silently affected every free-text field written
through `set_text()` up to this point, not just the one that happened to
get caught.

---

## A literal `%` in typed text vanished - `type_keys()` read it as a dangling Alt-modifier

**Symptom.** Creating a VAT rate named `"VAT 19%"` (step: setting up the 19%
rate the two order line items need) failed verification: `wrote 'VAT 19%'
but the field reads 'VAT 19'`. The trailing `%` was gone.

**Root cause.** Fixing the ValuePattern bug above made `_type_into` (real
keystrokes via `type_keys()`) the only write path - which is correct, but
surfaced a second, unrelated issue in the same function: pywinauto's
`type_keys()` uses a SendKeys-style mini-language where `+^%~(){}` are not
literal characters but modifier/grouping syntax (`%` = Alt, `^` = Ctrl,
`+` = Shift, `~` = Enter, `()`/`{}` = grouping). A bare `%` at the end of a
string is read as an Alt-modifier prefix with nothing following it and is
simply consumed. This was invisible until now because nothing typed through
`set_text()` up to this point happened to contain one of these characters -
the Debtor/PaymentTerm fields fixed earlier were all plain words, dates, or
emails.

**Fix.** Added `_escape_special_keys()`, which wraps each of `+^%~(){}` in
its own braces (`%` -> `{%}`) before handing the string to `type_keys()` -
the exact escaping pywinauto's own documentation prescribes for sending
these characters literally. `_type_into` now escapes unconditionally, so
every future `set_text()` call is safe by construction rather than by luck
of which characters the caller happens to pass.

**Lesson.** The ValuePattern fix above replaced a write path that silently
corrupted data (looked right, wasn't) with one that silently corrupts text
containing a specific character class instead (also looked right until this
specific value). Swapping to "the correct API" isn't the same as "the safe
API" - real keystroke simulation still has its own escaping contract, and
it only surfaces for input containing the syntax characters, so a currency
symbol, a percentage, a phone number with `+`, or a name with `(` would all
have hit this silently later if it hadn't been caught here.

---

## Quitting Fakturama silently saved a blank, never-explicitly-saved Order tab

**Symptom.** After the real Order was already saved and the follow-up
Invoice created and saved, one leftover unsaved `*New Order` tab (an
accidental duplicate opened earlier - see the Items-grid section of this
doc) was left open rather than closed, on the judgment that closing it via
Ctrl+W would only risk the "Save Parts" dialog with no clean discard
option. Quitting the whole application via its window-close button
produced a simple "Quit Fakturama - Do you want to exit?" Yes/No prompt -
no resource-save dialog appeared at all - and confirming it. On the next
launch, the Orders list showed a second row, `PO000001`, dated today with
every field empty and a total of `$0.00`, which had never been explicitly
saved.

**Root cause (inferred, not fully confirmed).** Eclipse RCP's workbench
persists its session state on a clean exit, and this build of Fakturama
appears to fold "save the open editor" into that same exit path for at
least one blank, untouched editor - unlike Ctrl+W on a single tab, which
does prompt. Not chased further since it isn't a bug in this project's own
code, only a consequence of leaving an unsaved stray tab open across a
full quit.

**Handling.** Deliberately not auto-deleted. The Orders list is a
UIA-opaque grid (same class of widget as the Items table and the Debtors
list elsewhere in this doc) with no per-row identity to ground a delete
click against - removing the wrong row on an unverified guess is a worse
outcome than leaving one harmless `$0.00` empty order behind. Recorded
here instead so it reads as a known, understood artifact of this run
rather than an unexplained extra record, with the real data (`PO000002`,
`INV000001`) independently confirmed correct in the same relaunch.

**Lesson.** Prefer letting a genuinely blank, disposable duplicate tab sit
open over forcing a close through a save-prompt with no clean discard
option - the cost of the leftover tab (one empty order after a full
restart) was smaller and more predictable than the risk of clicking
through a dialog whose exact button semantics ("Save Parts": OK/Cancel,
no explicit discard) weren't fully characterized under this exact
combination of state.

---

## The very first click against a just-launched Fakturama can silently vanish

**Symptom.** First real end-to-end run of the consolidated `fakturama-auto
run` command (everything before this had been driven by hand, one page
object at a time, always against an already-open, already-used Fakturama):
against a freshly wiped database, started fresh via `--launch`, it failed on
the very first UI action - `create_payment_method`'s click on "Create a new
term of payment" - with `ControlNotFound: could not ground New Term of
Payment editor content within 15s`. Re-running the exact same call by hand
moments later, against the same now-slightly-warmed-up instance, worked
immediately. A separate call in the same run, `window.maximize()`, had the
same shape of failure: no exception, but `window.is_maximized()` came back
`False` afterward and stayed `False`.

**Root cause (inferred).** Every prior test this session - dozens of
successful creates, saves, and dialog interactions - ran against a Fakturama
that had already been open and interacted with for a while. This was the
first time any code in this project acted on a Fakturama within moments of
its own `Application.start()` returning. `FakturamaSession.launch()`'s
readiness check (`main_window.wait("ready")`) confirms the shell window
exists and responds, but evidently not that the specific editor/list
machinery a given action needs has finished whatever Eclipse does lazily on
first use (class loading, plugin activation) - a button can be found,
report itself enabled and visible, and still not respond to a click yet.
Both failures - a button click producing no tab, and a window-state call
producing no state change - fit the same explanation: the very first
interaction of a session can be silently absorbed by the app before it has
truly finished settling, with no error surfaced anywhere to catch.

**Fix.** Two changes, both "verify and retry the whole action," not just
add a longer wait:

- `app/base.py` gained `click_and_await_pane()`, used by every "open a new
  X editor" factory (`open_new_order`, `open_new_debtor`,
  `open_new_payment_term`, `open_terms_of_payment_list`,
  `open_new_vat_rate`, `open_vats_list`, `open_new_product`). It retries the
  *entire* click-and-wait cycle - re-finding the trigger control fresh each
  attempt, not reusing the first lookup - because retrying only the click
  keeps pressing the same reference that already proved unreliable once;
  a fresh attempt after Eclipse has had another second to settle is what
  actually recovers.
- `flow/order_flow.py`'s window-maximise at the start of the run now checks
  `is_maximized()` and retries up to five times with a short delay, instead
  of calling `maximize()` once and trusting it.

**Lesson.** A page object verified correct by hand, one call at a time
against a warm app, is not the same claim as "correct in a cold-started,
fully automated run" - the entire session's manual testing had never
actually exercised the first few seconds after launch, because a human
naturally leaves a gap between starting an app and clicking something in
it. The fix generalizes past this one button: any "click and wait for a
result" action in this codebase should assume its first attempt might be
silently absorbed, especially when nothing else is known to have already
"warmed up" that part of the app.

---

## Two percentage fields silently mismatched their own read-back - "0" vs "0%"

**Symptom.** Later in the same first consolidated run, `zero_out_terms()`
failed: `AutomationError: setting Cash discount field failed after 3
attempts: Cash discount field: wrote '0' but the field reads '0%'`.

**Root cause.** Same underlying story as the Order date field's
reformat-on-input behaviour, discovered by the same mechanism: fixing
`set_text()` to always drive real keystrokes (rather than the broken
ValuePattern path) made previously-silent reformatting visible for the
first time. Both `PaymentTermEditor`'s Cash discount and `ContactEditor`'s
Discount are percentage fields that always display with a trailing '%';
typing a bare '0' leaves the field showing '0%', which a literal-string
comparison correctly flags as a mismatch. Neither call had ever been
exercised end-to-end this session through the corrected write path before
now - `set_discount_zero()`'s only earlier live use predates the
ValuePattern fix, when the same widget's reformatting was itself being
silently skipped, so the old "0" vs "0" comparison happened to pass for the
wrong reason.

**Fix.** Both call sites now type the literal `"0%"` instead of `"0"`,
which round-trips exactly. (Unlike the date field, no custom `verify`
callable was needed - the fix is simply typing what the field will
actually display, since escaping `%` already works correctly.)

**Lesson.** The ValuePattern fix didn't just correct a persistence bug - it
retroactively invalidated every earlier "this passed verification" result
for any field with reformat-on-input behaviour, because verification had
never actually been exercised against the field's *real* behaviour until
real keystrokes started triggering it. Worth treating any percentage/date/
currency field's existing test coverage as unconfirmed until re-run through
the corrected path, not just the specific field that happened to fail here.

---

## A wiped workspace has no default Shipping method - and Fakturama hard-stops on it

**Symptom.** The first consolidated `fakturama-auto run` against a freshly
wiped database got through creating the payment method, VAT rate, products,
and Debtor, then hung inside `open_new_order()`'s retry loop. The actual
cause only surfaced by reproducing it directly and dumping the tree: a
modal `Error` dialog, title "Error", message *"No default value found for
Shippings. Please set one from list!"*, with a single OK button - not a
warning that can be dismissed and worked around, since the New Order editor
never opens behind it at all.

**Root cause.** A normal, pre-existing Fakturama workspace - which is what
every manual test this session ran against, and the only kind of workspace
this bug class could have escaped detection on - ships a standard "Free of
shipping costs" Shipping record out of the box, visible as the Order
editor's default Shipping dropdown value in every screenshot taken before
this session's first from-scratch wipe. `rm -rf FData` removes that seeded
default along with everything else, and unlike VATs (which keeps its
built-in 0% "Tax-free" rate even after a wipe - confirmed live, it was
present in every post-wipe VATs list this session), Shipping has no
built-in fallback at all: zero Shipping records is a state opening a New
Order cannot tolerate.

**Fix.** Added `app/shipping_editor.py` (`ShippingEditor`,
`create_default_shipping_method()`), mirroring the VAT/Product/Payment-term
editors - create a zero-value shipping method, click "Set as standard",
save. Wired into `flow/order_flow.py` as the very first piece of master
data created, before the Debtor or Order, since it has to exist before
`open_new_order()` is ever called for any reason.

**Lesson.** Wiping the workspace to get a clean, reproducible starting
point (this session's own explicit request) is not the same guarantee as
"a normal Fakturama install's starting point" - some master data is
seeded by the installer/first-run process, not created lazily by the app
itself, and a fully-empty database can be in a state the app's own code
doesn't handle gracefully (a hard Error dialog, not a warning). Every
manual test this entire session ran against a workspace that had *already*
been through this exact first-run seeding at some earlier point, which is
exactly why this never surfaced until the first fully-from-scratch,
fully-automated run.

---

## The address selector's search doesn't match the Alias field

**Symptom.** `build_order`'s address-selection step chose the customer's
alias as its search text (`"NORTHSTAR-BERLIN"`) and the "Select the
address" dialog never produced a match - the search box accepted the text,
but no row appeared, so the fallback double-click landed on empty grid
space and the dialog never closed.

**Root cause.** Confirmed by hand: searching the literal alias
`"NORTHSTAR-BERLIN"` matches nothing, while searching `"Northstar"` (the
company name) finds the row immediately. The dialog's search evidently
matches against its visible columns (No. / First Name / Name / Company /
ZIP / City) and not the Alias field, even though Alias is a real, unique,
intentionally-searchable-sounding field elsewhere in the app (the
Miscellaneous tab, the Debtors list). Nothing about the dialog's own UI
suggests this - the search box carries no placeholder or column indicator
- so the only way to know was to try it.

**Fix.** `build_order`'s search-text priority is now company name first,
then last name, with alias only as a last resort - i.e. the fields
actually confirmed to be searched, ahead of the one confirmed not to be.

**Lesson.** "This field is called Alias and behaves like an identifier
elsewhere in the app" is not evidence it participates in a specific
search box - each search surface in this app has its own, undocumented set
of matched columns, and the only reliable way to know which is to test the
literal value against it, not to reason from the field's name or its role
elsewhere.

---

## `run_order_flow` read the Order's totals after navigating away from it

**Symptom.** `ControlNotFound: could not ground running net/gross total
field within 10s. Container held: <no descendants>` - `order_editor`'s own
content pane apparently held nothing at all.

**Root cause.** A same-session repeat of the tab-teardown behaviour
documented earlier for `activate_tab()`, this time self-inflicted in new
orchestration code rather than a page object: `run_order_flow` called
`order_editor.read_totals()` *after* `invoice_editor.save()`, by which
point the Invoice tab had been the active one for two whole steps
(`create_followup_invoice()` and `mark_paid()`/`save()`). The Order's
content pane, no longer the front tab, had already been torn from the
tree - `order_editor` the Python object was still perfectly valid, but the
live control its locators resolve against was gone.

**Fix.** Reordered `run_order_flow` to read the Order's totals immediately
after saving the Order, while it is still the active tab, *before* calling
`create_followup_invoice()` - which is also the more natural place for it
regardless, since those are the Order's own totals, not the Invoice's.

**Lesson.** This project's own documented rule ("re-activate a tab before
touching it, every time control has passed through another tab in
between") applies just as much to a `Page` object already sitting in a
local variable as to a fresh lookup by name - holding a reference to an
editor object across a step that switches tabs is not the same as that
editor's content still being reachable. Worth double-checking, in any new
orchestration code, whether a later step revisits an earlier editor after
something else has taken focus in between.

---

*(more entries added as they come up)*
