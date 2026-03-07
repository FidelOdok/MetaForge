import type { BomComponent } from '../../types/bom';

const MOCK_BOM: BomComponent[] = [
  { id: 'bom-001', designator: 'U1', partNumber: 'STM32F405RGT6', description: 'ARM Cortex-M4 MCU', manufacturer: 'STMicroelectronics', quantity: 1, unitPrice: 8.50, status: 'available', category: 'IC', projectId: 'proj-001' },
  { id: 'bom-002', designator: 'U2', partNumber: 'ICM-20689', description: '6-axis IMU', manufacturer: 'TDK InvenSense', quantity: 1, unitPrice: 3.20, status: 'available', category: 'Sensor', projectId: 'proj-001' },
  { id: 'bom-003', designator: 'U3', partNumber: 'BMP280', description: 'Barometric pressure sensor', manufacturer: 'Bosch', quantity: 1, unitPrice: 1.80, status: 'low_stock', category: 'Sensor', projectId: 'proj-001' },
  { id: 'bom-004', designator: 'U4', partNumber: 'NEO-M9N', description: 'GNSS receiver module', manufacturer: 'u-blox', quantity: 1, unitPrice: 12.50, status: 'available', category: 'Module', projectId: 'proj-001' },
  { id: 'bom-005', designator: 'C1-C20', partNumber: 'GRM155R71C104KA88D', description: '100nF 16V 0402 MLCC', manufacturer: 'Murata', quantity: 20, unitPrice: 0.02, status: 'available', category: 'Capacitor', projectId: 'proj-001' },
  { id: 'bom-006', designator: 'R1-R15', partNumber: 'RC0402FR-0710KL', description: '10K 0402 resistor', manufacturer: 'Yageo', quantity: 15, unitPrice: 0.01, status: 'available', category: 'Resistor', projectId: 'proj-001' },
  { id: 'bom-007', designator: 'Y1', partNumber: 'ABM8-8.000MHZ-B2-T', description: '8MHz crystal', manufacturer: 'Abracon', quantity: 1, unitPrice: 0.45, status: 'available', category: 'Crystal', projectId: 'proj-001' },
  { id: 'bom-008', designator: 'J1', partNumber: 'USB4105-GF-A', description: 'USB-C connector', manufacturer: 'GCT', quantity: 1, unitPrice: 0.65, status: 'out_of_stock', category: 'Connector', projectId: 'proj-001' },
  { id: 'bom-009', designator: 'L1', partNumber: 'LQH3NPN4R7NG0L', description: '4.7uH inductor', manufacturer: 'Murata', quantity: 1, unitPrice: 0.30, status: 'available', category: 'Inductor', projectId: 'proj-001' },
  { id: 'bom-010', designator: 'U5', partNumber: 'TPS62160', description: '3.3V buck converter', manufacturer: 'Texas Instruments', quantity: 1, unitPrice: 2.10, status: 'alternate_needed', category: 'IC', projectId: 'proj-001' },
];

export async function getBom(projectId?: string): Promise<BomComponent[]> {
  // TODO: Replace with real API call when BOM endpoints exist
  if (projectId) {
    return MOCK_BOM.filter((c) => c.projectId === projectId);
  }
  return MOCK_BOM;
}
