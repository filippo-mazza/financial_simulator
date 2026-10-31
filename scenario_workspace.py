"""Isolated named scenario variants for the optional A/B workspace."""

from copy import deepcopy


BASELINE_ID = "baseline"
ALTERNATIVE_ID = "alternative"


class ScenarioVariant:
    """A named configuration snapshot that never exposes its stored object."""

    __slots__ = ("_config", "id", "name")

    def __init__(self, variant_id, name, config):
        if not isinstance(variant_id, str) or not variant_id.strip():
            raise ValueError("Scenario variant ID is required")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Scenario variant name is required")
        self.id = variant_id.strip()
        self.name = name.strip()
        self._config = deepcopy(config)

    @property
    def config(self):
        return deepcopy(self._config)

    def with_config(self, config):
        return ScenarioVariant(self.id, self.name, config)

    def with_name(self, name):
        return ScenarioVariant(self.id, name, self._config)


class ScenarioWorkspace:
    """Immutable-style collection of isolated variants and one active ID."""

    __slots__ = ("_variants", "active_id")

    def __init__(self, variants, active_id=BASELINE_ID):
        variants = tuple(variants)
        if len(variants) < 2:
            raise ValueError("A scenario workspace requires at least two variants")
        ids = [variant.id for variant in variants]
        names = [variant.name for variant in variants]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario variant IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("Scenario variant names must be unique")
        if active_id not in ids:
            raise ValueError("Active scenario variant does not exist")
        self._variants = variants
        self.active_id = active_id

    @classmethod
    def from_config(cls, config):
        """Create independent A and B snapshots from one configuration."""
        return cls((
            ScenarioVariant(BASELINE_ID, "Baseline A", config),
            ScenarioVariant(ALTERNATIVE_ID, "Alternative B", config),
        ))

    @property
    def variants(self):
        # Variants themselves never expose their internal configuration.
        return self._variants

    def variant(self, variant_id):
        try:
            return next(item for item in self._variants if item.id == variant_id)
        except StopIteration as error:
            raise ValueError(f"Unknown scenario variant '{variant_id}'") from error

    @property
    def active_variant(self):
        return self.variant(self.active_id)

    def config_for(self, variant_id):
        return self.variant(variant_id).config

    def _replace_variant(self, replacement):
        return ScenarioWorkspace(
            tuple(
                replacement if item.id == replacement.id else item
                for item in self._variants
            ),
            self.active_id,
        )

    def save(self, variant_id, config):
        return self._replace_variant(self.variant(variant_id).with_config(config))

    def rename(self, variant_id, name):
        return self._replace_variant(self.variant(variant_id).with_name(name))

    def activate(self, variant_id):
        self.variant(variant_id)
        return ScenarioWorkspace(self._variants, variant_id)

    def reset_from(self, target_id, source_id):
        return self.save(target_id, self.config_for(source_id))
