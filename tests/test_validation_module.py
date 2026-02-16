import importlib


def test_infralink_validation_module_exists():
    module = importlib.import_module("infralink.validation")
    assert hasattr(module, "ValidationError")
    assert hasattr(module, "ValidationWarning")
    assert hasattr(module, "ValidationResult")
