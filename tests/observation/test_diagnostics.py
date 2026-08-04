import pytest

from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation


def _diagnostic(code: str, *, severity: str = "error", path: str = "z.yml") -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=code,
        location=SourceLocation(path=path, pointer="/"),
        next_actions=("Repair the document.",),
    )


def test_diagnostic_set_sorts_and_bounds_independent_findings() -> None:
    findings = [
        _diagnostic("z", severity="warning"),
        _diagnostic("b", path="b.yml"),
        _diagnostic("a", path="a.yml"),
    ]

    result = DiagnosticSet.from_diagnostics(findings, limit=2)

    assert [finding.code for finding in result] == ["a", "b"]
    assert result.total_count == 3
    assert result.truncated is True


def test_diagnostic_set_uses_path_pointer_and_identity_as_tie_breakers() -> None:
    findings = [
        Diagnostic("duplicate", "error", "duplicate", SourceLocation("b.yml", "/x/1"), "z"),
        Diagnostic("duplicate", "error", "duplicate", SourceLocation("a.yml", "/x/2"), "b"),
        Diagnostic("duplicate", "error", "duplicate", SourceLocation("a.yml", "/x/1"), "a"),
    ]

    result = DiagnosticSet.from_diagnostics(findings, limit=10)

    assert [(d.location.path, d.location.pointer, d.identity) for d in result] == [
        ("a.yml", "/x/1", "a"),
        ("a.yml", "/x/2", "b"),
        ("b.yml", "/x/1", "z"),
    ]


def test_diagnostic_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        _diagnostic("bad", severity="fatal")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"diagnostics": (), "limit": -1, "total_count": 0, "truncated": False},
        {"diagnostics": (), "limit": True, "total_count": 0, "truncated": False},
        {"diagnostics": (), "limit": 1, "total_count": -1, "truncated": False},
        {
            "diagnostics": (_diagnostic("a"),),
            "limit": 1,
            "total_count": 0,
            "truncated": False,
        },
        {"diagnostics": (), "limit": 1, "total_count": 2, "truncated": False},
    ],
)
def test_diagnostic_set_rejects_inconsistent_state(kwargs: dict[str, object]) -> None:
    kwargs.setdefault("error_count", max(int(kwargs["total_count"]), 0))
    kwargs.setdefault("warning_count", 0)
    with pytest.raises(ValueError):
        DiagnosticSet(**kwargs)  # type: ignore[arg-type]


def test_diagnostic_sort_has_fully_deterministic_final_tie_breakers() -> None:
    common = SourceLocation("same.yml", "/")
    findings = [
        Diagnostic("same", "error", "z", common, next_actions=("b",)),
        Diagnostic("same", "error", "a", common, next_actions=("z",)),
        Diagnostic("same", "error", "a", common, next_actions=("a",)),
    ]

    result = DiagnosticSet.from_diagnostics(findings, limit=10)

    assert [(item.message, item.next_actions) for item in result] == [
        ("a", ("a",)),
        ("a", ("z",)),
        ("z", ("b",)),
    ]


def test_diagnostic_set_retains_pre_truncation_severity_counts() -> None:
    result = DiagnosticSet.from_diagnostics(
        [_diagnostic("error"), _diagnostic("warning", severity="warning")], limit=0
    )

    assert result.error_count == 1
    assert result.warning_count == 1
    assert result.total_count == 2
