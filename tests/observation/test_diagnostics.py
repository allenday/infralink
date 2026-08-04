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
