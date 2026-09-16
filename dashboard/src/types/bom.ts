export interface BomComponent {
  id: string;
  designator: string;
  partNumber: string;
  description: string;
  manufacturer: string;
  quantity: number;
  unitPrice: number;
  priceCurrency: string;
  status: 'available' | 'low_stock' | 'out_of_stock' | 'alternate_needed';
  category: string;
  projectId: string;
  imageUrl: string | null;
  purchaseUrl: string | null;
  datasheetUrl: string | null;
  footprint: string | null;
  cadModelUrl: string | null;
}
