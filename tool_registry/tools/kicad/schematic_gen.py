"""Generate a KiCad 9 schematic from a circuit description (FORGE-683).

Every symbol is copied from the KiCad symbol libraries (``extends`` flattened,
as KiCad itself embeds them), placed on a grid, and its pins are joined by
net labels placed exactly on each pin's connection point, so KiCad connects
them; a pin on no net gets a no-connect flag, and a net whose power inputs
nothing drives gets a ``PWR_FLAG``. The result is only claimed after KiCad's
own ERC has run on it and its exported netlist has been compared with the
circuit (that check lives in the adapter).

Nothing is drawn freehand: an unknown symbol, or a node naming a pin the
symbol does not have, is refused by name.

A part drawn with a stand-in symbol (it has none of its own) carries a
``Symbol_Substitute`` field saying so and is listed in the result
(FORGE-699): the pins are the stand-in's, so a person checks them.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.circuit import Circuit, symbol_substitutions
from tool_registry.tools.kicad.sexpr import QStr

__all__ = [
    "SchematicResult",
    "SymbolLibrary",
    "SymbolNotFoundError",
    "generate_schematic",
    "write_project_files",
]

SCH_VERSION = "20250114"
GRID = 2.54
#: Paper sizes (width, height) mm, smallest that fits wins.
PAPERS = (
    ("A4", 297.0, 210.0),
    ("A3", 420.0, 297.0),
    ("A2", 594.0, 420.0),
    ("A1", 841.0, 594.0),
    ("A0", 1189.0, 841.0),
)
MARGIN = 25.4


class SymbolNotFoundError(LookupError):
    """A lib_id the installed libraries do not have."""


@dataclass(frozen=True)
class Pin:
    unit: int
    number: str
    name: str
    etype: str
    x: float
    y: float
    angle: float


class SymbolLibrary:
    """Reads symbols out of ``*.kicad_sym`` files, flattening ``extends``."""

    def __init__(self, root: str = "/usr/share/kicad/symbols") -> None:
        self.root = Path(root)
        self._text: dict[str, str] = {}
        self._flat: dict[str, list[Any]] = {}

    def _lib_text(self, lib: str) -> str:
        if lib not in self._text:
            path = self.root / f"{lib}.kicad_sym"
            if not path.is_file():
                raise SymbolNotFoundError(f"no symbol library {lib!r} in {self.root}")
            self._text[lib] = path.read_text(encoding="utf-8")
        return self._text[lib]

    def _raw(self, lib: str, name: str) -> list[Any]:
        text = self._lib_text(lib)
        m = re.search(r'\n\s*\(symbol "' + re.escape(name) + r'"\s', text)
        if not m:
            raise SymbolNotFoundError(f"symbol {lib}:{name} is not in the {lib} library")
        start = text.index("(", m.start())
        return sexpr.loads(text[start : _block_end(text, start)])

    def symbol(self, lib_id: str) -> list[Any]:
        """The flattened symbol, named ``lib_id``, ready for ``lib_symbols``."""
        if lib_id in self._flat:
            return copy.deepcopy(self._flat[lib_id])
        lib, name = lib_id.split(":", 1)
        raw = self._raw(lib, name)
        parent_ref = sexpr.find(raw, "extends")
        if parent_ref is not None:
            parent = self.symbol(f"{lib}:{parent_ref[1]}")
            parent_name = str(parent_ref[1])
            flat = [x for x in parent if not (isinstance(x, list) and x[:1] == ["property"])]
            child_props = {str(p[1]): p for p in sexpr.children(raw, "property")}
            parent_props = {str(p[1]): p for p in sexpr.children(parent, "property")}
            merged = {**parent_props, **child_props}
            body = []
            for item in flat[2:]:
                if isinstance(item, list) and item[:1] == ["symbol"]:
                    item = copy.deepcopy(item)
                    item[1] = QStr(str(item[1]).replace(parent_name, name, 1))
                body.append(item)
            props = list(merged.values())
            # Keep the header atoms/flags of the parent, then properties, then units.
            head = [x for x in body if not (isinstance(x, list) and x[:1] == ["symbol"])]
            units = [x for x in body if isinstance(x, list) and x[:1] == ["symbol"]]
            flat_sym: list[Any] = ["symbol", QStr(lib_id), *head, *props, *units]
        else:
            flat_sym = ["symbol", QStr(lib_id), *raw[2:]]
        flat_sym = [x for x in flat_sym if not (isinstance(x, list) and x[:1] == ["extends"])]
        self._flat[lib_id] = flat_sym
        return copy.deepcopy(flat_sym)


def _block_end(text: str, start: int) -> int:
    """Index just past the S-expression that opens at ``start``."""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise sexpr.SExprError("unbalanced symbol block")


def symbol_pins(sym: list[Any]) -> list[Pin]:
    """Pins of a flattened symbol (body style 1), with the unit each belongs to."""
    pins: list[Pin] = []
    for unit_sym in sexpr.children(sym, "symbol"):
        m = re.match(r".*_(\d+)_(\d+)$", str(unit_sym[1]))
        if not m:
            continue
        unit, style = int(m.group(1)), int(m.group(2))
        if style not in (0, 1):
            continue
        for p in sexpr.children(unit_sym, "pin"):
            at = sexpr.find(p, "at")
            name = sexpr.find(p, "name")
            number = sexpr.find(p, "number")
            if at is None or number is None:
                continue
            pins.append(
                Pin(
                    unit=unit,
                    number=str(number[1]),
                    name=str(name[1]) if name else "",
                    etype=str(p[1]),
                    x=float(at[1]),
                    y=float(at[2]),
                    angle=float(at[3]) if len(at) > 3 else 0.0,
                )
            )
    return pins


def _units(pins: list[Pin]) -> list[int]:
    units = sorted({p.unit for p in pins if p.unit > 0})
    return units or [1]


def _bbox(pins: list[Pin], sym: list[Any]) -> tuple[float, float, float, float]:
    """Library-unit extent of the pins and every drawn shape (y up)."""
    xs = [p.x for p in pins] or [0.0]
    ys = [p.y for p in pins] or [0.0]
    for unit_sym in sexpr.children(sym, "symbol"):
        for shape in ("rectangle", "polyline", "circle", "arc", "bezier"):
            for item in sexpr.find_all(unit_sym, shape) + ([] if shape != "rectangle" else []):
                for key in ("start", "end", "mid", "center"):
                    pt = sexpr.find(item, key)
                    if pt:
                        xs.append(float(pt[1]))
                        ys.append(float(pt[2]))
                for pt in sexpr.find_all(item, "xy"):
                    xs.append(float(pt[1]))
                    ys.append(float(pt[2]))
                radius = sexpr.find(item, "radius")
                center = sexpr.find(item, "center")
                if radius and center:
                    r = float(radius[1])
                    xs += [float(center[1]) - r, float(center[1]) + r]
                    ys += [float(center[2]) - r, float(center[2]) + r]
    return min(xs), min(ys), max(xs), max(ys)


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def _u() -> QStr:
    return QStr(str(uuid.uuid4()))


def _effects(hide: bool = False, justify: list[str] | None = None) -> list[Any]:
    eff: list[Any] = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if justify:
        eff.append(["justify", *justify])
    if hide:
        eff.append(["hide", "yes"])
    return eff


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _text_spots(
    pins: list[Pin], ox: float, oy: float, bb: tuple[float, float, float, float]
) -> list[tuple[float, float, list[str] | None]]:
    """Where Reference and Value go: on a side of the symbol with no pins.

    Net labels stick out from every pin, so text above a part with pins on
    top, or beside one with pins on that side, lands on a label.
    """
    minx, miny, maxx, maxy = bb
    sides = {round(p.angle) % 360 for p in pins}
    cx = ox + (minx + maxx) / 2
    cy = oy - (miny + maxy) / 2
    if 270 not in sides:  # no pin points down from the top edge
        top = oy - maxy - 1.27
        return [(cx, top - 2.54, None), (cx, top, None)]
    if 180 not in sides:  # nothing on the right edge
        x = ox + maxx + 2.54
        return [(x, cy - 1.27, ["left"]), (x, cy + 1.27, ["left"])]
    if 0 not in sides:
        x = ox + minx - 2.54
        return [(x, cy - 1.27, ["right"]), (x, cy + 1.27, ["right"])]
    bottom = oy - miny + 1.27
    return [(cx, bottom + 1.27, None), (cx, bottom + 3.81, None)]


@dataclass
class SchematicResult:
    path: str
    symbols_placed: int
    labels: int
    no_connects: int
    power_flags: list[str] = field(default_factory=list)
    paper: str = "A4"
    pins: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    symbol_uuids: dict[str, str] = field(default_factory=dict)
    substitutions: list[dict[str, Any]] = field(default_factory=list)


def generate_schematic(
    circuit: Circuit, out_path: str, library: SymbolLibrary | None = None
) -> SchematicResult:
    lib = library or SymbolLibrary()
    net_of = circuit.net_of()
    root = _u()

    # -- resolve symbols and validate nodes ------------------------------------
    syms: dict[str, list[Any]] = {}
    comp_pins: dict[str, list[Pin]] = {}
    for comp in circuit.components:
        if comp.symbol not in syms:
            syms[comp.symbol] = lib.symbol(comp.symbol)
        pins = symbol_pins(syms[comp.symbol])
        comp_pins[comp.ref] = pins
        numbers = {p.number for p in pins}
        for node, _net in net_of.items():
            ref, pin = node.split(".", 1)
            if ref == comp.ref and pin not in numbers:
                raise ValueError(
                    f"{node}: {comp.symbol} has no pin {pin!r} "
                    f"(pins: {', '.join(sorted(numbers, key=lambda s: (len(s), s)))})"
                )

    # Nets whose power inputs nothing drives get a PWR_FLAG.
    pin_type = {f"{ref}.{p.number}": p.etype for ref, pins in comp_pins.items() for p in pins}
    flags: list[str] = []
    for net in circuit.nets:
        types = {pin_type.get(n, "") for n in net.nodes}
        if "power_in" in types and "power_out" not in types:
            flags.append(net.name)
    if flags:
        syms.setdefault("power:PWR_FLAG", lib.symbol("power:PWR_FLAG"))

    # -- placement: one instance per unit, packed in rows ------------------------
    instances: list[tuple[str, str, int, list[Pin], tuple[float, float, float, float]]] = []
    for comp in circuit.components:
        pins = comp_pins[comp.ref]
        for unit in _units(pins):
            upins = [p for p in pins if p.unit in (0, unit)]
            instances.append((comp.ref, comp.symbol, unit, upins, _bbox(upins, syms[comp.symbol])))
    for i, _flag_net in enumerate(flags):
        fpins = symbol_pins(syms["power:PWR_FLAG"])
        instances.append(
            (f"#FLG{i + 1:02d}", "power:PWR_FLAG", 1, fpins, (-2.54, -2.54, 2.54, 2.54))
        )

    label_room = 12.7  # net labels stick out from the pins
    widths = [(b[2] - b[0]) + 2 * label_room + GRID * 2 for *_, b in instances]
    total_area = sum(
        w * ((b[3] - b[1]) + 2 * label_room) for w, (*_, b) in zip(widths, instances, strict=True)
    )
    paper = PAPERS[-1]
    for cand in PAPERS:
        if cand[1] * cand[2] * 0.45 >= total_area and cand[1] - 2 * MARGIN >= max(widths):
            paper = cand
            break
    x0, y0 = MARGIN, MARGIN
    row_h = 0.0
    x, y = x0, y0
    placed: list[tuple[str, str, int, list[Pin], float, float]] = []
    bboxes = {(ref, unit): bb for ref, _l, unit, _p, bb in instances}
    for (ref, lib_id, unit, upins, bb), w in zip(instances, widths, strict=True):
        h = (bb[3] - bb[1]) + 2 * label_room
        if x + w > paper[1] - MARGIN and x > x0:
            x, y = x0, y + row_h
            row_h = 0.0
        # symbol origin so its bbox sits at (x + label_room, y + label_room)
        ox = _snap(x + label_room - bb[0])
        oy = _snap(y + label_room + bb[3])  # library y is up
        placed.append((ref, lib_id, unit, upins, ox, oy))
        x += w
        row_h = max(row_h, h)

    # -- write ------------------------------------------------------------------
    comp_by_ref = {c.ref: c for c in circuit.components}
    substitutions = symbol_substitutions(circuit)
    substitute_of = {s["ref"]: s for s in substitutions}
    body: list[Any] = []
    labels = no_connects = 0
    pin_report: dict[str, list[dict[str, str]]] = {}
    flag_net = {f"#FLG{i + 1:02d}": net for i, net in enumerate(flags)}
    symbol_uuids: dict[str, str] = {}
    for ref, lib_id, unit, upins, ox, oy in placed:
        sym_uuid = _u()
        if unit == 1 and not ref.startswith("#"):
            symbol_uuids[ref] = str(sym_uuid)
        if ref in flag_net:
            props = [
                ("Reference", ref, True),
                ("Value", "PWR_FLAG", True),
                ("Footprint", "", True),
                ("Datasheet", "", True),
            ]
        else:
            comp = comp_by_ref[ref]
            props = [
                ("Reference", ref, False),
                ("Value", comp.value, False),
                ("Footprint", comp.footprint, True),
                ("Datasheet", "", True),
            ]
            props += [(k, v, True) for k, v in comp.fields.items()]
            sub = substitute_of.get(ref)
            if sub is not None:
                note = sub["pinout_check"] or "pinout not yet checked against the datasheet"
                text = f"{lib_id} drawn for {sub['part_number']}; {note}"
                props.append(("Symbol_Substitute", text, True))
        inst: list[Any] = [
            "symbol",
            ["lib_id", QStr(lib_id)],
            ["at", _num(ox), _num(oy), "0"],
            ["unit", str(unit)],
            ["exclude_from_sim", "no"],
            ["in_bom", "no" if ref.startswith("#") else "yes"],
            ["on_board", "no" if ref.startswith("#") else "yes"],
            ["dnp", "no"],
            ["uuid", sym_uuid],
        ]
        spots = _text_spots(upins, ox, oy, bboxes[(ref, unit)])
        for k, (pname, pval, hide) in enumerate(props):
            tx, ty, justify = spots[k] if k < 2 else (ox, oy, None)
            inst.append(
                [
                    "property",
                    QStr(pname),
                    QStr(pval),
                    ["at", _num(tx), _num(ty), "0"],
                    _effects(hide=hide, justify=justify),
                ]
            )
        for p in upins:
            inst.append(["pin", QStr(p.number), ["uuid", _u()]])
        inst.append(
            [
                "instances",
                [
                    "project",
                    QStr(circuit.name),
                    ["path", QStr(f"/{root}"), ["reference", QStr(ref)], ["unit", str(unit)]],
                ],
            ]
        )
        body.append(inst)
        for p in upins:
            if unit != 1 and p.unit == 0:
                continue  # common pins once, on unit 1
            px, py = _num(ox + p.x), _num(oy - p.y)
            node = f"{ref}.{p.number}"
            pin_net = flag_net.get(ref) if ref in flag_net else net_of.get(node)
            if not ref.startswith("#"):
                pin_report.setdefault(ref, []).append(
                    {"pin": p.number, "name": p.name, "type": p.etype, "net": pin_net or ""}
                )
            if pin_net:
                out = (p.angle + 180.0) % 360.0
                justify = ["right", "bottom"] if out in (180.0, 270.0) else ["left", "bottom"]
                # Global labels: the net is named plainly (VIN, not /VIN), as the
                # board names it, so schematic parity in DRC can compare them.
                body.append(
                    [
                        "global_label",
                        QStr(pin_net),
                        ["shape", "passive"],
                        ["at", px, py, _num(out)],
                        ["fields_autoplaced", "yes"],
                        _effects(justify=justify[:1]),
                        ["uuid", _u()],
                        [
                            "property",
                            QStr("Intersheetrefs"),
                            QStr("${INTERSHEET_REFS}"),
                            ["at", px, py, "0"],
                            _effects(hide=True),
                        ],
                    ]
                )
                labels += 1
            else:
                body.append(["no_connect", ["at", px, py], ["uuid", _u()]])
                no_connects += 1

    doc: list[Any] = [
        "kicad_sch",
        ["version", SCH_VERSION],
        ["generator", QStr("metaforge")],
        ["generator_version", QStr("9.0")],
        ["uuid", root],
        ["paper", QStr(paper[0])],
        [
            "title_block",
            ["title", QStr(circuit.name)],
            ["comment", "1", QStr("Generated by MetaForge")],
        ],
        ["lib_symbols", *syms.values()],
        *body,
        ["sheet_instances", ["path", QStr("/"), ["page", QStr("1")]]],
        ["embedded_fonts", "no"],
    ]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(sexpr.dumps(doc) + "\n", encoding="utf-8")
    write_project_files(
        Path(out_path).parent,
        circuit.name,
        symbol_libs={lid.split(":", 1)[0] for lid in syms},
        footprint_libs={c.footprint.split(":", 1)[0] for c in circuit.components},
        symbol_root=str(lib.root),
    )
    return SchematicResult(
        path=out_path,
        symbols_placed=len(placed),
        labels=labels,
        no_connects=no_connects,
        power_flags=flags,
        paper=paper[0],
        pins=pin_report,
        symbol_uuids=symbol_uuids,
        substitutions=substitutions,
    )


def write_project_files(
    folder: Path,
    name: str,
    symbol_libs: set[str],
    footprint_libs: set[str],
    symbol_root: str = "/usr/share/kicad/symbols",
    footprint_root: str = "/usr/share/kicad/footprints",
) -> None:
    """A ``.kicad_pro`` and project library tables naming the libraries used.

    Without them KiCad cannot tell which installed library each symbol and
    footprint came from, and ERC/DRC say so for every part.
    """
    folder.mkdir(parents=True, exist_ok=True)
    pro = folder / f"{name}.kicad_pro"
    if not pro.exists():
        pro.write_text(
            '{\n  "meta": {"filename": "' + pro.name + '", "version": 3},\n'
            '  "board": {}, "schematic": {}, "libraries": {}\n}\n',
            encoding="utf-8",
        )
    sym: list[Any] = ["sym_lib_table", ["version", "7"]]
    for lib in sorted(symbol_libs):
        sym.append(
            [
                "lib",
                ["name", QStr(lib)],
                ["type", QStr("KiCad")],
                ["uri", QStr(f"{symbol_root}/{lib}.kicad_sym")],
                ["options", QStr("")],
                ["descr", QStr("")],
            ]
        )
    (folder / "sym-lib-table").write_text(sexpr.dumps(sym) + "\n", encoding="utf-8")
    fp: list[Any] = ["fp_lib_table", ["version", "7"]]
    for lib in sorted(footprint_libs):
        fp.append(
            [
                "lib",
                ["name", QStr(lib)],
                ["type", QStr("KiCad")],
                ["uri", QStr(f"{footprint_root}/{lib}.pretty")],
                ["options", QStr("")],
                ["descr", QStr("")],
            ]
        )
    (folder / "fp-lib-table").write_text(sexpr.dumps(fp) + "\n", encoding="utf-8")
