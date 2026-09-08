"""
Lineage / inventory data model: the source-neutral estate graph.

``EstateGraph`` is the single artifact this package builds toward: a list of
``nodes`` (tables, views, stored procedures, SSIS packages, SSRS reports and
datasets, ...), a list of directed ``edges`` between them, and a list of
``diagnostics`` describing anything that could not be resolved cleanly. It is
plain pydantic, with no dependency on any host application's models.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations (closed sets) + node-kind constants (open set)
# ---------------------------------------------------------------------------


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFERRED = "inferred"


class Provenance(str, Enum):
    PARSER = "parser"          # statically extracted from a source artifact
    REFERENCED = "referenced"  # known only by name (referenced, not defined)
    LLM = "llm"                # inferred by a model
    SIDECAR = "sidecar"        # supplied out-of-band


class EdgeKind(str, Enum):
    DERIVES_FROM = "derives_from"  # data flows upstream to downstream
    CONTAINS = "contains"          # structural containment (report contains dataset)
    REFERENCES = "references"      # non-data reference


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARN = "warn"
    INFO = "info"
    ERROR = "error"


class NodeKind:
    """String constants for ``LineageNode.kind`` (kept open for new sources)."""

    TABLE = "table"
    VIEW = "view"
    STORED_PROCEDURE = "stored_procedure"
    SSIS_PACKAGE = "ssis_package"
    SSIS_TASK = "ssis_task"
    SSIS_COMPONENT = "ssis_component"
    SSRS_REPORT = "ssrs_report"
    SSRS_DATASET = "ssrs_dataset"


# ---------------------------------------------------------------------------
# Layers (lineage flows source to consumption, left to right)
# ---------------------------------------------------------------------------


class LayerDef(BaseModel):
    id: int
    key: str
    label: str


LAYER_SOURCE = 0
LAYER_ETL = 1
LAYER_STAGING = 2
LAYER_DW = 3
LAYER_SEMANTIC = 4
LAYER_REPORT = 5

DEFAULT_LAYERS: List[LayerDef] = [
    LayerDef(id=LAYER_SOURCE, key="source", label="Source"),
    LayerDef(id=LAYER_ETL, key="etl", label="ETL"),
    LayerDef(id=LAYER_STAGING, key="staging", label="Staging"),
    LayerDef(id=LAYER_DW, key="dw", label="DW (Dim / Fact)"),
    LayerDef(id=LAYER_SEMANTIC, key="semantic", label="Semantic Model"),
    LayerDef(id=LAYER_REPORT, key="report", label="Reporting"),
]


# ---------------------------------------------------------------------------
# Graph elements
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    path: Optional[str] = None
    locator: Optional[str] = None


class LineageNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str
    name: str
    qualified_name: str
    layer: int
    # "schema" is reserved on pydantic BaseModel; store as schema_name, emit as "schema".
    schema_name: Optional[str] = Field(default=None, alias="schema", serialization_alias="schema")
    domain: Optional[str] = None
    confidence: Confidence = Confidence.HIGH
    provenance: Provenance = Provenance.PARSER
    orphan: bool = False
    source_ref: SourceRef = Field(default_factory=SourceRef)
    attributes: Dict[str, str] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    upstream_id: str
    downstream_id: str
    kind: EdgeKind = EdgeKind.DERIVES_FROM
    provenance: Provenance = Provenance.PARSER
    confidence: Confidence = Confidence.HIGH
    evidence: Optional[str] = None


class LineageDiagnostic(BaseModel):
    kind: str
    severity: Severity = Severity.WARN
    message: str
    source_ref: SourceRef = Field(default_factory=SourceRef)
    node_id: Optional[str] = None
    impact: Optional[str] = None
    remediation: Optional[str] = None


class InventoryByKind(BaseModel):
    kind: str
    label: str
    count: int
    layer: str


class EstateMeta(BaseModel):
    name: str = "BI Estate"
    source_platform: Optional[str] = None
    target_platform: str = "Microsoft Fabric"
    run_id: Optional[str] = None
    scanned_at: Optional[str] = None
    illustrative: bool = False


class EstateGraph(BaseModel):
    meta: EstateMeta = Field(default_factory=EstateMeta)
    layers: List[LayerDef] = Field(default_factory=lambda: list(DEFAULT_LAYERS))
    nodes: List[LineageNode] = Field(default_factory=list)
    edges: List[LineageEdge] = Field(default_factory=list)
    diagnostics: List[LineageDiagnostic] = Field(default_factory=list)
    inventory_by_kind: List[InventoryByKind] = Field(default_factory=list)
    confidence: Dict[str, int] = Field(default_factory=dict)
    provenance: Dict[str, int] = Field(default_factory=dict)
    totals: Dict[str, int] = Field(default_factory=dict)
    kpis: Dict[str, float] = Field(default_factory=dict)

    def to_graph_json(self) -> dict:
        """Serialize with "schema" (not "schema_name") and JSON-native values."""
        return self.model_dump(by_alias=True, mode="json")
