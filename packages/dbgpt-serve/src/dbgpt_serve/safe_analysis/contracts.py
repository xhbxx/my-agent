"""Small shared contracts, independent of HTTP and model providers."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class AnalysisError(ValueError):
    """A deliberately safe message that may be sent to the caller."""


@dataclass(frozen=True)
class Principal:
    name: str
    sources: tuple


@dataclass(frozen=True)
class SourcePolicy:
    name: str
    url: str = field(repr=False)
    dialect: str = "sqlite"
    schema: str = "main"
    tables: Dict[str, Dict[str, str]] = field(default_factory=dict)
    metrics: List[str] = field(default_factory=list)
    knowledge_files: tuple = ()
    blocked_columns: tuple = ()
    masked_labels: tuple = ()
    max_rows: int = 200
    timeout_ms: int = 5000
    pool_size: int = 2
    scan_rows: int = 10000
    max_cost: float = 10000
    confirm_scans: bool = True
    vector: bool = False
    version: str = ""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    digest: str
    tables: tuple


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[List[Any]]
    truncated: bool
    elapsed_ms: float


@dataclass
class CostAssessment:
    reasons: List[str]
    estimated_cost: Optional[float] = None
    estimated_rows: Optional[int] = None

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.reasons)
