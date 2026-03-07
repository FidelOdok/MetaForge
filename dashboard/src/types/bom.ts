export interface BomComponent {
  id: string;
  designator: string;
  partNumber: string;
  description: string;
  manufacturer: string;
  quantity: number;
  unitPrice: number;
  status: 'available' | 'low_stock' | 'out_of_stock' | 'alternate_needed';
  category: string;
  projectId: string;
}
