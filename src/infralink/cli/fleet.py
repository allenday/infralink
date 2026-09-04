"""Private compatibility import for the generated fleet command.

The public entrypoint is ``infralink fleet validate`` from the single
``operator_surface`` projection.  This module remains import-safe for callers
that use the historical Python module path; it does not define a second tree.
"""

from __future__ import annotations

import click

from infralink.operator_surface import operator_click_adapter

_root = operator_click_adapter().command()
fleet = _root.get_command(click.Context(_root), "fleet")
assert isinstance(fleet, click.Group)
