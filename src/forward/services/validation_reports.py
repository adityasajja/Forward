"""Build API payloads from stored research reports."""

from __future__ import annotations

from typing import Any

from forward.core.research_ports import (
    ValidationReportPort,
)

_ALPHA_HUNT_REPORTS = (
    ("nifty50", "alpha_hunt_nifty50"),
    ("midcap150", "alpha_hunt_midcap150"),
    ("smallcap250", "alpha_hunt_smallcap250"),
)


class ValidationReportService:
    """Load, combine, and summarize stored research reports."""

    def __init__(self, reports: ValidationReportPort) -> None:
        """Create the report service.

        Args:
            reports: Port that reads report files.
        """
        self._reports = reports

    def _read_json(self, report_name: str) -> tuple[Any, Exception | None]:
        """Read one report and return its known storage or decode error.

        Args:
            report_name: Stable report name from the storage port.

        Returns:
            The decoded report and no error, or ``None`` and the read error.
        """
        try:
            return self._reports.read_json(report_name), None
        except (OSError, ValueError) as error:
            return None, error

    def paper_validation(self) -> dict[str, Any]:
        """Return the latest validation run for paper strategies.

        Returns:
            The stored validation report, or its availability details.
        """
        report_name = "paper_validation"
        if not self._reports.exists(report_name):
            return {
                "available": False,
                "hint": "run: .venv/Scripts/python.exe scripts/validate_paper_strategies.py",
            }
        payload, error = self._read_json(report_name)
        if error is not None:
            return {"available": False, "error": str(error)}

        payload["available"] = True
        return payload

    def episodic_pivot(self) -> dict[str, Any]:
        """Return the episodic-pivot validation and premise reports.

        Returns:
            The combined report or its availability details.
        """
        validation_name = "episodic_validation"
        if not self._reports.exists(validation_name):
            return {
                "available": False,
                "hint": (
                    "run: .venv/Scripts/python.exe scripts/research_episodic_pivot.py "
                    "&& .venv/Scripts/python.exe scripts/validate_episodic_pivot.py"
                ),
            }
        payload, error = self._read_json(validation_name)
        if error is not None:
            return {"available": False, "error": str(error)}

        premise_name = "episodic_premise"
        if self._reports.exists(premise_name):
            premise, premise_error = self._read_json(premise_name)
            if premise_error is None:
                payload["premise"] = premise

        payload["available"] = True
        return payload

    def alpha_hunt(self) -> dict[str, Any]:
        """Combine alpha-hunt reports that exist for each universe.

        Returns:
            Merged research results and any report read errors.
        """
        present = [
            (universe, report_name)
            for universe, report_name in _ALPHA_HUNT_REPORTS
            if self._reports.exists(report_name)
        ]
        if not present:
            return {
                "available": False,
                "hint": "run: .venv/Scripts/python.exe scripts/research_alpha_hunt.py",
            }

        merged: dict[str, Any] = {
            "available": True,
            "universes": {},
            "pre_registered": {},
            "config": {},
            "generated_at": None,
        }
        for _universe, report_name in present:
            payload, error = self._read_json(report_name)
            if error is not None:
                merged.setdefault("errors", {})[self._reports.path(report_name)] = str(error)
                continue
            merged["universes"].update(payload.get("universes", {}))
            merged["pre_registered"].update(payload.get("pre_registered", {}))
            merged["config"].update(payload.get("config", {}))
            generated = payload.get("generated_at")
            if generated and (merged["generated_at"] is None or generated > merged["generated_at"]):
                merged["generated_at"] = generated
        return merged

    def evidence(self) -> dict[str, Any]:
        """Return evidence findings with their credibility count.

        Returns:
            Evidence findings, their credibility count, and their total count.
        """
        report_name = "evidence"
        if not self._reports.exists(report_name):
            return {
                "available": False,
                "hint": "run: .venv/Scripts/python.exe scripts/research_honest_verdicts.py",
            }
        payload, error = self._read_json(report_name)
        if error is not None:
            return {"available": False, "error": str(error)}

        findings = payload.get("findings", [])
        payload["available"] = True
        payload["credible_count"] = sum(
            1 for finding in findings if finding.get("verdict", {}).get("credible")
        )
        payload["total"] = len(findings)
        return payload
