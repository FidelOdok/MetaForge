import apiClient from '../client';

export interface RiskFactor {
  name: string;
  weight: number;
  score: number;
  description: string;
}

export interface PartRiskScore {
  mpn: string;
  manufacturer: string;
  overallScore: number;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  factors: RiskFactor[];
  flagged: boolean;
}

export interface BomRiskReport {
  projectId: string;
  totalParts: number;
  overallScore: number;
  criticalCount: number;
  highCount: number;
  mediumCount: number;
  lowCount: number;
  partScores: PartRiskScore[];
}

interface RiskFactorApi {
  name: string;
  weight: number;
  score: number;
  description: string;
}

interface PartRiskScoreApi {
  mpn: string;
  manufacturer: string;
  overall_score: number;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  factors: RiskFactorApi[];
  flagged: boolean;
}

interface BomRiskReportApi {
  project_id: string;
  total_parts: number;
  overall_score: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  part_scores: PartRiskScoreApi[];
}

/** FORGE-268: score real supply-chain risk (single-source, lead time,
 * lifecycle, price volatility, stock level, compliance) for a project's
 * real BOM, from real resolved distributor offers. */
export async function getBomRisk(projectId: string): Promise<BomRiskReport> {
  const { data } = await apiClient.get<BomRiskReportApi>('/bom/risk', {
    params: { project_id: projectId },
  });
  return {
    projectId: data.project_id,
    totalParts: data.total_parts,
    overallScore: data.overall_score,
    criticalCount: data.critical_count,
    highCount: data.high_count,
    mediumCount: data.medium_count,
    lowCount: data.low_count,
    partScores: data.part_scores.map((p) => ({
      mpn: p.mpn,
      manufacturer: p.manufacturer,
      overallScore: p.overall_score,
      riskLevel: p.risk_level,
      factors: p.factors.map((f) => ({
        name: f.name,
        weight: f.weight,
        score: f.score,
        description: f.description,
      })),
      flagged: p.flagged,
    })),
  };
}
