"""Regression tests for the CalculiX .frd result parser (MET-661).

The fixture below is a byte-for-byte capture of a real ccx 2.20 .frd
output (a single C3D8 cube, nodes 1-4 fixed, nodes 5-8 loaded with
-1000N in Z). It proves the parser against the solver's actual output
format, not an assumed one -- the previous implementation looked for a
``100C`` header line naming the result type (e.g. "100CL 101STRESS"),
but real ccx output names the block on the ``-4`` line instead
(``-4  STRESS      6    1``); the ``100CL`` line is generic step
metadata shared by every block type. That mismatch meant
``in_stress_block``/``in_disp_block`` was never set True, so every
real FEA run silently returned empty/zero results despite a
successful, convergent solve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_registry.tools.calculix.result_parser import (
    FrdParseError,
    extract_results,
    parse_frd_file,
)

REAL_CCX_FRD = """\
    1C
    1UUSER
    1UDATE              25.august.2026
    1UTIME              03:55:10
    1UHOST
    1UPGM               CalculiX
    1UVERSION           Version 2.20
    1UCOMPILETIME       Sun Jul 31 18:08:37 CEST 2022
    1UDIR
    1UDBN
    1UMAT    1STEEL
    2C                             8                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -1         3 1.00000E+01 1.00000E+01 0.00000E+00
 -1         4 0.00000E+00 1.00000E+01 0.00000E+00
 -1         5 0.00000E+00 0.00000E+00 1.00000E+01
 -1         6 1.00000E+01 0.00000E+00 1.00000E+01
 -1         7 1.00000E+01 1.00000E+01 1.00000E+01
 -1         8 0.00000E+00 1.00000E+01 1.00000E+01
 -3
    3C                             1                                     1
 -1         1    1    0    1
 -2         1         2         3         4         5         6         7         8
 -3
    1PSTEP                         1           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -5  D2          1    2    2    0
 -5  D3          1    2    3    0
 -5  ALL         1    2    0    0    1ALL
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 0.00000E+00 0.00000E+00 0.00000E+00
 -1         3 0.00000E+00 0.00000E+00 0.00000E+00
 -1         4 0.00000E+00 0.00000E+00 0.00000E+00
 -1         5-3.71429E-04-3.71429E-04-1.73333E-03
 -1         6 3.71429E-04-3.71429E-04-1.73333E-03
 -1         7 3.71429E-04 3.71429E-04-1.73333E-03
 -1         8-3.71429E-04 3.71429E-04-1.73333E-03
 -3
    1PSTEP                         2           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -5  SYY         1    4    2    2
 -5  SZZ         1    4    3    3
 -5  SXY         1    4    1    2
 -5  SYZ         1    4    2    3
 -5  SZX         1    4    3    1
 -1         1-2.09997E+01-2.09997E+01-4.89983E+01 1.48489E-16-2.99998E+00-2.99998E+00
 -1         2-2.09997E+01-2.09997E+01-4.89983E+01-3.23572E-17-2.99998E+00 2.99998E+00
 -1         3-2.09997E+01-2.09997E+01-4.89983E+01 7.29624E-17 2.99998E+00 2.99998E+00
 -1         4-2.09997E+01-2.09997E+01-4.89983E+01 2.84439E-17 2.99998E+00-2.99998E+00
 -1         5 9.00015E+00 9.00015E+00-3.09985E+01 1.32215E-16-2.99998E+00-2.99998E+00
 -1         6 9.00015E+00 9.00015E+00-3.09985E+01 3.05009E-15-2.99998E+00 2.99998E+00
 -1         7 9.00015E+00 9.00015E+00-3.09985E+01 4.05887E-15 2.99998E+00 2.99998E+00
 -1         8 9.00015E+00 9.00015E+00-3.09985E+01 1.98207E-15 2.99998E+00-2.99998E+00
 -3
    1PSTEP                         3           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  ERROR       1    1
 -5  STR(%)      1    1    0    0
 -1         1 1.12061E+01
 -1         2 1.12061E+01
 -1         3 1.12061E+01
 -1         4 1.12061E+01
 -1         5 1.12061E+01
 -1         6 1.12061E+01
 -1         7 1.12061E+01
 -1         8 1.12061E+01
 -3
 9999
"""


@pytest.fixture
def real_frd_path(tmp_path: Path) -> str:
    path = tmp_path / "test_cube.frd"
    path.write_text(REAL_CCX_FRD, encoding="utf-8")
    return str(path)


class TestParseFrdFileRealCcxOutput:
    def test_extracts_nonzero_displacement_for_loaded_nodes(self, real_frd_path: str) -> None:
        result = parse_frd_file(real_frd_path)

        disp = result["displacement"]
        assert disp["nodes"], "DISP block must be found and parsed"
        # Node 5 has real, nonzero displacement (fixed nodes 1-4 are ~0).
        assert disp["nodes"][5] == pytest.approx(0.00181117, rel=1e-3)
        assert disp["max"] > 0.0

    def test_extracts_nonzero_von_mises_stress(self, real_frd_path: str) -> None:
        result = parse_frd_file(real_frd_path)

        stress = result["stress"]
        assert stress["nodes"], "STRESS block must be found and parsed"
        assert all(v > 0.0 for v in stress["nodes"].values())
        assert stress["max"] > 0.0

    def test_node_count_reflects_real_mesh(self, real_frd_path: str) -> None:
        result = parse_frd_file(real_frd_path)
        assert result["node_count"] == 8

    def test_does_not_pick_up_mesh_coordinates_as_results(self, real_frd_path: str) -> None:
        # The nodal-coordinate "-1" lines under the "2C" mesh header (before
        # any "-4" block) must never be mistaken for DISP/STRESS data.
        result = parse_frd_file(real_frd_path)
        for field in ("stress", "displacement"):
            assert set(result[field]["nodes"]) <= {1, 2, 3, 4, 5, 6, 7, 8}

    def test_ignores_the_error_block(self, real_frd_path: str) -> None:
        # A third "-4  ERROR ..." block follows STRESS; it must not leak
        # into either the stress or displacement extraction.
        result = parse_frd_file(real_frd_path)
        assert result["stress"]["max"] < 100.0

    def test_extract_results_high_level_entry_point(self, real_frd_path: str) -> None:
        result = extract_results(real_frd_path)
        assert result["displacement"]["nodes"]
        assert result["stress"]["nodes"]


class TestParseFrdFileErrors:
    def test_missing_file_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_frd_file("/nonexistent/path.frd")

    def test_empty_file_yields_empty_results_not_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.frd"
        path.write_text("", encoding="utf-8")

        result = parse_frd_file(str(path))
        assert result["node_count"] == 0
        assert result["stress"]["nodes"] == {}
        assert result["displacement"]["nodes"] == {}

    def test_unreadable_file_raises_frd_parse_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "test.frd"
        path.write_text(REAL_CCX_FRD, encoding="utf-8")

        def _boom(*_a: object, **_k: object) -> str:
            raise OSError("disk gone")

        monkeypatch.setattr(Path, "read_text", _boom)
        with pytest.raises(FrdParseError):
            parse_frd_file(str(path))
