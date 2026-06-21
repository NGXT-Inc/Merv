"""Port for sandbox procurement admission checks."""

from __future__ import annotations

from typing import Protocol

from ..domain.quota_contract import AdmissionRequest


class QuotaAdmission(Protocol):
    """Admits or denies fresh sandbox procurement."""

    def check_admission(self, *, request: AdmissionRequest) -> None:
        ...
