"""Interface 360 loader — writes interfaces, derived systems, routing hops."""
from __future__ import annotations
from collections import defaultdict

from .loader import Loader
from .interface360_model import Interface360Bundle
from .project_resolver import ProjectResolver


class Interface360Loader:
    def __init__(self, loader: Loader, resolver: ProjectResolver):
        self.loader = loader
        self.resolver = resolver

    def load(self, bundle: Interface360Bundle) -> None:
        sys_agg: dict[str, dict] = defaultdict(
            lambda: {"out": 0, "in": 0, "party": None, "pii": "N"})

        for iface in bundle.interfaces:
            self.loader._merge(
                "interface360_interfaces", ("interface_id",), {
                    "interface_id": iface.interface_id, "domain": iface.domain,
                    "date_of_update": iface.date_of_update, "scope": iface.scope,
                    "update_owner": iface.update_owner, "application": iface.application,
                    "integration_name": iface.integration_name,
                    "description": iface.description, "feed_type": iface.feed_type,
                    "source_system": iface.source_system, "source_party": iface.source_party,
                    "target_system": iface.target_system, "target_party": iface.target_party,
                    "direction": iface.direction, "direct_feed": iface.direct_feed,
                    "feed_routing": iface.feed_routing, "intraday": iface.intraday,
                    "eod_overnight": iface.eod_overnight, "frequency": iface.frequency,
                    "extract_type": iface.extract_type, "app_contact": iface.app_owner,
                    "migration_flag": iface.migration_flag,
                    "type_app_extract": iface.type_app_extract,
                    "process_improvement": iface.improvement, "notes": iface.notes,
                    "source_project_id": iface.source_project_id,
                    "target_project_id": iface.target_project_id,
                },
                protect=("carries_pii", "pii_categories"))  # set by PII matcher

            if iface.source_system:
                sys_agg[iface.source_system]["out"] += 1
                sys_agg[iface.source_system]["party"] = iface.source_party
            if iface.target_system:
                sys_agg[iface.target_system]["in"] += 1
                sys_agg[iface.target_system]["party"] = iface.target_party

            for hop_order, hop in enumerate(iface.routing_hops):
                self.loader._merge(
                    "interface360_routing_hops", ("interface_id", "hop_order"), {
                        "interface_id": iface.interface_id, "hop_order": hop_order,
                        "system_name": hop,
                        "project_id": self.resolver.resolve_for_interface_system(hop),
                    })

        for sys_name, agg in sys_agg.items():
            self.loader._merge(
                "interface360_systems", ("system_name",), {
                    "system_name": sys_name,
                    "project_id": self.resolver.resolve_for_interface_system(sys_name),
                    "party": agg["party"], "outbound_count": agg["out"],
                    "inbound_count": agg["in"], "total_count": agg["out"] + agg["in"],
                },
                protect=("carries_pii",))
        self.loader.commit()
