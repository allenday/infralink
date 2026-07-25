from __future__ import annotations

import shlex

from infralink.cli.contracts import Action, Binding


def action(
    rel: str,
    argv: list[str],
    description: str,
    *,
    bindings: dict[str, Binding] | None = None,
) -> Action:
    active_bindings = {
        name: binding.model_copy(deep=True) for name, binding in (bindings or {}).items()
    }
    copied_argv = list(argv)
    return Action(
        rel=rel,
        argv=copied_argv,
        command=shlex.join(copied_argv),
        description=description,
        safe=True,
        templated=bool(active_bindings),
        bindings=active_bindings,
    )
