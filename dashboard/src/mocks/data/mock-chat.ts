/**
 * Mock chat data for standalone dashboard development.
 *
 * Covers all five scope kinds: session, approval, bom-entry,
 * digital-twin-node, and project. Messages include realistic engineering
 * conversations with markdown formatting and Digital Twin graph references.
 */

import type {
  ChatMessage,
  ChatThread,
  ChatChannel,
  ChatActor,
} from '../../types/chat';

// ---------------------------------------------------------------------------
// Reusable actor identities
// ---------------------------------------------------------------------------

export const actors: Record<string, ChatActor> = {
  userAlex: {
    id: 'user-1',
    kind: 'user',
    displayName: 'Alex Chen',
  },
  userMaria: {
    id: 'user-2',
    kind: 'user',
    displayName: 'Maria Torres',
  },
  agentME: {
    id: 'agent-me',
    kind: 'agent',
    displayName: 'Mechanical Agent',
    agentCode: 'ME',
  },
  agentEE: {
    id: 'agent-ee',
    kind: 'agent',
    displayName: 'Electronics Agent',
    agentCode: 'EE',
  },
  agentFW: {
    id: 'agent-fw',
    kind: 'agent',
    displayName: 'Firmware Agent',
    agentCode: 'FW',
  },
  agentSE: {
    id: 'agent-se',
    kind: 'agent',
    displayName: 'Systems Agent',
    agentCode: 'SE',
  },
  agentSIM: {
    id: 'agent-sim',
    kind: 'agent',
    displayName: 'Simulation Agent',
    agentCode: 'SIM',
  },
  system: {
    id: 'system',
    kind: 'system',
    displayName: 'MetaForge',
  },
};

// ---------------------------------------------------------------------------
// Channels
// ---------------------------------------------------------------------------

export const mockChannels: ChatChannel[] = [
  {
    id: 'ch-1',
    name: 'General',
    scopeKind: 'project',
    unreadCount: 3,
  },
  {
    id: 'ch-2',
    name: 'Mechanical Review',
    scopeKind: 'session',
    unreadCount: 1,
  },
  {
    id: 'ch-3',
    name: 'Approvals',
    scopeKind: 'approval',
    unreadCount: 5,
  },
  {
    id: 'ch-4',
    name: 'BOM Tracking',
    scopeKind: 'bom-entry',
    unreadCount: 0,
  },
  {
    id: 'ch-5',
    name: 'Twin Updates',
    scopeKind: 'digital-twin-node',
    unreadCount: 2,
  },
];

// ---------------------------------------------------------------------------
// Threads
// ---------------------------------------------------------------------------

export const mockThreads: ChatThread[] = [
  // --- approval ---
  {
    id: 'th-1',
    channelId: 'ch-3',
    scope: { kind: 'approval', entityId: 'approval-001', label: 'PCB Layout v2.1' },
    title: 'PCB Layout Review',
    messages: [], // populated below via mockMessages
    participants: [actors.userAlex, actors.agentEE, actors.system],
    createdAt: '2026-03-01T10:30:00Z',
    lastMessageAt: '2026-03-01T11:45:00Z',
    archived: false,
  },
  // --- session ---
  {
    id: 'th-2',
    channelId: 'ch-2',
    scope: { kind: 'session', entityId: 'session-042', label: 'FEA Run #42' },
    title: 'Bracket Stress Analysis',
    messages: [],
    participants: [actors.userAlex, actors.agentME, actors.agentSIM],
    createdAt: '2026-03-01T14:00:00Z',
    lastMessageAt: '2026-03-01T15:20:00Z',
    archived: false,
  },
  // --- bom-entry ---
  {
    id: 'th-3',
    channelId: 'ch-4',
    scope: { kind: 'bom-entry', entityId: 'bom-entry-stm32', label: 'STM32F405RGT6' },
    title: 'MCU Sourcing Alternatives',
    messages: [],
    participants: [actors.userMaria, actors.agentEE, actors.agentSE],
    createdAt: '2026-03-02T09:00:00Z',
    lastMessageAt: '2026-03-02T10:15:00Z',
    archived: false,
  },
  // --- digital-twin-node ---
  {
    id: 'th-4',
    channelId: 'ch-5',
    scope: { kind: 'digital-twin-node', entityId: 'node-chassis-asm', label: 'Chassis Assembly' },
    title: 'Chassis Material Change',
    messages: [],
    participants: [actors.userAlex, actors.agentME, actors.system],
    createdAt: '2026-03-02T11:00:00Z',
    lastMessageAt: '2026-03-02T12:30:00Z',
    archived: false,
  },
  // --- project ---
  {
    id: 'th-5',
    channelId: 'ch-1',
    scope: { kind: 'project', entityId: 'proj-drone-fc', label: 'Drone FC v1' },
    title: 'Sprint Planning - Week 10',
    messages: [],
    participants: [actors.userAlex, actors.userMaria, actors.system],
    createdAt: '2026-03-03T08:00:00Z',
    lastMessageAt: '2026-03-03T09:00:00Z',
    archived: false,
  },
  // --- approval (second) ---
  {
    id: 'th-6',
    channelId: 'ch-3',
    scope: { kind: 'approval', entityId: 'approval-002', label: 'Firmware pinmap v3' },
    title: 'Firmware Pin Assignment Review',
    messages: [],
    participants: [actors.userMaria, actors.agentFW, actors.agentEE],
    createdAt: '2026-03-02T13:00:00Z',
    lastMessageAt: '2026-03-02T14:30:00Z',
    archived: false,
  },
  // --- digital-twin-node (second) ---
  {
    id: 'th-7',
    channelId: 'ch-5',
    scope: { kind: 'digital-twin-node', entityId: 'node-motor-drv', label: 'Motor Driver Circuit' },
    title: 'Motor Driver Thermal Validation',
    messages: [],
    participants: [actors.userAlex, actors.agentEE, actors.agentSIM],
    createdAt: '2026-03-02T15:00:00Z',
    lastMessageAt: '2026-03-02T16:45:00Z',
    archived: false,
  },
  // --- session (second) ---
  {
    id: 'th-8',
    channelId: 'ch-2',
    scope: { kind: 'session', entityId: 'session-043', label: 'DRC Session #43' },
    title: 'Power Board DRC Results',
    messages: [],
    participants: [actors.userMaria, actors.agentEE, actors.system],
    createdAt: '2026-03-03T10:00:00Z',
    lastMessageAt: '2026-03-03T11:30:00Z',
    archived: false,
  },
  // --- bom-entry (second) ---
  {
    id: 'th-9',
    channelId: 'ch-4',
    scope: { kind: 'bom-entry', entityId: 'bom-entry-ldo', label: 'AP2112K-3.3 LDO' },
    title: 'LDO Thermal Budget Discussion',
    messages: [],
    participants: [actors.userAlex, actors.agentEE, actors.agentME],
    createdAt: '2026-03-03T13:00:00Z',
    lastMessageAt: '2026-03-03T14:20:00Z',
    archived: false,
  },
  // --- project (archived) ---
  {
    id: 'th-10',
    channelId: 'ch-1',
    scope: { kind: 'project', entityId: 'proj-drone-fc', label: 'Drone FC v1' },
    title: 'Initial Requirements Sync',
    messages: [],
    participants: [actors.userAlex, actors.userMaria, actors.agentSE],
    createdAt: '2026-02-20T09:00:00Z',
    lastMessageAt: '2026-02-20T10:30:00Z',
    archived: true,
  },
];

// ---------------------------------------------------------------------------
// Messages (keyed by thread ID)
// ---------------------------------------------------------------------------

export const mockMessages: Record<string, ChatMessage[]> = {
  // ----- th-1: PCB Layout Review (approval) -----
  'th-1': [
    {
      id: 'msg-1-1',
      threadId: 'th-1',
      actor: actors.userAlex,
      content: 'Can you review the PCB layout for the power section?',
      status: 'sent',
      createdAt: '2026-03-01T10:30:00Z',
      updatedAt: '2026-03-01T10:30:00Z',
    },
    {
      id: 'msg-1-2',
      threadId: 'th-1',
      actor: actors.agentEE,
      content:
        "I've reviewed the power section layout. Found 2 issues:\n\n" +
        '1. **Trace width**: The 12V power trace is 10mil — should be at least 20mil for 2A current capacity per IPC-2221.\n' +
        '2. **Decoupling caps**: C12 and C15 are placed 8mm from the IC — recommend <=3mm for proper high-frequency decoupling.\n\n' +
        'Would you like me to run a full DRC check?',
      status: 'sent',
      createdAt: '2026-03-01T10:32:00Z',
      updatedAt: '2026-03-01T10:32:00Z',
      graphRef: {
        nodeId: 'work_product-pcb-001',
        nodeType: 'work_product',
        label: 'main-pcb.kicad_pcb',
      },
    },
    {
      id: 'msg-1-3',
      threadId: 'th-1',
      actor: actors.userAlex,
      content: 'Yes, please run the full DRC. Also flag any clearance violations near the mounting holes.',
      status: 'sent',
      createdAt: '2026-03-01T10:35:00Z',
      updatedAt: '2026-03-01T10:35:00Z',
    },
    {
      id: 'msg-1-4',
      threadId: 'th-1',
      actor: actors.system,
      content: 'DRC check initiated. Estimated completion: ~30 seconds.',
      status: 'sent',
      createdAt: '2026-03-01T10:35:30Z',
    },
    {
      id: 'msg-1-5',
      threadId: 'th-1',
      actor: actors.agentEE,
      content:
        'DRC complete. **14 violations** found:\n\n' +
        '| Category | Count | Severity |\n' +
        '|---|---|---|\n' +
        '| Trace clearance | 6 | Error |\n' +
        '| Copper-to-edge | 3 | Error |\n' +
        '| Silkscreen overlap | 4 | Warning |\n' +
        '| Unconnected net | 1 | Error |\n\n' +
        'The copper-to-edge violations are near mounting holes M1 and M3 — the keepout zone is set to 0.2mm but the design rule requires 0.5mm.\n\n' +
        '```\nM1: clearance = 0.18mm (min 0.50mm) FAIL\nM3: clearance = 0.22mm (min 0.50mm) FAIL\nM4: clearance = 0.55mm (min 0.50mm) PASS\n```',
      status: 'sent',
      createdAt: '2026-03-01T10:36:00Z',
      updatedAt: '2026-03-01T10:36:00Z',
      graphRef: {
        nodeId: 'work_product-pcb-001',
        nodeType: 'work_product',
        label: 'main-pcb.kicad_pcb',
      },
    },
  ],

  // ----- th-2: Bracket Stress Analysis (session) -----
  'th-2': [
    {
      id: 'msg-2-1',
      threadId: 'th-2',
      actor: actors.userAlex,
      content: 'Run stress analysis on the motor mounting bracket with 50N load on each mounting point.',
      status: 'sent',
      createdAt: '2026-03-01T14:00:00Z',
      updatedAt: '2026-03-01T14:00:00Z',
    },
    {
      id: 'msg-2-2',
      threadId: 'th-2',
      actor: actors.system,
      content: 'CalculiX FEA job submitted. Mesh generation in progress (tetrahedral, 2mm element size).',
      status: 'sent',
      createdAt: '2026-03-01T14:00:15Z',
    },
    {
      id: 'msg-2-3',
      threadId: 'th-2',
      actor: actors.agentME,
      content:
        'FEA analysis complete. Results summary:\n\n' +
        '- **Max von Mises stress**: 142 MPa (at fillet near bolt hole #2)\n' +
        '- **Yield strength (6061-T6)**: 276 MPa\n' +
        '- **Safety factor**: 1.94\n' +
        '- **Max displacement**: 0.23mm (tip of bracket arm)\n\n' +
        'The design meets the minimum safety factor of 1.5. However, the stress concentration at the fillet could be reduced by increasing the fillet radius from 1mm to 2mm.\n\n' +
        '> Recommendation: Increase fillet radius to 2mm. This would reduce peak stress to ~105 MPa (SF = 2.63).',
      status: 'sent',
      createdAt: '2026-03-01T14:05:00Z',
      updatedAt: '2026-03-01T14:05:00Z',
      graphRef: {
        nodeId: 'work_product-bracket-001',
        nodeType: 'work_product',
        label: 'motor-bracket.step',
      },
    },
    {
      id: 'msg-2-4',
      threadId: 'th-2',
      actor: actors.userAlex,
      content: 'Good. Apply the 2mm fillet change and re-run. Also check if the bolt pattern can handle the shear loads.',
      status: 'sent',
      createdAt: '2026-03-01T14:10:00Z',
      updatedAt: '2026-03-01T14:10:00Z',
    },
    {
      id: 'msg-2-5',
      threadId: 'th-2',
      actor: actors.agentME,
      content:
        'Updated model re-analyzed:\n\n' +
        '- **Peak stress (fillet)**: 108 MPa -- down from 142 MPa\n' +
        '- **Safety factor**: 2.56\n' +
        '- **Bolt shear check** (M3 x 4 bolts, Grade 8.8):\n' +
        '  - Applied shear per bolt: 12.5N\n' +
        '  - Allowable shear per bolt: 3,040N\n' +
        '  - **Bolt SF: 243** -- vastly overdesigned for this load case\n\n' +
        'The bracket passes all checks. Shall I update the Digital Twin with the revised geometry?',
      status: 'sent',
      createdAt: '2026-03-01T15:20:00Z',
      updatedAt: '2026-03-01T15:20:00Z',
      graphRef: {
        nodeId: 'work_product-bracket-001',
        nodeType: 'work_product',
        label: 'motor-bracket.step',
      },
    },
  ],

  // ----- th-3: MCU Sourcing Alternatives (bom-entry) -----
  'th-3': [
    {
      id: 'msg-3-1',
      threadId: 'th-3',
      actor: actors.userMaria,
      content: 'The STM32F405RGT6 is showing 26-week lead times on DigiKey. Can you find pin-compatible alternatives?',
      status: 'sent',
      createdAt: '2026-03-02T09:00:00Z',
      updatedAt: '2026-03-02T09:00:00Z',
    },
    {
      id: 'msg-3-2',
      threadId: 'th-3',
      actor: actors.agentEE,
      content:
        'Found 3 alternatives with compatible pinout (LQFP-64):\n\n' +
        '| Part | Flash | RAM | Price (1k) | Lead Time | Notes |\n' +
        '|---|---|---|---|---|---|\n' +
        '| **STM32F446RET6** | 512KB | 128KB | $4.82 | 8 weeks | Drop-in, higher clock |\n' +
        '| **STM32F407VGT6** | 1MB | 192KB | $6.15 | 4 weeks | Extra GPIO, LQFP-100 needs adapter |\n' +
        '| **GD32F405RGT6** | 1MB | 192KB | $3.20 | 2 weeks | GigaDevice clone, HAL differences |\n\n' +
        'The STM32F446RET6 is the safest swap -- same footprint, same HAL, and the firmware only needs a clock config change.',
      status: 'sent',
      createdAt: '2026-03-02T09:05:00Z',
      updatedAt: '2026-03-02T09:05:00Z',
      graphRef: {
        nodeId: 'bom-entry-stm32',
        nodeType: 'bom-entry',
        label: 'STM32F405RGT6',
      },
    },
    {
      id: 'msg-3-3',
      threadId: 'th-3',
      actor: actors.agentSE,
      content:
        'Cross-checking system constraints:\n\n' +
        '- **Flash usage**: Current firmware build is 380KB. All three alternatives meet the requirement.\n' +
        '- **Peripheral requirements**: SPI x2, I2C x1, UART x3, CAN x1, USB OTG -- all three have these.\n' +
        '- **Power budget**: The F446 draws ~93mA at 180MHz vs 87mA for the F405 at 168MHz. Within the 150mA MCU budget.\n\n' +
        'No system-level blockers for the F446 swap.',
      status: 'sent',
      createdAt: '2026-03-02T09:10:00Z',
      updatedAt: '2026-03-02T09:10:00Z',
    },
    {
      id: 'msg-3-4',
      threadId: 'th-3',
      actor: actors.userMaria,
      content: 'Go with the F446. Update the BOM and flag the firmware team about the clock config change.',
      status: 'sent',
      createdAt: '2026-03-02T10:15:00Z',
      updatedAt: '2026-03-02T10:15:00Z',
    },
  ],

  // ----- th-4: Chassis Material Change (digital-twin-node) -----
  'th-4': [
    {
      id: 'msg-4-1',
      threadId: 'th-4',
      actor: actors.userAlex,
      content: 'We need to switch the chassis from AL6061 to carbon fiber composite to save weight. What are the implications?',
      status: 'sent',
      createdAt: '2026-03-02T11:00:00Z',
      updatedAt: '2026-03-02T11:00:00Z',
    },
    {
      id: 'msg-4-2',
      threadId: 'th-4',
      actor: actors.agentME,
      content:
        'Material change impact analysis for chassis assembly:\n\n' +
        '**Weight**\n' +
        '- Current (AL6061): 340g\n' +
        '- Proposed (CF T300/epoxy, 2mm layup): ~185g\n' +
        '- **Savings: 155g (45.6%)**\n\n' +
        '**Structural**\n' +
        '- Tensile strength: 276 MPa (AL) vs 530 MPa (CF, 0-degree)\n' +
        '- Concern: CF is anisotropic. The current mounting boss design assumes isotropic material.\n' +
        '- Fastener holes in CF need metal inserts to prevent bearing failure.\n\n' +
        '**Thermal**\n' +
        '- CF thermal conductivity: ~7 W/mK vs 167 W/mK for AL6061\n' +
        '- The motor driver heatsink path relies on chassis conduction. This **breaks** the current thermal design.\n\n' +
        '**Manufacturing**\n' +
        '- CNC milling replaced with layup + autoclave cure\n' +
        '- Estimated cost increase: +$18/unit at 1k volume\n' +
        '- Lead time increase: +2 weeks for tooling',
      status: 'sent',
      createdAt: '2026-03-02T11:10:00Z',
      updatedAt: '2026-03-02T11:10:00Z',
      graphRef: {
        nodeId: 'node-chassis-asm',
        nodeType: 'assembly',
        label: 'Chassis Assembly',
      },
    },
    {
      id: 'msg-4-3',
      threadId: 'th-4',
      actor: actors.system,
      content: 'Constraint violation detected: Thermal path from motor driver to chassis no longer meets Rth <= 5 K/W requirement (estimated 22 K/W with CF).',
      status: 'sent',
      createdAt: '2026-03-02T11:11:00Z',
    },
    {
      id: 'msg-4-4',
      threadId: 'th-4',
      actor: actors.userAlex,
      content: 'Can we add an aluminum thermal bridge plate under the motor driver area and keep the rest CF?',
      status: 'sent',
      createdAt: '2026-03-02T12:00:00Z',
      updatedAt: '2026-03-02T12:00:00Z',
    },
    {
      id: 'msg-4-5',
      threadId: 'th-4',
      actor: actors.agentME,
      content:
        'Hybrid approach analysis:\n\n' +
        '- AL6061 thermal bridge plate (60x40x3mm): +28g\n' +
        '- Net weight savings: 127g (37.4%) -- still significant\n' +
        '- Estimated Rth with bridge: 3.8 K/W -- **passes** the 5 K/W constraint\n' +
        '- Requires 4x M2.5 threaded inserts in the CF panel\n\n' +
        'This approach satisfies both the weight goal and the thermal constraint. Shall I update the Twin?',
      status: 'sent',
      createdAt: '2026-03-02T12:30:00Z',
      updatedAt: '2026-03-02T12:30:00Z',
      graphRef: {
        nodeId: 'node-chassis-asm',
        nodeType: 'assembly',
        label: 'Chassis Assembly',
      },
    },
  ],

  // ----- th-5: Sprint Planning (project) -----
  'th-5': [
    {
      id: 'msg-5-1',
      threadId: 'th-5',
      actor: actors.system,
      content: 'Sprint 10 started. 14 issues assigned, 42 story points.',
      status: 'sent',
      createdAt: '2026-03-03T08:00:00Z',
    },
    {
      id: 'msg-5-2',
      threadId: 'th-5',
      actor: actors.userAlex,
      content: 'The motor driver thermal issue from yesterday is a blocker. We need to reprioritize the chassis redesign.',
      status: 'sent',
      createdAt: '2026-03-03T08:10:00Z',
      updatedAt: '2026-03-03T08:10:00Z',
    },
    {
      id: 'msg-5-3',
      threadId: 'th-5',
      actor: actors.userMaria,
      content: 'Agreed. I will move MET-52 (chassis thermal validation) to the top of the sprint. The BOM update for the MCU swap can go next.',
      status: 'sent',
      createdAt: '2026-03-03T08:30:00Z',
      updatedAt: '2026-03-03T08:30:00Z',
    },
    {
      id: 'msg-5-4',
      threadId: 'th-5',
      actor: actors.system,
      content: 'Sprint backlog updated. MET-52 moved to priority 1 (Urgent).',
      status: 'sent',
      createdAt: '2026-03-03T09:00:00Z',
    },
  ],

  // ----- th-6: Firmware Pin Assignment Review (approval) -----
  'th-6': [
    {
      id: 'msg-6-1',
      threadId: 'th-6',
      actor: actors.userMaria,
      content: 'Submitting the updated pinmap for review. Key changes:\n- Moved SPI2_MOSI from PB15 to PC3 to avoid conflict with TIM1_CH3N\n- Added UART4 on PA0/PA1 for telemetry radio',
      status: 'sent',
      createdAt: '2026-03-02T13:00:00Z',
      updatedAt: '2026-03-02T13:00:00Z',
    },
    {
      id: 'msg-6-2',
      threadId: 'th-6',
      actor: actors.agentFW,
      content:
        'Pin assignment validation results:\n\n' +
        '**Conflicts**: None detected\n\n' +
        '**Warnings**:\n' +
        '- PA0 (UART4_TX) is also ADC1_IN0. Verify ADC is not needed on this pin.\n' +
        '- PC3 (SPI2_MOSI) shares AF5 with I2S2_SD. Confirm I2S2 is unused.\n\n' +
        '**Peripheral check**:\n' +
        '```\nSPI1  : PA5/PA6/PA7         OK (IMU)\nSPI2  : PB13/PB14/PC3       OK (Flash) \nI2C1  : PB6/PB7              OK (Barometer)\nUART1 : PA9/PA10             OK (Debug)\nUART4 : PA0/PA1              OK (Telemetry)\nCAN1  : PB8/PB9              OK (ESC bus)\nUSB   : PA11/PA12            OK (DFU)\n```\n\nAll peripheral assignments verified. No DMA channel conflicts.',
      status: 'sent',
      createdAt: '2026-03-02T13:10:00Z',
      updatedAt: '2026-03-02T13:10:00Z',
      graphRef: {
        nodeId: 'work_product-pinmap-003',
        nodeType: 'work_product',
        label: 'pinmap.json',
      },
    },
    {
      id: 'msg-6-3',
      threadId: 'th-6',
      actor: actors.agentEE,
      content:
        'Cross-checking against the schematic:\n\n' +
        '- The PC3 move requires updating the SPI2 net on sheet 3 of the schematic.\n' +
        '- UART4 routing on PA0/PA1 adds two traces near the analog section. Recommend a ground guard trace to prevent noise coupling.\n\n' +
        'No schematic-level blockers.',
      status: 'sent',
      createdAt: '2026-03-02T13:15:00Z',
      updatedAt: '2026-03-02T13:15:00Z',
      graphRef: {
        nodeId: 'work_product-sch-001',
        nodeType: 'work_product',
        label: 'drone-fc.kicad_sch',
      },
    },
    {
      id: 'msg-6-4',
      threadId: 'th-6',
      actor: actors.system,
      content: 'Approval gate: 2/2 agent reviews complete. Awaiting human approval.',
      status: 'sent',
      createdAt: '2026-03-02T13:16:00Z',
    },
    {
      id: 'msg-6-5',
      threadId: 'th-6',
      actor: actors.userMaria,
      content: 'ADC1_IN0 is not used in this design -- we read analog sensors via the dedicated ADC channels on Port C. Approved. Apply the changes.',
      status: 'sent',
      createdAt: '2026-03-02T14:30:00Z',
      updatedAt: '2026-03-02T14:30:00Z',
    },
  ],

  // ----- th-7: Motor Driver Thermal Validation (digital-twin-node) -----
  'th-7': [
    {
      id: 'msg-7-1',
      threadId: 'th-7',
      actor: actors.userAlex,
      content: 'Run thermal simulation on the DRV8302 motor driver with 4A continuous per phase. The ambient temp spec is 40C.',
      status: 'sent',
      createdAt: '2026-03-02T15:00:00Z',
      updatedAt: '2026-03-02T15:00:00Z',
    },
    {
      id: 'msg-7-2',
      threadId: 'th-7',
      actor: actors.agentSIM,
      content:
        'Thermal simulation complete (steady-state, natural convection + conduction to chassis):\n\n' +
        '- **Junction temperature**: 127C\n' +
        '- **Absolute max (DRV8302)**: 150C\n' +
        '- **Thermal margin**: 23C\n' +
        '- **MOSFET case temp**: 98C\n' +
        '- **PCB hotspot (via array)**: 85C\n\n' +
        'The design passes but the 23C margin is tight. At 50C ambient (derating scenario), the junction would reach **137C** -- only 13C margin.\n\n' +
        'Recommendations:\n' +
        '1. Add thermal vias under the exposed pad (current: 9 vias, recommend: 16)\n' +
        '2. Increase copper pour on bottom layer from 1oz to 2oz\n' +
        '3. Consider adding a small heatsink (Fischer SK 576 or similar)',
      status: 'sent',
      createdAt: '2026-03-02T15:20:00Z',
      updatedAt: '2026-03-02T15:20:00Z',
      graphRef: {
        nodeId: 'node-motor-drv',
        nodeType: 'component',
        label: 'DRV8302 Motor Driver',
      },
    },
    {
      id: 'msg-7-3',
      threadId: 'th-7',
      actor: actors.agentEE,
      content:
        'Adding to the thermal analysis -- the via array change impacts the PCB layout:\n\n' +
        '- 16 vias (0.3mm drill, 0.6mm pad) fit within the 5x5mm exposed pad\n' +
        '- No routing conflicts on inner layers\n' +
        '- Estimated Rth improvement: from 42 K/W down to 28 K/W\n\n' +
        'With this change, junction temp at 50C ambient drops to ~118C (32C margin).',
      status: 'sent',
      createdAt: '2026-03-02T15:35:00Z',
      updatedAt: '2026-03-02T15:35:00Z',
    },
    {
      id: 'msg-7-4',
      threadId: 'th-7',
      actor: actors.userAlex,
      content: 'Apply the 16-via change and 2oz bottom copper. Skip the external heatsink for now -- we want to keep the form factor slim.',
      status: 'sent',
      createdAt: '2026-03-02T16:45:00Z',
      updatedAt: '2026-03-02T16:45:00Z',
    },
  ],

  // ----- th-8: Power Board DRC Results (session) -----
  'th-8': [
    {
      id: 'msg-8-1',
      threadId: 'th-8',
      actor: actors.system,
      content: 'DRC session #43 initiated on `power-board-v2.kicad_pcb`. Rule set: IPC-2221 Class 2.',
      status: 'sent',
      createdAt: '2026-03-03T10:00:00Z',
    },
    {
      id: 'msg-8-2',
      threadId: 'th-8',
      actor: actors.agentEE,
      content:
        'DRC completed. **3 errors, 7 warnings**.\n\n' +
        '**Errors:**\n' +
        '1. `U3 pin 14` (VDD) -- missing decoupling capacitor within 3mm\n' +
        '2. `J2 pin 1` (VIN) -- trace width 8mil for 3A path (min 24mil per IPC-2221)\n' +
        '3. `R15/R16` -- 0402 pads overlap (center-to-center: 0.4mm, min: 0.65mm)\n\n' +
        '**Warnings:**\n' +
        '- 4x silkscreen overlap on bottom layer\n' +
        '- 2x courtyard violation (C8/C9 overlap by 0.05mm)\n' +
        '- 1x unrouted net: `NET_SENSE_GND`',
      status: 'sent',
      createdAt: '2026-03-03T10:05:00Z',
      updatedAt: '2026-03-03T10:05:00Z',
      graphRef: {
        nodeId: 'work_product-pcb-002',
        nodeType: 'work_product',
        label: 'power-board-v2.kicad_pcb',
      },
    },
    {
      id: 'msg-8-3',
      threadId: 'th-8',
      actor: actors.userMaria,
      content: 'Fix errors 1 and 2 automatically. For error 3, move R16 0.3mm to the right and re-check.',
      status: 'sent',
      createdAt: '2026-03-03T10:30:00Z',
      updatedAt: '2026-03-03T10:30:00Z',
    },
    {
      id: 'msg-8-4',
      threadId: 'th-8',
      actor: actors.agentEE,
      content:
        'Fixes applied:\n\n' +
        '- Added 100nF MLCC (C20) within 1.5mm of U3 pin 14\n' +
        '- Widened J2-VIN trace to 30mil (supports 3.8A per IPC-2221 with 10C rise)\n' +
        '- R16 moved 0.3mm right -- pad spacing now 0.7mm (passes)\n\n' +
        'Re-running DRC... **0 errors, 5 warnings** (remaining silkscreen and courtyard issues).',
      status: 'sent',
      createdAt: '2026-03-03T11:30:00Z',
      updatedAt: '2026-03-03T11:30:00Z',
      graphRef: {
        nodeId: 'work_product-pcb-002',
        nodeType: 'work_product',
        label: 'power-board-v2.kicad_pcb',
      },
    },
  ],

  // ----- th-9: LDO Thermal Budget (bom-entry) -----
  'th-9': [
    {
      id: 'msg-9-1',
      threadId: 'th-9',
      actor: actors.userAlex,
      content: 'The AP2112K-3.3 is dissipating more heat than expected. Can you run the thermal numbers for the worst case?',
      status: 'sent',
      createdAt: '2026-03-03T13:00:00Z',
      updatedAt: '2026-03-03T13:00:00Z',
    },
    {
      id: 'msg-9-2',
      threadId: 'th-9',
      actor: actors.agentEE,
      content:
        'AP2112K-3.3 thermal analysis (worst case):\n\n' +
        '- **Input**: 5.0V (USB)\n' +
        '- **Output**: 3.3V @ 450mA (measured peak from firmware telemetry)\n' +
        '- **Dropout**: V_in - V_out = 1.7V\n' +
        '- **Power dissipation**: 1.7V x 0.45A = **765mW**\n' +
        '- **Rth (junction-ambient)**: 250 K/W (SOT-23-5, 4-layer, 1oz)\n' +
        '- **Tj rise**: 765mW x 250 K/W = **191C rise**\n' +
        '- **Tj at 25C ambient**: 216C -- **EXCEEDS 125C max**\n\n' +
        'This LDO cannot handle the load. Options:\n' +
        '1. Switch to a more efficient regulator (e.g., TLV62569 buck, 95% efficiency)\n' +
        '2. Reduce load on this rail by moving the IMU to a separate 3.3V supply\n' +
        '3. Use a larger package LDO (e.g., AP7361C-33E in SOT-223, Rth = 55 K/W)',
      status: 'sent',
      createdAt: '2026-03-03T13:10:00Z',
      updatedAt: '2026-03-03T13:10:00Z',
      graphRef: {
        nodeId: 'bom-entry-ldo',
        nodeType: 'bom-entry',
        label: 'AP2112K-3.3',
      },
    },
    {
      id: 'msg-9-3',
      threadId: 'th-9',
      actor: actors.agentME,
      content:
        'From a mechanical perspective, the SOT-223 package (option 3) increases board height by 0.5mm in that region. Current stack-up clearance is 1.2mm so this is fine.\n\n' +
        'The TLV62569 buck converter (option 1) requires an inductor (4.7uH, 3x3mm) which needs 12mm^2 of board area. The current layout has ~15mm^2 available near the LDO.',
      status: 'sent',
      createdAt: '2026-03-03T13:20:00Z',
      updatedAt: '2026-03-03T13:20:00Z',
    },
    {
      id: 'msg-9-4',
      threadId: 'th-9',
      actor: actors.userAlex,
      content: 'Go with option 1 (buck converter). The efficiency gain matters for battery life. Update the BOM and schematic.',
      status: 'sent',
      createdAt: '2026-03-03T14:20:00Z',
      updatedAt: '2026-03-03T14:20:00Z',
    },
  ],

  // ----- th-10: Initial Requirements Sync (project, archived) -----
  'th-10': [
    {
      id: 'msg-10-1',
      threadId: 'th-10',
      actor: actors.userAlex,
      content: 'Kicking off the drone flight controller project. Target specs: 30x30mm mounting, STM32F4, 4-layer PCB, BetaFlight compatible.',
      status: 'sent',
      createdAt: '2026-02-20T09:00:00Z',
      updatedAt: '2026-02-20T09:00:00Z',
    },
    {
      id: 'msg-10-2',
      threadId: 'th-10',
      actor: actors.agentSE,
      content:
        'Requirements captured. Initial constraint set generated:\n\n' +
        '- **Dimensions**: 30.5 x 30.5mm (standard FC mounting)\n' +
        '- **MCU**: STM32F405 or compatible\n' +
        '- **Layers**: 4 (signal/GND/PWR/signal)\n' +
        '- **Interfaces**: 6x PWM/DShot outputs, 1x SPI (gyro), 1x I2C (baro), 1x USB, 1x CAN\n' +
        '- **Firmware**: BetaFlight 4.5+ target\n\n' +
        'Shall I generate the initial schematic block diagram?',
      status: 'sent',
      createdAt: '2026-02-20T09:15:00Z',
      updatedAt: '2026-02-20T09:15:00Z',
    },
    {
      id: 'msg-10-3',
      threadId: 'th-10',
      actor: actors.userMaria,
      content: 'Looks good. Also add USB-C connector and include a BMP280 barometer.',
      status: 'sent',
      createdAt: '2026-02-20T10:30:00Z',
      updatedAt: '2026-02-20T10:30:00Z',
    },
  ],
};

// ---------------------------------------------------------------------------
// Helper: get a fully hydrated thread (messages populated)
// ---------------------------------------------------------------------------

/**
 * Returns a shallow copy of the thread with its `messages` array populated
 * from `mockMessages`. Useful for the "get single thread" handler.
 */
export function getHydratedThread(threadId: string): ChatThread | undefined {
  const thread = mockThreads.find((t) => t.id === threadId);
  if (!thread) return undefined;
  return {
    ...thread,
    messages: mockMessages[threadId] ?? [],
  };
}
