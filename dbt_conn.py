"""
dbt connector — reads manifest.json (+ optional catalog.json) and writes:
  - datasets (object_type='MODEL') per dbt model, with project_id resolved
  - transformations (compiled_sql) per model
  - transform_lineage edges from the ref()/source() dependency graph

Project detection uses ProjectResolver.resolve_for_dbt (meta.project, then
^sei_/^swp_ name patterns). Pure parsing; writes only via loader._merge.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any, Optional

from .base import BaseConnector
from .model import Dataset, Column
from .project_resolver import ProjectResolver

log = logging.getLogger(__name__)

# dbt materialization / layer hint from the model's path or config
_LAYER_HINTS = (("bronze", "bronze"), ("brz", "bronze"), ("staging", "bronze"),
                ("silver", "silver"), ("slv", "silver"), ("intermediate", "silver"),
                ("gold", "gold"), ("gld", "gold"), ("mart", "gold"))


def _layer_of(name: str, path: str) -> Optional[str]:
    hay = f"{name} {path}".lower()
    for needle, layer in _LAYER_HINTS:
        if needle in hay:
            return layer
    return None


class DbtConnector(BaseConnector):
    PLATFORM_ID = "dbt"

    def __init__(self, manifest_path: str, resolver: ProjectResolver,
                 catalog_path: Optional[str] = None):
        self.manifest_path = manifest_path
        self.catalog_path = catalog_path
        self.resolver = resolver

    @classmethod
    def from_env(cls) -> "DbtConnector":
        return cls(
            manifest_path=os.environ["DBT_MANIFEST_PATH"],
            catalog_path=os.getenv("DBT_CATALOG_PATH"),
            resolver=ProjectResolver.from_env(),
        )

    # ---- parse -------------------------------------------------------
    def parse(self) -> dict[str, Any]:
        with open(self.manifest_path, encoding="utf-8", errors="replace") as fh:
            manifest = json.load(fh)
        catalog = {}
        if self.catalog_path and os.path.exists(self.catalog_path):
            with open(self.catalog_path, encoding="utf-8", errors="replace") as fh:
                catalog = json.load(fh)

        nodes = manifest.get("nodes", {})
        sources = manifest.get("sources", {})
        datasets: list[Dataset] = []
        transforms: list[dict] = []
        edges: list[dict] = []

        # map unique_id -> dataset_key, for lineage edge resolution
        key_of: dict[str, str] = {}

        for uid, node in nodes.items():
            if node.get("resource_type") != "model":
                continue
            name = node.get("name", uid.split(".")[-1])
            meta = (node.get("config") or {}).get("meta") or node.get("meta") or {}
            project_id = self.resolver.resolve_for_dbt(name, meta)
            schema = node.get("schema") or "dbt"
            path = node.get("path", "")
            layer = _layer_of(name, path)
            ds = Dataset(
                platform_id=self.PLATFORM_ID, schema=schema, object_name=name,
                object_type="MODEL", project_id=project_id, layer=layer,
                tech_desc=node.get("description"),
                owner=(meta or {}).get("owner"))
            # columns from catalog.json if available
            cat_node = (catalog.get("nodes") or {}).get(uid, {})
            for cidx, (cname, cmeta) in enumerate(
                    (cat_node.get("columns") or {}).items(), start=1):
                ds.columns.append(Column(
                    platform_id=self.PLATFORM_ID, schema=schema, object_name=name,
                    column_name=cname, position_order=cidx,
                    data_type=cmeta.get("type"),
                    tech_desc=(node.get("columns", {}).get(cname, {}) or {}).get("description")))
            datasets.append(ds)
            key_of[uid] = ds.key
            # transformation row (compiled SQL)
            transforms.append({
                "target_key": ds.key, "transform_type": "dbt_model",
                "dbt_model": name, "project_id": project_id,
                "compiled_sql": node.get("compiled_code") or node.get("compiled_sql"),
                "raw_sql": node.get("raw_code") or node.get("raw_sql"),
            })

        # source nodes -> dataset keys (so source->model edges resolve)
        for uid, snode in sources.items():
            sname = snode.get("name", uid.split(".")[-1])
            sschema = snode.get("schema") or snode.get("source_name") or "source"
            key_of[uid] = f"{self.PLATFORM_ID}.{sschema}.{sname}"

        # lineage edges from depends_on.nodes
        for uid, node in nodes.items():
            if node.get("resource_type") != "model":
                continue
            to_key = key_of.get(uid)
            for dep in (node.get("depends_on") or {}).get("nodes", []):
                from_key = key_of.get(dep)
                if from_key and to_key:
                    edges.append({
                        "edge_id": f"{from_key}->{to_key}",
                        "from_key": from_key, "to_key": to_key,
                        "from_type": "dataset", "to_type": "dataset",
                        "source": "dbt",
                        "project_id": self.resolver.resolve_for_dbt(
                            node.get("name", ""), {}),
                    })

        log.info("dbt: %d models, %d transforms, %d edges",
                 len(datasets), len(transforms), len(edges))
        return {"datasets": datasets, "transforms": transforms, "edges": edges}

    # ---- load --------------------------------------------------------
    def load(self, loader, bundle: dict[str, Any]) -> None:
        for ds in bundle["datasets"]:
            loader._merge("datasets",
                ("platform_id", "schema_name", "object_name"),
                {"platform_id": ds.platform_id, "schema_name": ds.schema,
                 "object_name": ds.object_name, "object_type": ds.object_type,
                 "project_id": ds.project_id, "layer": ds.layer,
                 "tech_desc": ds.tech_desc, "owner": ds.owner},
                protect=("business_desc",))
            for c in ds.columns:
                loader._merge("columns",
                    ("platform_id", "schema_name", "object_name", "column_name"),
                    {"platform_id": ds.platform_id, "schema_name": ds.schema,
                     "object_name": ds.object_name, "column_name": c.name,
                     "position_order": c.position_order, "data_type": c.data_type,
                     "tech_desc": c.tech_desc},
                    protect=("business_desc", "is_pii", "pii_category", "pii_attribute"))
        for tr in bundle["transforms"]:
            loader._merge("transformations", ("target_key",), tr)
        for e in bundle["edges"]:
            loader._merge("transform_lineage", ("edge_id",), e)
        loader.commit()
