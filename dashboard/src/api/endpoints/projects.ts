import type { Project } from '../../types/project';

/** Mock project data until project endpoints exist in the Gateway. */
const MOCK_PROJECTS: Project[] = [
  {
    id: 'proj-001',
    name: 'Drone Flight Controller',
    description: 'STM32-based flight controller with IMU, barometer, and GPS integration.',
    status: 'active',
    agentCount: 3,
    lastUpdated: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
    artifacts: [
      { id: 'art-001', name: 'Main Schematic', type: 'schematic', status: 'valid', updatedAt: new Date().toISOString() },
      { id: 'art-002', name: 'PCB Layout', type: 'pcb', status: 'warning', updatedAt: new Date().toISOString() },
      { id: 'art-003', name: 'Enclosure CAD', type: 'cad_model', status: 'valid', updatedAt: new Date().toISOString() },
    ],
  },
  {
    id: 'proj-002',
    name: 'IoT Sensor Hub',
    description: 'ESP32-based sensor aggregation board with LoRa and WiFi connectivity.',
    status: 'active',
    agentCount: 2,
    lastUpdated: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 14 * 24 * 60 * 60 * 1000).toISOString(),
    artifacts: [
      { id: 'art-004', name: 'Sensor Board Schematic', type: 'schematic', status: 'valid', updatedAt: new Date().toISOString() },
      { id: 'art-005', name: 'Firmware', type: 'firmware', status: 'unknown', updatedAt: new Date().toISOString() },
    ],
  },
  {
    id: 'proj-003',
    name: 'Power Supply Module',
    description: 'High-efficiency buck converter module for 5V/3.3V output.',
    status: 'draft',
    agentCount: 0,
    lastUpdated: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    artifacts: [],
  },
];

export async function getProjects(): Promise<Project[]> {
  // TODO: Replace with real API call when project endpoints exist
  return MOCK_PROJECTS;
}

export async function getProject(id: string): Promise<Project | undefined> {
  // TODO: Replace with real API call
  return MOCK_PROJECTS.find((p) => p.id === id);
}
