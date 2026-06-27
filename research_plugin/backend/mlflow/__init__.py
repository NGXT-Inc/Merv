"""MLflow tracking integration.

This package contains the Research Plugin facade around real MLflow services:
agent-facing tracking context, backend metric snapshots, and the local managed
server wrapper.
"""

from .local_server import LocalMlflowServer
from .tracking import CentralMlflowService, mlflow_experiment_name

__all__ = [
    "CentralMlflowService",
    "LocalMlflowServer",
    "mlflow_experiment_name",
]
