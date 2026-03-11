"""FreeCAD / STEP file change adapter.

Parses FreeCAD native files (``.FCStd`` — ZIP containing XML) and
STEP/STP files, extracting part names, dimensions, materials, and
basic metadata.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import structlog

from digital_twin.assistant.adapters.base import (
    FileChangeAdapter,
    GraphMutation,
    MutationType,
)
from digital_twin.assistant.watcher import ChangeType, FileChangeEvent
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.assistant.adapters.freecad")

# Regex to count STEP entities (lines starting with #<number>)
_STEP_ENTITY_RE = re.compile(r"^#\d+", re.MULTILINE)


class FreecadAdapter(FileChangeAdapter):
    """Parse FreeCAD and STEP files into graph mutations."""

    @property
    def supported_extensions(self) -> set[str]:
        return {".FCStd", ".step", ".stp"}

    async def parse_change(self, event: FileChangeEvent) -> list[GraphMutation]:
        """Parse a FreeCAD/STEP file change into graph mutations."""
        with tracer.start_as_current_span("freecad.parse_change") as span:
            span.set_attribute("file.path", event.path)
            span.set_attribute("file.change_type", str(event.change_type))

            if event.change_type == ChangeType.DELETED:
                return [
                    GraphMutation(
                        mutation_type=MutationType.NODE_DELETED,
                        node_type="cad_file",
                        node_id=event.path,
                        source_file=event.path,
                    )
                ]

            path = Path(event.path)
            try:
                if path.suffix == ".FCStd":
                    mutations = self._parse_fcstd(path, event.path)
                else:
                    mutations = self._parse_step(path, event.path)
            except Exception as exc:
                logger.error(
                    "freecad_parse_failed",
                    path=event.path,
                    suffix=path.suffix,
                    error=str(exc),
                )
                span.record_exception(exc)
                return []

            span.set_attribute("freecad.mutation_count", len(mutations))
            logger.info(
                "freecad_parsed",
                path=event.path,
                mutation_count=len(mutations),
            )
            return mutations

    # -- FCStd parsing (ZIP + XML) ------------------------------------------

    def _parse_fcstd(self, path: Path, source: str) -> list[GraphMutation]:
        """Extract part names, dimensions, and materials from an FCStd file.

        An ``.FCStd`` file is a ZIP archive containing ``Document.xml``
        with the FreeCAD object tree.
        """
        mutations: list[GraphMutation] = []

        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "Document.xml" not in zf.namelist():
                    logger.warning("fcstd_missing_document_xml", path=source)
                    mutations.append(
                        GraphMutation(
                            mutation_type=MutationType.NODE_UPDATED,
                            node_type="cad_assembly",
                            node_id=f"{source}::assembly",
                            properties={
                                "source_file": source,
                                "files_in_archive": zf.namelist(),
                            },
                            source_file=source,
                        )
                    )
                    return mutations

                with zf.open("Document.xml") as doc_file:
                    tree = ET.parse(doc_file)  # noqa: S314
                    root = tree.getroot()

                parts = self._extract_parts_from_xml(root)
                for part in parts:
                    mutations.append(
                        GraphMutation(
                            mutation_type=MutationType.NODE_UPDATED,
                            node_type="cad_part",
                            node_id=f"{source}::{part['name']}",
                            properties={
                                **part,
                                "source_file": source,
                            },
                            source_file=source,
                        )
                    )

        except zipfile.BadZipFile as exc:
            logger.error("fcstd_bad_zip", path=source, error=str(exc))
            return []

        return mutations

    def _extract_parts_from_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Walk FreeCAD Document.xml to find Object elements."""
        parts: list[dict[str, str]] = []

        # FreeCAD Document.xml has <ObjectData> -> <Object name="..."> -> <Properties>
        for obj_data in root.iter("ObjectData"):
            for obj in obj_data.iter("Object"):
                name = obj.get("name", "")
                if not name:
                    continue
                props: dict[str, str] = {"name": name}

                for prop in obj.iter("Property"):
                    prop_name = prop.get("name", "")
                    if prop_name == "Label":
                        label_el = prop.find("String")
                        if label_el is not None:
                            props["label"] = label_el.get("value", "")
                    elif prop_name == "Material":
                        mat_el = prop.find("String")
                        if mat_el is not None:
                            props["material"] = mat_el.get("value", "")
                    elif prop_name in ("Length", "Width", "Height"):
                        float_el = prop.find("Float")
                        if float_el is not None:
                            props[prop_name.lower()] = float_el.get("value", "")

                parts.append(props)

        return parts

    # -- STEP/STP parsing ---------------------------------------------------

    def _parse_step(self, path: Path, source: str) -> list[GraphMutation]:
        """Extract basic metadata from a STEP/STP file."""
        try:
            stat = path.stat()
        except OSError as exc:
            logger.error("step_stat_failed", path=source, error=str(exc))
            return []

        properties: dict[str, object] = {
            "source_file": source,
            "file_size_bytes": stat.st_size,
            "modification_date": str(stat.st_mtime),
        }

        # Count STEP entities for a rough complexity metric
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            entity_count = len(_STEP_ENTITY_RE.findall(content))
            properties["entity_count"] = entity_count
        except OSError:
            properties["entity_count"] = 0

        return [
            GraphMutation(
                mutation_type=MutationType.NODE_UPDATED,
                node_type="step_model",
                node_id=f"{source}::step",
                properties=properties,
                source_file=source,
            )
        ]
