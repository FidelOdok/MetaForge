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
    DatParseError,
    FrdParseError,
    extract_results,
    parse_frd_file,
    parse_frequencies_dat,
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


REAL_CCX_THERMAL_FRD = """\
    1C
    1UUSER
    1UDATE              25.august.2026
    1UTIME              04:21:21
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
 -4  NDTEMP      1    1
 -5  T           1    1    0    0
 -1         1 2.00000E+01
 -1         2 2.00000E+01
 -1         3 2.00000E+01
 -1         4 2.00000E+01
 -1         5 1.00000E+02
 -1         6 1.00000E+02
 -1         7 1.00000E+02
 -1         8 1.00000E+02
 -3
 9999
"""


@pytest.fixture
def real_frd_path(tmp_path: Path) -> str:
    path = tmp_path / "test_cube.frd"
    path.write_text(REAL_CCX_FRD, encoding="utf-8")
    return str(path)


@pytest.fixture
def real_thermal_frd_path(tmp_path: Path) -> str:
    path = tmp_path / "test_thermal.frd"
    path.write_text(REAL_CCX_THERMAL_FRD, encoding="utf-8")
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

    def test_stress_carries_an_accuracy_verdict(self, real_frd_path: str) -> None:
        """FORGE-280: auto-flag is computed unconditionally, from the full
        nodal field, so it's present even without include_node_data=False
        stripping anything."""
        result = extract_results(real_frd_path)
        assert "accuracy" in result["stress"]
        assert result["stress"]["accuracy"]["suspicious"] in (True, False)

    def test_accuracy_survives_stripped_node_data(self, real_frd_path: str) -> None:
        """The accuracy verdict is a summary, computed before include_node_
        data=False discards the raw per-node values it was computed from."""
        result = extract_results(real_frd_path, include_node_data=False)
        assert result["stress"]["nodes"] == {}
        assert "accuracy" in result["stress"]
        assert result["stress"]["accuracy"]["max_to_median_ratio"] is not None


class TestParseFrdFileRealThermalOutput:
    """MET-661 follow-up: same -4/100C block-detection bug applies to the
    NDTEMP block used by calculix.run_thermal (adapter.py previously never
    parsed this at all -- it returned hardcoded zeros unconditionally)."""

    def test_extracts_nonzero_nodal_temperatures(self, real_thermal_frd_path: str) -> None:
        result = parse_frd_file(real_thermal_frd_path)

        temperature = result["temperature"]
        assert temperature["nodes"], "NDTEMP block must be found and parsed"
        assert temperature["nodes"][1] == pytest.approx(20.0)
        assert temperature["nodes"][5] == pytest.approx(100.0)
        assert temperature["max"] == pytest.approx(100.0)
        assert temperature["min"] == pytest.approx(20.0)

    def test_temperature_does_not_leak_into_stress_or_displacement(
        self, real_thermal_frd_path: str
    ) -> None:
        result = parse_frd_file(real_thermal_frd_path)
        assert result["stress"]["nodes"] == {}
        assert result["displacement"]["nodes"] == {}


class TestParseFrdFileErrors:
    def test_missing_file_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_frd_file("/nonexistent/path.frd")

    def test_empty_file_raises_instead_of_silently_reporting_success(self, tmp_path: Path) -> None:
        """FORGE-232: this used to return a "successful" empty result --
        exactly the class of bug the ticket reported (CalculiX solving an
        empty deck and every caller reporting success on nothing). node_count
        == 0 must be a loud failure, not a quiet one."""
        path = tmp_path / "empty.frd"
        path.write_text("", encoding="utf-8")

        with pytest.raises(FrdParseError, match="node_count == 0"):
            parse_frd_file(str(path))

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


# FORGE-281 follow-up: byte-for-byte capture of a real ccx *FREQUENCY .dat
# file, same discipline as REAL_CCX_FRD above. Captured live on fidel-dev
# 2026-09-30 from a real modal solve of a 100x20x10mm steel cantilever
# (fixed at the x=0 face), AFTER fixing the *DENSITY-card float-precision
# bug this same follow-up found (see deck_builder.py's ``_fmt`` docstring --
# the original synthetic fixture this replaced assumed a "F R E Q U E N C I E S"
# header with 2 data columns; the real header is "E I G E N V A L U E
# O U T P U T" with 4 data columns per mode, Hz being the 3rd, not the
# last -- neither original assumption was correct).
REAL_CCX_DAT_FREQUENCIES = """\
     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.2755344E+08   0.5249137E+04   0.8354261E+03   0.0000000E+00
      2   0.1012618E+09   0.1006289E+05   0.1601559E+04   0.0000000E+00
      3   0.9911443E+09   0.3148245E+05   0.5010587E+04   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S
"""


class TestParseFrequenciesDat:
    def test_extracts_frequencies_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "modal.dat"
        path.write_text(REAL_CCX_DAT_FREQUENCIES, encoding="utf-8")
        frequencies = parse_frequencies_dat(str(path))
        assert frequencies == pytest.approx([835.4261, 1601.559, 5010.587])

    def test_missing_file_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_frequencies_dat("/nonexistent/path.dat")

    def test_no_frequency_block_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "no_freq.dat"
        path.write_text("some unrelated ccx output\n", encoding="utf-8")
        with pytest.raises(DatParseError, match="no frequency block"):
            parse_frequencies_dat(str(path))

    def test_header_with_no_mode_lines_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_freq.dat"
        path.write_text("E I G E N V A L U E   O U T P U T\n\n(nothing here)\n", encoding="utf-8")
        with pytest.raises(DatParseError, match="no mode data lines"):
            parse_frequencies_dat(str(path))
