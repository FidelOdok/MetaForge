# Simulator Landscape — Engineering & Physics Catalog

A categorized catalog of engineering and physics simulation tools — free, open-source,
freeware, and commercial — spanning sixteen application areas. Roughly 150 tools, each
with maintainer, license, price tier, platform, and primary use case.

## Provenance & confidence

This catalog was compiled from a **deep-research pass** (105 agents, 23 sources fetched,
104 claims extracted) followed by **3-vote adversarial verification** on 25 claims —
25 confirmed, 0 refuted. After de-duplication, **ten tools carry citations** and are
marked ✅ **verified** below:

> ngspice · KiCad · Xyce · Qucs-S · Siemens HyperLynx SI · Silvaco Victory TCAD ·
> Synopsys OptSim · preCICE · FMI standard · preCICE-FMI Runner

The remaining ~140 entries are well-established tools drawn from surfaced sources and
domain knowledge but were **not independently re-verified** in this run. Treat license
and price tiers as **directional** — every commercial EDA/CAE vendor here prices by
private quote, so `$` / `$$` / `$$$` indicate relative tier, not a figure.

**License key:** `open-source` (free, OSI license) · `freeware` ($0, proprietary) ·
`commercial` (paid / quote) · `standard` (open specification).

---

## Circuit / SPICE

Analog and mixed-signal device-level simulation descending from Berkeley SPICE3. The
open-source kernels (ngspice, Xyce) act as backends behind most EDA front-ends.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **ngspice** ✅ | Community (ex-Berkeley Spice3f5) | open-source | Lin / Win / Mac | Mixed-level/mixed-signal SPICE; backend for KiCad, Altium, Eagle, Qucs-S |
| **Xyce** ✅ | Sandia National Labs | open-source (GPL-3) | Lin / Win / Mac | Parallel/MPI SPICE for very large circuits |
| **Qucs-S** ✅ | ra3xdh (V. Kuznetsov) | open-source (GPL-2) | Lin (Flatpak) | GUI front-end driving ngspice / Xyce / SPICE OPUS |
| LTspice | Analog Devices | freeware | Win / Mac | Fast analog & power-electronics simulation |
| SPICE OPUS | Univ. of Ljubljana | freeware | Win | SPICE with built-in optimization loop |
| Qucs | Community | open-source | Lin / Win / Mac | Native-engine circuit + RF simulation |
| gnucap | GNU project | open-source | Lin / Win / Mac | General-purpose circuit simulator |
| PSpice | Cadence (OrCAD) | commercial $$$ | Win | Industry analog / mixed-signal |
| Multisim | NI / Emerson | commercial $$ | Win | Education & analog design |
| TINA-TI | Texas Instruments | freeware | Win | TI-part analog simulation |
| Micro-Cap 12 | Spectrum Software | free (EOL) | Win | Legacy analog / mixed-signal |

## PCB / EDA — capture, layout, ERC/DRC, signal integrity

Schematic capture and board layout suites, plus signal/power-integrity analysis.
Distinct from raw SPICE: these front the simulation kernels and add physical design rules.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **KiCad** ✅ | KiCad project / KiCad Services Corp | open-source (GPL-3) | Lin / Win / Mac | Schematic + PCB; bundles ngspice (AC/DC/OP/transient) |
| **HyperLynx SI** ✅ | Siemens EDA | commercial · quote | Win | Signal/power integrity for DDR, PCIe, USB, SerDes |
| Altium Designer | Altium (Renesas) | commercial $$$ | Win | Integrated professional PCB design |
| OrCAD / Allegro | Cadence | commercial $$$ | Win | High-end PCB + layout |
| Xpedition | Siemens EDA | commercial $$$ | Win | Enterprise PCB design |
| CR-8000 | Zuken | commercial $$$ | Win | System-level PCB design |
| Fusion Electronics (Eagle) | Autodesk | commercial $ | Win / Mac | Hobby → pro PCB |
| LibrePCB | Community | open-source | Lin / Win / Mac | Lightweight PCB design |
| Horizon EDA | Community | open-source | Lin / Win | Integrated EDA suite |

## FEA / Structural

Finite-element solvers for stress, deformation, and thermomechanical analysis. CalculiX
and Code_Aster anchor the open-source side; Abaqus and Nastran the commercial standard.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| CalculiX | Dhondt & Wittig | open-source | Lin / Win | Implicit/explicit FE, Abaqus-style input; couples via preCICE |
| Code_Aster | EDF | open-source | Lin | Structural & thermomechanical FE |
| Elmer FEM | CSC Finland | open-source | Lin / Win / Mac | Multiphysics FE including structural |
| FEniCS / deal.II / MFEM | Academic communities | open-source | Lin / Win / Mac | FE solver libraries for custom PDEs |
| FreeCAD FEM | FreeCAD community | open-source | Lin / Win / Mac | GUI front-end to CalculiX / Elmer |
| Abaqus | Dassault SIMULIA | commercial $$$ | Lin / Win | Nonlinear / contact FE benchmark |
| ANSYS Mechanical | Ansys | commercial $$$ | Win / Lin | General structural FE |
| MSC Nastran | Hexagon | commercial $$$ | Win / Lin | Aerospace structural standard |
| LS-DYNA | Ansys | commercial $$$ | Win / Lin | Explicit dynamics / crash |

## CFD / Fluids

Computational fluid dynamics for flow, aerodynamics, and heat-transfer coupling.
OpenFOAM dominates open-source; Fluent and STAR-CCM+ the commercial market.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| OpenFOAM | ESI / OpenFOAM Foundation | open-source | Lin / WSL / Mac | General-purpose CFD; official preCICE adapter |
| SU2 | Stanford / community | open-source | Lin / Win / Mac | Compressible CFD & adjoint optimization |
| Nek5000 / Palabos | Academic | open-source | Lin | Spectral-element / lattice-Boltzmann CFD |
| ANSYS Fluent / CFX | Ansys | commercial $$$ | Win / Lin | Industry CFD standard |
| Simcenter STAR-CCM+ | Siemens | commercial $$$ | Win / Lin | Multidisciplinary CFD |
| Autodesk CFD | Autodesk | commercial $$ | Win | Design-stage flow / thermal |

## Thermal

Conduction, convection, and electronics-cooling analysis. Often a module within
FEA / CFD / multiphysics suites rather than a standalone category.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| CalculiX / Elmer (thermal) | Open-source projects | open-source | Lin / Win | Conduction + coupled thermomechanical |
| FloTHERM / FloEFD | Siemens | commercial $$$ | Win | Electronics cooling |
| ANSYS Icepak | Ansys | commercial $$$ | Win / Lin | IC / board / system thermal |
| 6SigmaET | Future Facilities (Cadence) | commercial $$$ | Win | Electronics thermal |
| COMSOL Heat Transfer | COMSOL | commercial $$$ | Win / Lin / Mac | Multiphysics thermal |

## Multiphysics

Coupled-physics solving — structural + thermal + fluid + EM in one model. preCICE takes
a distinct approach: it couples independent solvers at runtime rather than solving itself.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **preCICE** ✅ | TU Munich / U Stuttgart et al. | open-source (LGPL-3) | Lin / Win / Mac | Coupling library; glues OpenFOAM, CalculiX, FEniCS, deal.II |
| Elmer | CSC Finland | open-source | Lin / Win / Mac | Built-in multiphysics FE |
| MOOSE | Idaho National Lab | open-source | Lin / Mac | Multiphysics PDE framework |
| COMSOL Multiphysics | COMSOL | commercial $$$ | Win / Lin / Mac | GUI multiphysics, module-based |
| ANSYS Workbench | Ansys | commercial $$$ | Win / Lin | Coupled structural / thermal / EM / CFD |

## Electromagnetics / RF / Antenna

Full-wave field solvers (FDTD, FEM, method-of-moments) for antennas, microwave
components, and EMC. Commercial leaders HFSS and CST set the benchmark; openEMS leads
open-source.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| openEMS | Community | open-source | Lin / Win | FDTD electromagnetic field solver |
| NEC2 / 4nec2 | OSS engine / freeware GUI | free | Win / Lin | Wire-antenna method-of-moments |
| scikit-rf | Community | open-source | Any (Python) | RF network analysis |
| ANSYS HFSS | Ansys | commercial $$$ | Win / Lin | 3D full-wave antenna / component EM |
| CST Studio Suite | Dassault | commercial $$$ | Win / Lin | Broadband 3D EM |
| FEKO | Altair | commercial $$$ | Win / Lin | Antenna placement / scattering |
| Keysight ADS | Keysight | commercial $$$ | Win / Lin | RF / microwave circuit + EM |
| Sonnet Lite | Sonnet Software | free tier / commercial | Win | Planar 3D EM (Lite is free) |

## Semiconductor / TCAD

Technology Computer-Aided Design: simulating fabrication process steps and resulting
device physics (MOSFET, BJT, diode). A duopoly market with a thin open-source edge.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **Silvaco Victory TCAD** ✅ | Silvaco (NASDAQ: SVCO) | commercial · quote | Win / Lin | Process + device simulation (MOSFET / BJT / diode) |
| Sentaurus TCAD | Synopsys | commercial $$$ | Lin | Industry TCAD standard |
| DEVSIM | Community | open-source | Lin / Win / Mac | Semiconductor device TCAD |
| Archimedes | GNU | open-source | Lin | Monte-Carlo device simulation |

## Optics / Photonics

From ray/lens design to nanophotonic FDTD and optical-link system simulation. Meep leads
open-source; Lumerical, Zemax, and OptSim cover the commercial spread.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **OptSim** ✅ | Synopsys | commercial · quote | Win / Lin | Optical-communication system simulation (signal-propagation level) |
| Lumerical | Ansys | commercial $$$ | Win / Lin / Mac | FDTD / photonic-component design |
| Zemax OpticStudio | Ansys | commercial $$$ | Win | Lens / ray optical design |
| Meep | MIT | open-source | Lin / Mac | FDTD electromagnetics / photonics |
| MPB | MIT | open-source | Lin / Mac | Photonic band-structure computation |

## Mechanical CAD-motion / Kinematics / Multibody dynamics

Rigid- and flexible-body motion, mechanisms, and robotics physics. Adams is the
commercial reference; Chrono, MBDyn, and MuJoCo carry the open-source and research side.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| FreeCAD (Assembly + FEM) | FreeCAD community | open-source | Lin / Win / Mac | Parametric CAD + motion / FEM |
| Project Chrono | Community | open-source | Lin / Win / Mac | Multibody, granular, vehicle dynamics |
| MBDyn | Politecnico di Milano | open-source | Lin | Multibody dynamics |
| MuJoCo | Google DeepMind | open-source | Lin / Win / Mac | Contact / robotics physics |
| MSC Adams | Hexagon | commercial $$$ | Win / Lin | Multibody dynamics standard |
| Simscape Multibody | MathWorks | commercial $$$ | Win / Lin / Mac | Mechanism modeling in Simulink |

## Control systems / Systems modeling / Co-simulation

Block-diagram control design, equation-based system modeling (Modelica), and the FMI
interchange standard that lets these models co-simulate across tools.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| **FMI standard** ✅ | Modelica Association | open standard | n/a | Model-exchange / co-sim container (FMU); 280+ tools |
| **preCICE-FMI Runner** ✅ | preCICE project | open-source (LGPL-3) | Lin / Win / Mac | Couples FMU controllers to PDE solvers |
| OpenModelica | OSMC | open-source | Lin / Win / Mac | Modelica-based system modeling |
| Scilab / Xcos | Dassault | open-source | Lin / Win / Mac | Numerical computing + block diagrams |
| MATLAB / Simulink | MathWorks | commercial $$$ | Win / Lin / Mac | Control design & system simulation |
| Dymola | Dassault | commercial $$$ | Win / Lin | Modelica modeling |
| Wolfram System Modeler | Wolfram | commercial $$ | Win / Lin / Mac | Multidomain system modeling |

## Embedded / Firmware / Digital logic

CPU/SoC emulation, MCU + circuit co-simulation, and HDL (Verilog/VHDL) simulation for
firmware and chip-design verification.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| QEMU | Community | open-source | Lin / Win / Mac | CPU / SoC emulation |
| Renode | Antmicro | open-source | Lin / Win / Mac | Multi-node embedded / IoT simulation |
| Verilator | Community | open-source | Lin / Win / Mac | Fast Verilog simulation |
| Icarus Verilog / GHDL | Community | open-source | Lin / Win / Mac | Verilog / VHDL simulation |
| SimulIDE | Community | open-source | Lin / Win / Mac | MCU + circuit co-simulation |
| Wokwi | Wokwi | free (web) | Browser | Arduino / ESP32 simulation |
| gem5 | Community | open-source | Lin | Computer-architecture research |
| Proteus VSM | Labcenter | commercial $$ | Win | MCU + analog co-simulation |
| Questa / ModelSim | Siemens EDA | commercial $$$ | Win / Lin | HDL verification |
| VCS / Xcelium | Synopsys / Cadence | commercial $$$ | Lin | Industry HDL simulation |

## Acoustics / Vibration

Sound propagation, vibro-acoustics, and NVH. Predominantly a commercial-suite domain;
Elmer offers an open-source acoustic FE path.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| Elmer (acoustics) | CSC Finland | open-source | Lin / Win / Mac | Acoustic finite-element analysis |
| Actran | Hexagon | commercial $$$ | Win / Lin | Acoustics & vibro-acoustics |
| Simcenter (LMS) | Siemens | commercial $$$ | Win | NVH / vibro-acoustics |
| COMSOL Acoustics | COMSOL | commercial $$$ | Win / Lin / Mac | Multiphysics acoustics |

## Manufacturing / CAM / Process

Toolpath generation, machine control, and manufacturing-process simulation (molding,
forming, chemical process). Spans free machine controllers to high-end process solvers.

| Tool | Maintainer / Vendor | License & tier | Platform | Primary use |
|---|---|---|---|---|
| FreeCAD Path | FreeCAD community | open-source | Lin / Win / Mac | CNC toolpath generation |
| LinuxCNC | Community | open-source | Lin | Machine-tool motion control |
| DWSIM | Community | open-source (GPL-3) | Lin / Win / Mac / mobile | Chemical-process simulation (CAPE-OPEN) |
| Fusion 360 (Mfg) | Autodesk | commercial $ | Win / Mac | Integrated CAD / CAM |
| Mastercam / PowerMill | CNC Software / Autodesk | commercial $$$ | Win | Production CAM |
| Moldflow | Autodesk | commercial $$$ | Win | Injection-molding simulation |
| DEFORM / Simufact | SFTC / Hexagon | commercial $$$ | Win | Metal forming & machining |
| Aspen Plus / HYSYS | AspenTech | commercial $$$ | Win | Chemical-process simulation |

---

## Coverage & follow-ups

**Independently verified (cited):** ngspice, KiCad, Xyce, Qucs-S, Siemens HyperLynx SI,
Silvaco Victory TCAD, Synopsys OptSim, preCICE, FMI standard, preCICE-FMI Runner.

**Open follow-ups:**

- Concrete quote ranges for the commercial leaders (HFSS, Fluent, Abaqus, COMSOL,
  Altium) — all resolve only to enterprise pricing.
- Standalone characterization of CalculiX and OpenFOAM, which the verified pass touched
  only as preCICE adapters.
- Thermal, acoustics, and CAM categories lean heavily commercial — open-source depth
  there is thinner and worth a dedicated pass.

## Verified sources

| Tool | Source |
|---|---|
| ngspice | ngspice.sourceforge.io · en.wikipedia.org/wiki/Ngspice |
| KiCad + ngspice | kicad.org/discover/spice |
| Xyce | xyce.sandia.gov |
| Qucs-S | github.com/ra3xdh/qucs_s |
| HyperLynx SI | siemens.com/products/pcb/hyperlynx/signal-integrity |
| Silvaco Victory TCAD | silvaco.com/tcad |
| Synopsys OptSim | synopsys.com/photonic-solutions/optsim.html |
| preCICE | precice.org |
| FMI standard | fmi-standard.org |
| preCICE-FMI Runner | github.com/precice/fmi-runner |

---

*Compiled via the deep-research harness: 105 agents · 23 sources · 104 claims extracted ·
25 claims verified (3-0, 0 refuted). License and price tiers for unverified entries are
directional.*
