"""Fakturama image-to-cash automation.

Layers, outermost first:

``flow``     the order-first orchestration described in the brief
``app``      page objects for individual Fakturama screens and dialogs
``uia``      the grounding layer: locators, waits, retries, tree inspection
``extract``  image -> validated :class:`~fakturama_auto.models.OrderDoc`
``models``   the domain schema everything above agrees on
"""

__version__ = "0.1.0"
