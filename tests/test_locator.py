"""Regression tests for Locator._match against a real pywinauto quirk.

``descendants(title_re=...)`` raises ``TypeError`` on this pywinauto/backend
combination rather than supporting or ignoring the kwarg - discovered live
when a locator using ``name_re`` (grounding the running total field, whose
accessible name flips between "Total Net" and "Total Gross") failed with a
10-second ``ControlNotFound`` timeout that turned out to have nothing to do
with timing: the very first native query raised immediately, and the broad
``except Exception: return []`` in ``_match`` was swallowing that into "not
found yet" and polling out the full timeout instead of surfacing it.

Locator._match() now (a) never pushes ``name``/``name_re`` down as native
pywinauto kwargs - it filters both in Python against a fetch that only pushes
what is actually supported (``control_type``, ``auto_id``, ``class_name``) -
and (b) re-raises TypeError instead of treating a malformed query the same
as a legitimately empty result.
"""

from __future__ import annotations

import pytest

from fakturama_auto.uia.locator import Locator


class FakeInfo:
    def __init__(
        self,
        control_type: str,
        name: str | None = None,
        automation_id: str | None = None,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.class_name = None


class FakeElement:
    def __init__(
        self,
        control_type: str,
        name: str | None = None,
        automation_id: str | None = None,
    ) -> None:
        self.element_info = FakeInfo(control_type, name, automation_id)


class FakeContainer:
    """Mimics pywinauto: supports only control_type on descendants(); raises
    TypeError on any of title/title_re/auto_id/class_name - matching the
    confirmed live behaviour that only control_type is ever pushed down."""

    def __init__(self, elements: list[FakeElement]) -> None:
        self._elements = elements

    def descendants(self, **kwargs):
        unsupported = set(kwargs) - {"control_type"}
        if unsupported:
            raise TypeError(f"descendants() got an unexpected keyword argument {next(iter(unsupported))!r}")
        results = self._elements
        if "control_type" in kwargs:
            results = [e for e in results if e.element_info.control_type == kwargs["control_type"]]
        return results

    def children(self, **kwargs):
        return self.descendants(**kwargs)


def test_name_re_matches_without_relying_on_native_title_re():
    total_net = FakeElement("Edit", name="Total Net")
    other_edit = FakeElement("Edit", name="Discount")
    container = FakeContainer([total_net, other_edit])

    locator = Locator(control_type="Edit", name_re=r"(?i)^total (net|gross)$")

    found = locator.find_all(container)

    assert found == [total_net]


def test_a_malformed_native_query_is_not_treated_as_not_found():
    """_match distinguishes "this query is broken" from "legitimately not
    found yet". name_re itself can no longer trigger this - it's never
    pushed down natively any more - but the same TypeError-from-pywinauto
    shape can still occur for any kwarg that IS still pushed down natively
    (control_type, auto_id, class_name) on some other version/backend, and
    it must propagate rather than being swallowed into an empty result that
    then times out silently 10 seconds later."""

    class BrokenContainer:
        def descendants(self, **kwargs):
            raise TypeError("descendants() got an unexpected keyword argument 'auto_id'")

    locator = Locator(control_type="Edit", automation_id="123")

    with pytest.raises(TypeError):
        locator._match(BrokenContainer())


def test_plain_name_matching_still_works_through_the_native_path():
    cust_ref = FakeElement("Edit", name="Cust.Ref.")
    container = FakeContainer([cust_ref, FakeElement("Edit", name="Consultant")])

    found = Locator(control_type="Edit", name="Cust.Ref.").find_all(container)

    assert found == [cust_ref]


def test_automation_id_matches_through_the_python_side_path_not_native():
    """Confirmed live: descendants(auto_id=...) also raises TypeError on
    this pywinauto/backend combination, the same shape as the title_re bug
    above. automation_id must never be pushed down natively either."""
    target = FakeElement("Edit", automation_id="395794")
    other = FakeElement("Edit", automation_id="723234")
    container = FakeContainer([target, other])

    found = Locator(control_type="Edit", automation_id="395794").find_all(container)

    assert found == [target]


def test_a_transient_lookup_error_is_still_treated_as_not_found():
    """A tree mutating mid-walk (a dialog opening) surfaces as some other
    exception type - that case must keep degrading to "not yet", not raise."""

    class FlakyContainer:
        def descendants(self, **kwargs):
            raise RuntimeError("COM error: element no longer exists")

    assert Locator(control_type="Edit", name="Anything").find_all(FlakyContainer()) == []
