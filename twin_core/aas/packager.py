"""AASX packager — creates OPC-based ZIP files containing AAS JSON submodels.

The AASX format (IDTA-02001) is an OPC-based ZIP archive containing:
  - [Content_Types].xml  — MIME type declarations
  - _rels/.rels          — root relationships
  - aasx/aas/aas_env.json — the AAS environment (shells + submodels)
  - aasx/aas/*.json      — individual submodel files (optional)
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import structlog

from observability.tracing import get_tracer
from twin_core.aas.models import AASEnvironment, Submodel

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.aas.packager")

# OPC content types XML
CONTENT_TYPES_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
</Types>"""

# OPC root relationships XML
ROOT_RELS_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://www.admin-shell.io/aasx/relationships/aas-spec"
    Target="/aasx/aas/aas_env.json"/>
</Relationships>"""


class AASXPackager:
    """Creates AASX archive files from an AASEnvironment.

    The packager writes a standards-compliant OPC ZIP with JSON payloads.
    """

    def package_to_bytes(self, environment: AASEnvironment) -> bytes:
        """Serialize an AASEnvironment to an in-memory AASX (ZIP) archive.

        Args:
            environment: The complete AAS environment to package.

        Returns:
            Raw bytes of the AASX ZIP archive.
        """
        with tracer.start_as_current_span("aas.package_to_bytes") as span:
            span.set_attribute(
                "aas.shell_count",
                len(environment.asset_administration_shells),
            )
            span.set_attribute("aas.submodel_count", len(environment.submodels))

            buffer = io.BytesIO()

            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                self._write_opc_boilerplate(zf)
                self._write_environment(zf, environment)
                self._write_individual_submodels(zf, environment.submodels)

            result = buffer.getvalue()

            logger.info(
                "aasx_packaged",
                size_bytes=len(result),
                shell_count=len(environment.asset_administration_shells),
                submodel_count=len(environment.submodels),
            )

            return result

    def package_to_file(
        self, environment: AASEnvironment, output_path: str | Path
    ) -> Path:
        """Serialize an AASEnvironment to an AASX file on disk.

        Args:
            environment: The complete AAS environment to package.
            output_path: File path for the output .aasx file.

        Returns:
            The resolved Path of the written file.
        """
        with tracer.start_as_current_span("aas.package_to_file") as span:
            output = Path(output_path)
            span.set_attribute("aas.output_path", str(output))

            data = self.package_to_bytes(environment)
            output.write_bytes(data)

            logger.info(
                "aasx_written_to_file",
                path=str(output),
                size_bytes=len(data),
            )

            return output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_opc_boilerplate(self, zf: zipfile.ZipFile) -> None:
        """Write the OPC [Content_Types].xml and _rels/.rels files."""
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", ROOT_RELS_XML)

    def _write_environment(
        self, zf: zipfile.ZipFile, environment: AASEnvironment
    ) -> None:
        """Write the main AAS environment JSON file."""
        env_json = environment.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        zf.writestr(
            "aasx/aas/aas_env.json",
            json.dumps(env_json, indent=2, ensure_ascii=False),
        )

    def _write_individual_submodels(
        self, zf: zipfile.ZipFile, submodels: list[Submodel]
    ) -> None:
        """Write each submodel as an individual JSON file for easy access."""
        for submodel in submodels:
            sm_json = submodel.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            safe_name = submodel.id_short.replace(" ", "_").lower()
            zf.writestr(
                f"aasx/aas/{safe_name}.json",
                json.dumps(sm_json, indent=2, ensure_ascii=False),
            )
