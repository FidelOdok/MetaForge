// Generative UI: the typed blocks an agent reply can carry. Mirrors
// api_gateway/chat/ui_blocks.py, which validates every block before it is
// stored, so these arrive as data and are rendered as components.

export type Basis = 'illustrative' | 'estimate' | 'calculated' | 'measured' | 'verified';

export interface ChoiceBlock {
  type: 'choice';
  prompt: string;
  options: { label: string; value?: string; description?: string }[];
  multiple?: boolean;
}

export interface ComparisonBlock {
  type: 'comparison';
  title?: string;
  criteria?: string[];
  options: {
    name: string;
    summary?: string;
    pros?: string[];
    cons?: string[];
    values?: Record<string, string>;
    recommended?: boolean;
  }[];
  basis?: Basis;
}

export interface MeasurementRequestBlock {
  type: 'measurement_request';
  label: string;
  unit: string;
  why?: string;
  critical?: boolean;
  current?: number;
}

export interface TableBlock {
  type: 'table';
  title?: string;
  columns: string[];
  rows: (string | number | null)[][];
  basis?: Basis;
}

export interface MetricsBlock {
  type: 'metrics';
  title?: string;
  items: { label: string; value: number; unit?: string; limit?: number; limit_kind?: 'max' | 'min' }[];
  basis?: Basis;
}

export interface CalloutBlock {
  type: 'callout';
  tone?: 'info' | 'warning' | 'risk' | 'success';
  title: string;
  body?: string;
}

export interface ProposalBlock {
  type: 'proposal';
  change_id: string;
  title?: string;
  summary?: string;
}

export interface PartBlock {
  type: 'part';
  node_id: string;
  label?: string;
  note?: string;
}

export type UiBlock =
  | ChoiceBlock
  | ComparisonBlock
  | MeasurementRequestBlock
  | TableBlock
  | MetricsBlock
  | CalloutBlock
  | ProposalBlock
  | PartBlock;
