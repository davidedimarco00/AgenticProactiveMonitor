"""Backward-compatible facade for the OpenSearch anomaly infrastructure package."""

from .domain.anomalies import AnomalyObservation
from .infrastructure.opensearch import OpenSearchAnomalyWatcher, anomaly_observation_from_hit

__all__ = ["AnomalyObservation", "OpenSearchAnomalyWatcher", "anomaly_observation_from_hit"]
