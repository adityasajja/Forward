"""Application workflows for saved options strategies."""

from __future__ import annotations

from typing import Any

from forward.core.strategy_kinds import OPTIONS_STRATEGY_KIND
from forward.services.strategies import StrategyService
from forward.strategy.definition import validate_definition
from forward.strategy.options import (
    OPTIONS_STRATEGY_ENGINE_KEY,
    OptionsStrategyDefinition,
    options_definition,
)


class InvalidOptionsStrategy(ValueError):
    """An options operation used a strategy that is not an options strategy."""


class InvalidOptionsConfiguration(ValueError):
    """An options definition failed the shared structural validation rules."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        """Store the full validation report for the API response.

        Args:
            message: Short explanation shown to the user.
            report: Shared strategy validation result.
        """
        self.report = report
        super().__init__(message)


class OptionsStrategyService:
    """Create, validate, and list saved options strategy definitions."""

    def __init__(self, strategies: StrategyService) -> None:
        """Create the service with the shared strategy authoring use case.

        Args:
            strategies: Shared owner-aware strategy authoring service.
        """
        self._strategies = strategies

    @staticmethod
    def build_definition(options: dict[str, Any]) -> dict[str, Any]:
        """Wrap the options payload as a reusable strategy definition.

        Args:
            options: Configuration entered in the options strategy editor.

        Returns:
            A strategy definition ready for shared validation.
        """
        definition = {
            "kind": OPTIONS_STRATEGY_KIND,
            "engine_key": OPTIONS_STRATEGY_ENGINE_KEY,
            "options": options,
        }
        return definition

    @staticmethod
    def _validated_definition(options: dict[str, Any]) -> dict[str, Any]:
        """Build an options definition and raise one typed validation error.

        Args:
            options: Configuration entered in the options strategy editor.

        Returns:
            A structurally valid strategy definition.

        Raises:
            InvalidOptionsConfiguration: If shared structural validation fails.
        """
        definition = OptionsStrategyService.build_definition(options)
        report = validate_definition(definition)
        if not report["ok"]:
            raise InvalidOptionsConfiguration("options configuration is invalid", report)
        return definition

    def validate_draft(self, options: dict[str, Any]) -> dict[str, Any]:
        """Validate an unsaved options strategy draft.

        Args:
            options: Configuration entered in the options strategy editor.

        Returns:
            The shared structural validation report.
        """
        return validate_definition(self.build_definition(options))

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return saved options strategies and their immutable versions.

        Args:
            user_id: Account whose strategies are requested.

        Returns:
            Options strategies, each with its version list.
        """
        strategies = [
            row for row in self._strategies.list(user_id) if row["kind"] == OPTIONS_STRATEGY_KIND
        ]
        for row in strategies:
            row["versions"] = self._strategies.versions(user_id, row["strategy_id"])
        return strategies

    def create(
        self,
        user_id: str,
        *,
        name: str,
        description: str | None,
        options: dict[str, Any],
        change_note: str | None,
    ) -> dict[str, Any]:
        """Create an options strategy and its first immutable version.

        Args:
            user_id: Account that owns the new strategy.
            name: Strategy name.
            description: Optional strategy description.
            options: Options configuration to save.
            change_note: Optional note for the first version.

        Returns:
            The strategy row and its first version.

        Raises:
            InvalidOptionsConfiguration: If shared structural validation fails.
        """
        definition = self._validated_definition(options)
        strategy = self._strategies.create(
            user_id,
            name=name,
            description=description,
            kind=OPTIONS_STRATEGY_KIND,
            engine_key=OPTIONS_STRATEGY_ENGINE_KEY,
        )
        version = self._strategies.create_version(
            user_id,
            strategy["strategy_id"],
            definition=definition,
            change_note=change_note,
        )
        return {"strategy": strategy, "version": version}

    def create_version(
        self,
        user_id: str,
        strategy_id: str,
        *,
        options: dict[str, Any],
        change_note: str | None,
    ) -> dict[str, Any]:
        """Append an immutable options configuration to an owned strategy.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy receiving the new version.
            options: Options configuration to save.
            change_note: Optional note for the new version.

        Returns:
            The saved immutable version.

        Raises:
            InvalidOptionsStrategy: If the selected strategy is not an options
                strategy.
        """
        definition = self._validated_definition(options)
        strategy = self._strategies.get(user_id, strategy_id)
        if strategy.get("kind") != OPTIONS_STRATEGY_KIND:
            raise InvalidOptionsStrategy("the selected strategy is not an options strategy")
        return self._strategies.create_version(
            user_id,
            strategy_id,
            definition=definition,
            change_note=change_note,
        )

    def get_version(self, user_id: str, strategy_id: str, version: int) -> dict[str, Any]:
        """Read an owned options strategy version.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy identifier.
            version: Exact immutable version to read.

        Returns:
            The saved strategy version.

        Raises:
            InvalidOptionsStrategy: If the saved strategy is not an options
                strategy.
        """
        strategy = self._strategies.get(user_id, strategy_id)
        saved = self._strategies.version(user_id, strategy_id, version)
        if strategy.get("kind") != OPTIONS_STRATEGY_KIND:
            raise InvalidOptionsStrategy("The selected saved version is not an options strategy.")
        return saved

    def config_for_version(
        self, user_id: str, strategy_id: str, version: int
    ) -> OptionsStrategyDefinition:
        """Read and parse the options config from an exact saved version.

        Args:
            user_id: Account that owns the strategy.
            strategy_id: Strategy identifier.
            version: Exact immutable version to read.

        Returns:
            The validated options strategy configuration.
        """
        saved = self.get_version(user_id, strategy_id, version)
        return options_definition(saved["definition"])
