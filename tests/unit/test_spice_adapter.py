"""spice.run_simulation: deck building and ngspice rawfile parsing (FORGE-542).

The rawfiles in tests/fixtures/spice are real ngspice-44.2 output from the
spice-adapter image (a 5 V 1k/1k divider; a 1k/1u RC low-pass, AC 1 Hz to
100 kHz). ngspice itself runs only in that container; a live run there is
covered by the deploy check, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_registry.tools.spice.adapter import (
    SpiceServer,
    analysis_card,
    build_deck,
    parse_ascii_raw,
    summarise,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "spice"
DIVIDER = "V1 in 0 DC 5\nR1 in out 1k\nR2 out 0 1k\n"


class TestDeck:
    def test_params_become_one_card(self) -> None:
        deck = build_deck(DIVIDER, "transient", {"step": "10u", "stop": "5m"})
        assert deck.splitlines()[0].startswith("*")
        assert ".tran 10u 5m" in deck
        assert deck.rstrip().endswith(".end")

    def test_netlist_card_is_used_when_no_params(self) -> None:
        deck = build_deck(DIVIDER + ".dc V1 0 5 1\n.end\n", "dc", {})
        assert deck.count(".dc V1 0 5 1") == 1
        assert deck.count(".end") == 1

    def test_card_and_params_together_are_refused(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            build_deck(DIVIDER + ".op\n", "op", {"x": 1})

    def test_control_blocks_are_refused(self) -> None:
        with pytest.raises(ValueError, match=".control"):
            build_deck(DIVIDER + ".control\nrun\n.endc\n", "op", {})

    def test_no_card_and_no_params_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            build_deck(DIVIDER, "ac", {})

    @pytest.mark.parametrize(
        "analysis, params, card",
        [
            ("dc", {"source": "V1", "start": 0, "stop": 5, "step": 0.5}, ".dc V1 0 5 0.5"),
            (
                "ac",
                {"variation": "oct", "points": 5, "fstart": 10, "fstop": "1meg"},
                ".ac oct 5 10 1meg",
            ),
            ("transient", {"step": "1u", "stop": "1m", "max_step": "1u"}, ".tran 1u 1m 0 1u"),
        ],
    )
    def test_cards(self, analysis: str, params: dict, card: str) -> None:
        assert analysis_card(analysis, params) == card

    def test_missing_param_and_injection_are_refused(self) -> None:
        with pytest.raises(ValueError, match="needs params: stop"):
            analysis_card("transient", {"step": "1u"})
        with pytest.raises(ValueError, match="plain SPICE value"):
            analysis_card("transient", {"step": "1u", "stop": "1m\n.include /etc/passwd"})


class TestRawfile:
    def test_operating_point(self) -> None:
        (plot,) = parse_ascii_raw((FIX / "op_divider.raw").read_text())
        results, waveform, scale = summarise(plot, [], 200)
        assert results["v(out)"]["final"] == pytest.approx(2.5)
        assert results["i(v1)"]["final"] == pytest.approx(-2.5e-3)
        assert scale is None and waveform == {}

    def test_dc_sweep(self) -> None:
        (plot,) = parse_ascii_raw((FIX / "dc_divider.raw").read_text())
        results, waveform, scale = summarise(plot, ["V(OUT)"], 200)
        assert scale == "v(v-sweep)"
        assert waveform["v(out)"] == pytest.approx([x / 2 for x in range(11)])
        assert results["v(out)"]["max"] == pytest.approx(5.0)

    def test_ac_magnitude_and_phase(self) -> None:
        (plot,) = parse_ascii_raw((FIX / "ac_rc.raw").read_text())
        assert plot["flags"] == "complex"
        results, waveform, scale = summarise(plot, ["v(out)"], 4)
        # fc = 1 / (2 pi 1k 1u) = 159 Hz, so 100 kHz is 20 log(159/100k) down
        assert results["v(out)"]["mag_db_final"] == pytest.approx(-55.96, abs=0.01)
        assert results["v(out)"]["phase_deg_final"] == pytest.approx(-89.9, abs=0.1)
        assert scale == "frequency"
        assert len(waveform["frequency"]) == 5  # 4 by stride plus the last point

    def test_unknown_probe_is_refused(self) -> None:
        (plot,) = parse_ascii_raw((FIX / "op_divider.raw").read_text())
        with pytest.raises(ValueError, match="not in the results"):
            summarise(plot, ["v(nope)"], 10)


class TestServer:
    async def test_needs_exactly_one_netlist_source(self) -> None:
        server = SpiceServer()
        with pytest.raises(ValueError, match="exactly one"):
            await server.run_simulation({"analysis_type": "op"})

    async def test_missing_binary_is_reported(self, tmp_path: Path) -> None:
        from tool_registry.tools.spice.adapter import SpiceNotAvailableError
        from tool_registry.tools.spice.config import SpiceConfig

        server = SpiceServer(SpiceConfig(ngspice="ngspice-not-installed", work_dir=str(tmp_path)))
        with pytest.raises(SpiceNotAvailableError, match="spice-adapter"):
            await server.run_simulation({"analysis_type": "op", "netlist": DIVIDER})
