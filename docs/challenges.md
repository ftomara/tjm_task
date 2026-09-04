# Challenges encountered during implementation
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

## A Ctrl+S on the still-open Debtor refreshes its stale Payment combo - found by the user, not guessed at

**Symptom.** The previous fix for the missing-payment-term case (abandon
the in-progress Debtor editor, create the term, fill a second fresh
instance from scratch) worked but was wasteful - every field typed twice
whenever the term happened to be missing.

**What actually works, confirmed live by hand.** After creating the
payment term standalone and returning to the *same still-open* Debtor tab
(no reopening, no second editor), pressing Ctrl+S on it does two things at
once: it refreshes that tab's Payment combo to include the just-created
term, and it saves cleanly - no error, even though Payment is still unset
at the moment the keystroke is sent. Verified independently after a full
app relaunch: Company and every other field came back correctly from
disk, confirming this Ctrl+S save is a real, complete persist, not a
partial or cosmetic one.

**Why this doesn't contradict the earlier "saving with no payment method
fails outright" finding.** That failure (Fakturama's own Error Log:
"Document number invalid" / "Failed to persist contents of part") happened
when *no payment term existed anywhere in the workspace yet* - a
first-save attempt made before the term was ever created. Here, the term
already exists in the system by the time Ctrl+S is pressed; this specific
record just hasn't referenced one yet. Those are different states that
happen to look identical from this function's own point of view (a Debtor
with Payment blank, about to be saved) - what actually matters is whether
a valid payment term exists *anywhere*, not whether this record points at
one.

**Fix.** `create_debtor()` now: fills the Debtor, and if the target
payment method isn't yet an option, creates the term and sends a bare
Ctrl+S to the same still-open editor - no abandoning, no second fill.
`_fill_new_debtor()`'s dual-instance path is gone; there is only ever one
Debtor editor per customer now.

**Lesson.** The first fix that avoids a known bug (staleness) isn't
necessarily the cheapest one - "never let the combo render before the
data exists" and "abandon and start over" were the two options considered
at the time, and a third, better one (an explicit save/refresh action on
the same instance) was sitting in the app the whole time, just untested.
Worth trying the "is there an in-app refresh for this" question directly -
by hand, live - before settling for the workaround that only needed
knowledge already in hand to build.

---

## Clicking Order while Shipping is missing opens a real tab; the Error dialog just hides it - two fix attempts before landing on this

**Symptom.** A full run reported success end to end - but the user,
watching it live, spotted a second, unsaved `*New Order` tab sitting next
to the real, saved `PO000001`. The *saved* order had also somehow ended up
in "Gross" price mode despite the flow explicitly calling
`set_price_mode("Net")`, which had genuinely succeeded (verified) against
whichever Order tab was active at the time.

**Root cause.** Clicking Order while no default Shipping exists does not
fail to open the New Order tab - it opens it, and shows the modal Error
dialog on top. Confirmed live, and it is the same rule already documented
elsewhere in this project for inactive tabs and `ExpandableComposite`
sections: content sitting behind an open modal dialog is torn from the UIA
tree while that dialog is up, not merely hidden. So `open_new_order()`'s
`Locator(control_type="Pane", name="New Order").find(...)` genuinely finds
nothing for as long as the dialog is open - not because the tab failed to
open, but because its content isn't in the tree yet from this call's point
of view.

**Two wrong fixes before this one.** First attempt: wrap `open_new_order()`
in a try/except that dismisses the dialog and retries on failure - but
`open_new_order()` already retries its own click-and-wait cycle three
times internally *before* that outer exception is ever raised, so each
internal retry clicked Order again while the dialog was still up, each
time opening a *second, separate, genuine* Order tab (not a hidden one -
an actually new one). Second attempt, reasoning "don't retry a click while
the dialog might be open, but do click once more right after dismissing
it" - still wrong, for the same underlying misunderstanding: since the
first click's tab already exists and just needs the dialog gone to
reappear, that deliberate second click *also* opened a genuine second
Order every single time this path fired, which is not a rare edge case on
a wiped workspace - it is the common case. Later, `_resolve_debtor()`'s
`session.activate_tab("New Order")` - a name-based lookup that can't tell
two same-named tabs apart - could then resolve to whichever duplicate,
not necessarily the one carrying `set_price_mode("Net")` and everything
else already applied to it; the rest of the flow continued on that wrong
tab, still reconciling correctly (its items were entered correctly) while
silently defaulting to Fakturama's own "Gross" mode.

**Fix.** `open_new_order()` now clicks Order exactly once on the common
path: if the Shipping error appears, it is only *dismissed* - no default
Shipping method is created here at all - and the *same* click's tab is
simply waited for again, no second click. Creating the default Shipping
method moved out of this function entirely; the Order's own Shipping field
is simply left blank until the flow actually reaches it (see the next
entry), since nothing about opening the Order itself needs it. A second
click only happens via the function's own light retry wrapper, as a last
resort if the Pane still isn't found for an unrelated reason.

---