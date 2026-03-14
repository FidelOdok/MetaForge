# Drone Flight Controller Example

This example demonstrates MetaForge's end-to-end hardware design validation pipeline using a realistic drone flight controller as the reference project. It exercises the complete stack from human intent (PRD) through Digital Twin, domain agents, skills, MCP protocol, and tool adapters.

## Overview

The drone flight controller is a 4-layer PCB built around the STM32F405RGT6 MCU with an MPU6050 IMU, BMP280 barometer, GPS module, and motor driver FETs. This example shows how MetaForge validates the design across six engineering disciplines simultaneously, identifies supply chain risks, and tracks regulatory compliance.

## Prerequisites

- Python 3.11 or later
- MetaForge installed in development mode:

```bash
pip install -e ".[dev]"
```

No external tools (CalculiX, KiCad, FreeCAD) are required. The demos use mock tool adapters that return realistic responses, so you can run everything locally without Docker.

## Project Structure

```
examples/drone_flight_controller/
├── README.md                      # This file
├── demo_validate_stress.py        # Single-agent demo (mechanical stress validation)
├── demo_full_pipeline.py          # Full 6-agent pipeline demo (if available)
└── __init__.py
```

The demo simulates the user project structure that `forge setup` would create:

```
project/
├── PRD.md                         # Product Requirements Document
├── constraints.json               # Design rules (yield strength, thermal limits, etc.)
├── eda/kicad/                     # KiCad schematic and PCB files
│   ├── main.kicad_sch             # Top-level schematic
│   └── main.kicad_pcb             # PCB layout
├── bom/                           # Bill of Materials
│   └── bom.csv                    # Component list with distributor data
├── firmware/src/                  # Firmware source code
│   └── pinmap.json                # MCU pin assignments
├── manufacturing/                 # Manufacturing outputs
│   ├── gerbers/                   # Gerber files for PCB fabrication
│   └── pick_and_place.csv         # Assembly placement data
├── tests/bringup.md               # Hardware bring-up checklist
└── .forge/
    ├── sessions/                  # Agent session records
    └── traces/                    # Execution traces for debugging
```

## Running the Demos

### Quick Start: Single-Agent Stress Validation

This demo exercises the complete vertical stack for one agent (Mechanical):

```bash
python examples/drone_flight_controller/demo_validate_stress.py
```

Or as a module:

```bash
python -m examples.drone_flight_controller.demo_validate_stress
```

**What it does:**

1. Initializes a Digital Twin with a motor mount bracket CAD work_product
2. Starts a CalculiX tool adapter (mocked FEA solver)
3. Wires up the MCP protocol stack (client, transport, bridge)
4. Creates the Mechanical Agent
5. Runs stress validation for a hover 3g load case
6. Displays region-by-region stress results with pass/fail status
7. Updates the Twin with analysis results

### Full Pipeline: All Six Agents

If the full pipeline demo is available:

```bash
python examples/drone_flight_controller/demo_full_pipeline.py
```

This runs all six agents in sequence, coordinated by the orchestrator.

## What Each Agent Does

### 1. Mechanical Agent

**Role:** Validates structural integrity of physical components.

- Runs CalculiX FEA on the motor mount bracket
- Checks von Mises stress against Al6061-T6 yield strength (276 MPa)
- Applies a 1.5x safety factor
- Reports per-region stress results (bracket body, mounting holes, fillets)
- Flags regions exceeding allowable stress

### 2. Electronics Agent

**Role:** Validates schematic and PCB design rules.

- Runs Electrical Rules Check (ERC) on the KiCad schematic
- Runs Design Rules Check (DRC) on the PCB layout
- Checks for unconnected pins, missing power flags, clearance violations
- Reports error and warning counts with specific component references

### 3. Firmware Agent

**Role:** Generates hardware abstraction layer code.

- Produces HAL configuration for STM32F405 peripherals
- Maps I2C, SPI, UART, and GPIO pins from `pinmap.json`
- Generates driver scaffolds for IMU, barometer, GPS, and motor control
- Validates pin assignments against MCU datasheet constraints

### 4. Simulation Agent

**Role:** Runs thermal and environmental simulations.

- Performs thermal analysis of the PCB under operating conditions
- Checks junction temperatures against component ratings
- Identifies thermal hotspots near the MCU and power FETs
- Validates thermal relief patterns and copper pour adequacy

### 5. Supply Chain Agent

**Role:** Scores BOM risk and finds alternate parts.

- Evaluates each component across four risk dimensions:
  - **Source diversity**: Number of distributors with stock (single-source = high risk)
  - **Lifecycle status**: Active, NRND, EOL, or obsolete
  - **Stock levels**: Total available inventory across distributors
  - **Lead time**: Average weeks to delivery
- Produces a normalized 0-1 risk score per part and an overall BOM risk score
- Flags critical and high-risk parts (e.g., EOL components, single-source sensors)
- Suggests alternate parts from a knowledge base (pin-compatible or functional equivalents)

### 6. Compliance Agent

**Role:** Generates regulatory checklists and tracks evidence.

- Builds compliance checklists for selected regimes (UKCA, CE, FCC)
- Each checklist contains regime-specific requirements with standard references
- Tracks evidence work_products (test reports, certificates) linked to checklist items
- Computes coverage percentage: (items with accepted evidence) / (mandatory items)
- Supports multi-regime tracking (e.g., UKCA + CE + FCC = 23 checklist items)

## Understanding Results

### Stress Validation Output

```
Region               Stress (MPa)    Allowable       Status
--------------------------------------------------------------
bracket_body         85.3            184.0           PASS
bracket_mount        42.1            184.0           PASS
fillet_region        120.7           184.0           PASS
```

- **Stress (MPa)**: Von Mises stress computed by FEA
- **Allowable (MPa)**: Yield strength / safety factor (276 / 1.5 = 184 MPa)
- **Status**: PASS if stress is below allowable, FAIL otherwise

### BOM Risk Scores

Each part receives a risk score from 0.0 (no risk) to 1.0 (maximum risk):

| Risk Level | Score Range | Meaning |
|-----------|-------------|---------|
| Low       | 0.00 - 0.24 | Multi-source, active, adequate stock |
| Medium    | 0.25 - 0.49 | Some supply concern (dual source, NRND, etc.) |
| High      | 0.50 - 0.74 | Significant risk (single source, EOL, low stock) |
| Critical  | 0.75 - 1.00 | Immediate action needed (obsolete, zero stock) |

### Compliance Coverage

Coverage is computed as a percentage of mandatory checklist items that have accepted evidence:

- **0%**: No evidence submitted yet
- **50%**: Half the mandatory items have accepted evidence
- **100%**: All mandatory items covered, ready for gate review

## EVT Gate Readiness

The EVT (Engineering Validation Test) gate readiness check combines signals from multiple agents into a single score:

```
Gate Readiness = (BOM Weight * BOM Score) + (Compliance Weight * Compliance Coverage)
```

Where:
- **BOM Score** = (1 - overall_risk_score) * 100
- **Compliance Coverage** = percentage of mandatory items with accepted evidence
- Default weights: BOM 40%, Compliance 60%

A gate readiness score above 80 typically indicates the design is ready for EVT review. Scores below 50 indicate significant blockers that must be resolved.

**What reduces readiness:**
- Critical or high-risk BOM parts (EOL components, single-source parts)
- Low compliance evidence coverage
- Failed stress analysis or ERC/DRC violations

## Extending the Example

### Adding Custom Constraints

Edit the `constraints` list in the demo script to add stress constraints:

```python
result = await agent.run_task(
    TaskRequest(
        task_type="validate_stress",
        work_product_id=work_product.id,
        parameters={
            "mesh_file_path": "models/your_part.inp",
            "load_case": "max_thrust",
            "constraints": [
                {
                    "max_von_mises_mpa": 500.0,  # Ti-6Al-4V yield
                    "safety_factor": 2.0,
                    "material": "Ti-6Al-4V",
                }
            ],
        },
    )
)
```

### Modifying the BOM

Create `BOMEntry` objects with real distributor data:

```python
from domain_agents.supply_chain.models import BOMEntry, DistributorInfo, LifecycleStatus
from domain_agents.supply_chain.risk_scorer import BOMRiskScorer

bom = [
    BOMEntry(
        part_number="YOUR-PART-001",
        manufacturer="Acme Corp",
        lifecycle=LifecycleStatus.ACTIVE,
        distributors=[
            DistributorInfo(name="Digi-Key", in_stock=True, stock_quantity=5000,
                           lead_time_weeks=2, unit_price_usd=1.50),
        ],
    ),
]
scorer = BOMRiskScorer()
report = scorer.score_bom(bom)
```

### Adding a Compliance Regime

The `ChecklistGenerator` accepts custom templates:

```python
from domain_agents.compliance.checklist_generator import ChecklistGenerator
from domain_agents.compliance.models import ComplianceRegime

gen = ChecklistGenerator()
checklist = gen.generate([ComplianceRegime.UKCA, ComplianceRegime.CE, ComplianceRegime.FCC])
```

To add a new regime, extend the `_REGIME_TEMPLATES` dictionary in `checklist_generator.py` with the regime's requirements.

## Architecture: Data Flow

```
Human Intent (PRD.md, constraints.json)
         |
    CLI (forge run)
         |
    Gateway Service (HTTP/WebSocket)
         |
    Orchestrator (DAG execution, agent coordination)
         |
    +------------------+------------------+------------------+
    |                  |                  |                  |
  Mechanical       Electronics       Firmware          Simulation
  Agent            Agent             Agent             Agent
    |                  |                  |                  |
  validate_stress   run_erc          generate_hal     thermal_analysis
  (Skill)           run_drc          (Skill)           (Skill)
    |                (Skills)             |                  |
    |                  |                  |                  |
    +--------+---------+--------+--------+--------+---------+
             |                           |
         MCP Protocol Layer          MCP Protocol Layer
             |                           |
         CalculiX              KiCad         FreeCAD
         (FEA)                 (ERC/DRC)     (CAD)
             |                           |
             +-------------+-------------+
                           |
                    Digital Twin
                 (work_product graph —
              single source of truth)
                           |
         +-----------------+-----------------+
         |                                   |
    Supply Chain Agent              Compliance Agent
         |                                   |
    BOM risk scoring               Checklist generation
    Alternate parts                Evidence tracking
```

All agents read from and write to the Digital Twin. No agent calls tools directly -- all tool access goes through the MCP protocol layer. This ensures every action is traceable, auditable, and reproducible.
