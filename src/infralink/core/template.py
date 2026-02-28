from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

from infralink.core.schema import ServiceTemplateSchema, ServiceTemplateSetSchema


class ServiceTemplate:
    """Reusable service deployment pattern."""

    def __init__(self, id: str, schema: ServiceTemplateSchema) -> None:
        self.id = id
        self.schema = schema

    @property
    def description(self) -> str | None:
        return self.schema.description

    def to_dict(self) -> dict[str, Any]:
        return self.schema.model_dump()


class ServiceTemplateSet:
    """Collection of service templates."""

    def __init__(
        self, templates: list[ServiceTemplate], schema_version: str = "1.0"
    ) -> None:
        self._templates = {t.id: t for t in templates}
        self._schema_version = schema_version

    @classmethod
    def load(cls, path: str | Path) -> ServiceTemplateSet:
        """Load templates from YAML file."""
        path = Path(path)
        if not path.exists():
            return cls([], "1.0")

        with path.open() as f:
            data = yaml.safe_load(f)

        if not data:
            return cls([], "1.0")

        schema = ServiceTemplateSetSchema(**data)
        templates = [
            ServiceTemplate(tid, t_schema)
            for tid, t_schema in schema.templates.items()
        ]
        return cls(templates, schema.schema_version)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceTemplateSet:
        """Create service template set from dictionary."""
        schema = ServiceTemplateSetSchema(**data)
        templates = [
            ServiceTemplate(tid, t_schema)
            for tid, t_schema in schema.templates.items()
        ]
        return cls(templates, schema.schema_version)

    def get_template(self, template_id: str) -> ServiceTemplate | None:
        return self._templates.get(template_id)

    def __iter__(self) -> Iterator[ServiceTemplate]:
        return iter(self._templates.values())

    def __len__(self) -> int:
        return len(self._templates)
