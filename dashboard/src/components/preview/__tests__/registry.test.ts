import { describe, it, expect } from 'vitest';
import {
  BINARY_FORMATS,
  ENGINE_LABELS,
  TEXT_ENGINES,
  formatOf,
  is3dEngine,
  previewEngineFor,
  type PreviewEngine,
} from '../registry';

// Every WorkProductType in twin_core/models/enums.py, plus an unknown one.
const WP_TYPES = [
  'schematic', 'pcb_layout', 'bom', 'cad_model', 'firmware_source', 'simulation_result', 'test_plan',
  'test_result', 'manufacturing_file', 'constraint_set', 'prd', 'pinmap', 'gerber', 'pick_and_place',
  'documentation', 'design_decision', 'cad_source_script', 'robot_description', 'design_sketch',
  'hazard_analysis', 'system_architecture', 'technical_drawing', 'compliance_checklist', 'procurement_record',
  'load_case', 'something_new', '',
];

// Every format the registry knows, plus binaries it must refuse and junk.
const FORMATS = [
  '', 'step', 'stp', 'iges', 'brep', 'fcstd', 'stl', '3mf', 'glb', 'gltf', 'md', 'markdown', 'csv', 'tsv', 'json',
  'c', 'h', 'cpp', 'hpp', 'ino', 'py', 'rs', 'kicad_sch', 'kicad_pcb', 'kicad_pro', 'gbr', 'gtl', 'gbl', 'drl',
  'dxf', 'png', 'jpg', 'svg', 'webp', 'pdf', 'html', 'txt', 'log', 'urdf', 'sdf', 'yaml', 'xml', 'zip', 'bin',
  'hex', 'elf', 'xlsx', 'docx', 'weird',
];

const wp = (wp_type: string, format: string, extra: Record<string, string> = {}) => ({
  properties: { ...(wp_type ? { wp_type } : {}), ...(format ? { format } : {}), ...extra },
});

const engine = (wp_type: string, format: string) => previewEngineFor(wp(wp_type, format)).engine;

describe('previewEngineFor: explicit type/format expectations', () => {
  const cases: [string, string, PreviewEngine][] = [
    // 3D
    ['cad_model', 'step', 'cad3d'],
    ['cad_model', 'stp', 'cad3d'],
    ['cad_model', '', 'cad3d'],
    ['cad_model', 'fcstd', 'cad3d'],
    ['manufacturing_file', 'step', 'cad3d'],
    ['cad_model', 'stl', 'mesh3d'],
    ['cad_model', '3mf', 'mesh3d'],
    ['cad_model', 'glb', 'mesh3d'],
    ['manufacturing_file', 'stl', 'mesh3d'],
    ['', 'gltf', 'mesh3d'],
    // documents
    ['prd', 'md', 'markdown'],
    ['prd', '', 'markdown'],
    ['documentation', 'md', 'markdown'],
    ['documentation', '', 'markdown'],
    ['test_plan', 'md', 'markdown'],
    ['hazard_analysis', '', 'markdown'],
    ['', 'markdown', 'markdown'],
    ['prd', 'pdf', 'pdf'],
    // structured records
    ['constraint_set', 'md', 'requirements'],
    ['constraint_set', '', 'requirements'],
    ['constraint_set', 'json', 'json'],
    ['bom', 'csv', 'bom'],
    ['bom', '', 'bom'],
    ['bom', 'json', 'json'],
    ['design_decision', 'md', 'decision'],
    ['design_decision', '', 'decision'],
    ['simulation_result', 'json', 'sim'],
    ['simulation_result', '', 'sim'],
    ['robot_description', 'urdf', 'robot'],
    ['robot_description', 'sdf', 'robot'],
    // data
    ['', 'csv', 'csv'],
    ['pick_and_place', 'csv', 'csv'],
    ['test_result', 'tsv', 'csv'],
    ['pinmap', 'json', 'json'],
    ['load_case', 'json', 'json'],
    // code
    ['firmware_source', 'c', 'code'],
    ['firmware_source', 'h', 'code'],
    ['firmware_source', 'cpp', 'code'],
    ['firmware_source', '', 'code'],
    ['cad_source_script', 'py', 'code'],
    ['cad_source_script', '', 'code'],
    // EDA
    ['schematic', 'kicad_sch', 'kicad'],
    ['pcb_layout', 'kicad_pcb', 'kicad'],
    ['schematic', '', 'kicad'],
    ['gerber', 'gbr', 'gerber'],
    ['gerber', 'gtl', 'gerber'],
    ['manufacturing_file', 'drl', 'gerber'],
    ['gerber', '', 'gerber'],
    ['gerber', 'zip', 'none'],
    ['technical_drawing', 'dxf', 'dxf'],
    ['', 'dxf', 'dxf'],
    // media and text
    ['design_sketch', 'png', 'image'],
    ['', 'svg', 'image'],
    ['design_sketch', 'html', 'html'],
    ['design_sketch', '', 'html'],
    ['', 'txt', 'text'],
    ['', 'log', 'text'],
    ['', 'urdf', 'text'],
    // unknown
    ['', '', 'none'],
    ['something_new', 'weird', 'none'],
    ['cad_model', 'zip', 'none'],
    ['firmware_source', 'hex', 'none'],
    ['firmware_source', 'elf', 'none'],
  ];

  it.each(cases)('%s / .%s -> %s', (wpType, format, expected) => {
    expect(engine(wpType, format)).toBe(expected);
  });

  it('carries a highlighter language for code', () => {
    expect(previewEngineFor(wp('firmware_source', 'c')).language).toBe('c');
    expect(previewEngineFor(wp('firmware_source', 'cpp')).language).toBe('cpp');
    expect(previewEngineFor(wp('cad_source_script', 'py')).language).toBe('python');
    expect(previewEngineFor(wp('cad_source_script', '')).language).toBe('python');
  });

  it('renders a prd revision as the derived prd, a legacy prd as Markdown (FORGE-528)', () => {
    expect(previewEngineFor({ properties: { wp_type: 'prd', format: 'md', item_key: 'PRD-W' } }).engine).toBe('prd');
    expect(previewEngineFor({ properties: { wp_type: 'prd', format: '', item_key: 'PRD-W' } }).engine).toBe('prd');
    expect(previewEngineFor({ properties: { wp_type: 'prd', format: 'md' } }).engine).toBe('markdown');
    expect(previewEngineFor({ properties: { wp_type: 'prd', format: 'pdf', item_key: 'PRD-W' } }).engine).toBe('pdf');
  });

  it('routes an assembly (metadata.parts) to the 3D CAD engine', () => {
    const asm = { properties: { wp_type: 'cad_model', format: 'json' }, assemblyParts: [{ node_id: 'p1', name: 'arm' }] };
    expect(previewEngineFor(asm).engine).toBe('cad3d');
  });
});

describe('previewEngineFor: every type x format pair', () => {
  const pairs = WP_TYPES.flatMap((t) => FORMATS.map((f) => [t, f] as const));

  it('covers the full matrix', () => {
    expect(pairs.length).toBe(WP_TYPES.length * FORMATS.length);
  });

  it.each(pairs)('%s / .%s resolves to a known engine and never reads a binary as text', (wpType, format) => {
    const res = previewEngineFor(wp(wpType, format));
    expect(Object.keys(ENGINE_LABELS)).toContain(res.engine);
    expect(res.format).toBe(format);
    if (BINARY_FORMATS.has(format)) expect(TEXT_ENGINES.has(res.engine)).toBe(false);
    if (wpType === 'robot_description') expect(res.engine).toBe('robot');
    if (wpType === 'simulation_result') expect(res.engine).toBe('sim');
  });
});

describe('formatOf', () => {
  it('normalises case and a leading dot', () => {
    expect(formatOf({ properties: { format: '.STEP' } })).toBe('step');
  });
  it('falls back to the file_path extension', () => {
    expect(formatOf({ properties: { file_path: 'eda/kicad/board.kicad_pcb' } })).toBe('kicad_pcb');
    expect(formatOf({ properties: { file_path: 'C:\\work\\bracket.STL' } })).toBe('stl');
    expect(previewEngineFor({ properties: { wp_type: 'cad_model', file_path: 'parts/arm.3mf' } }).engine).toBe('mesh3d');
  });
  it('is empty when nothing says', () => {
    expect(formatOf({ properties: { file_path: 'Makefile' } })).toBe('');
    expect(formatOf({})).toBe('');
  });
});

describe('is3dEngine', () => {
  it('is true only for the 3D engines', () => {
    expect(is3dEngine('cad3d')).toBe(true);
    expect(is3dEngine('mesh3d')).toBe(true);
    expect(is3dEngine('robot')).toBe(false);
    expect(is3dEngine('markdown')).toBe(false);
  });
});
