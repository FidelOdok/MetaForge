/**
 * FORGE-531: the one place that decides how a work product is previewed.
 *
 * Before this, two dispatch points disagreed. The twin inspector gated on
 * `wp_type` (only `cad_model` got 3D) and the full-screen modal dispatched on
 * `format` alone, so Markdown, JSON, CSV, KiCad, Gerber and C all showed as a
 * raw `<pre>`, while STEP in the modal, STL, 3MF and DXF had no preview at
 * all. Every preview surface (twin inspector, full-screen modal, project page
 * rows) now asks `previewEngineFor` and renders the answer through
 * `PreviewHost`.
 *
 * Rules, in priority order:
 *   1. A work product type whose meaning beats its file format (robot,
 *      simulation result, design decision, constraint set, BOM, assembly).
 *   2. The file format (from `format`, else the `file_path` extension).
 *   3. A per-type default when the format is missing or unknown.
 *   4. `none`, which names the format so the fallback can say which engine
 *      is missing. A binary is never routed to a text engine.
 */

export type PreviewEngine =
  | 'cad3d'
  | 'mesh3d'
  | 'markdown'
  | 'requirements'
  | 'bom'
  | 'decision'
  | 'csv'
  | 'json'
  | 'code'
  | 'kicad'
  | 'gerber'
  | 'dxf'
  | 'sim'
  | 'image'
  | 'pdf'
  | 'html'
  | 'robot'
  | 'text'
  | 'none';

/** The subset of a twin node the registry reads. A `TwinNode` satisfies it. */
export interface PreviewSubject {
  properties?: Record<string, string | number | boolean | undefined>;
  assemblyParts?: unknown[];
}

export interface PreviewResolution {
  engine: PreviewEngine;
  /** Normalised format (lowercase, no dot); '' when unknown. */
  format: string;
  /** Code-engine language hint (c, cpp, python, ...). */
  language?: string;
}

const STEP_FORMATS = new Set(['step', 'stp', 'iges', 'igs', 'brep', 'fcstd']);
const MESH_FORMATS = new Set(['stl', '3mf', 'glb', 'gltf']);
const MARKDOWN_FORMATS = new Set(['md', 'markdown']);
const CSV_FORMATS = new Set(['csv', 'tsv']);
const IMAGE_FORMATS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp']);
const KICAD_FORMATS = new Set(['kicad_sch', 'kicad_pcb', 'kicad_pro', 'kicad_sym', 'kicad_mod', 'sch']);
const GERBER_FORMATS = new Set([
  'gbr', 'ger', 'gerber', 'gtl', 'gbl', 'gts', 'gbs', 'gto', 'gbo', 'gtp', 'gbp', 'gko', 'gm1', 'gml',
  'drl', 'xln', 'exc',
]);
const TEXT_FORMATS = new Set([
  'txt', 'log', 'urdf', 'xacro', 'sdf', 'usda', 'net', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg',
]);

/** Code formats and the highlighter language they map to. */
const CODE_LANGUAGES: Record<string, string> = {
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  hpp: 'cpp',
  hh: 'cpp',
  ino: 'cpp',
  py: 'python',
  rs: 'rust',
  js: 'javascript',
  ts: 'typescript',
  s: 'asm',
};

/** Mesh formats the viewers load with three's loaders directly (not glTF). */
export const DIRECT_MESH_FORMATS = new Set(['stl', '3mf']);

/** Formats that are binary on disk: never fetched as text. */
export const BINARY_FORMATS = new Set([
  'step', 'stp', 'iges', 'igs', 'brep', 'fcstd', 'stl', '3mf', 'glb', 'png', 'jpg', 'jpeg', 'gif', 'webp',
  'pdf', 'zip', 'gz', 'tar', '7z', 'bin', 'hex', 'elf', 'xlsx', 'xls', 'docx',
]);

/** Engines that read the file as text (and so may fetch it). */
export const TEXT_ENGINES = new Set<PreviewEngine>([
  'markdown', 'requirements', 'bom', 'decision', 'csv', 'json', 'code', 'gerber', 'dxf', 'html', 'text',
]);

/** Human names, used by the "engine missing" fallback and the UI labels. */
export const ENGINE_LABELS: Record<PreviewEngine, string> = {
  cad3d: '3D CAD',
  mesh3d: '3D mesh',
  markdown: 'Markdown',
  requirements: 'Requirements table',
  bom: 'BOM table',
  decision: 'Decision card',
  csv: 'CSV table',
  json: 'JSON tree',
  code: 'Code',
  kicad: 'KiCad viewer',
  gerber: 'Gerber renderer',
  dxf: 'DXF viewer',
  sim: 'FEA summary',
  image: 'Image',
  pdf: 'PDF',
  html: 'HTML sketch',
  robot: 'Robot viewer',
  text: 'Text',
  none: 'No preview engine',
};

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

/** `format` if set, else the `file_path` extension; lowercase, no leading dot. */
export function formatOf(subject: PreviewSubject): string {
  const props = subject.properties ?? {};
  const fmt = str(props.format).trim().toLowerCase().replace(/^\./, '');
  if (fmt) return fmt;
  const path = str(props.file_path) || str(props.original_filename);
  const base = path.split(/[\\/]/).pop() ?? '';
  const dot = base.lastIndexOf('.');
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : '';
}

const DOC_TYPES = new Set([
  'prd', 'documentation', 'test_plan', 'hazard_analysis', 'system_architecture', 'technical_drawing',
  'compliance_checklist', 'procurement_record',
]);

function byFormat(fmt: string): PreviewResolution | null {
  if (!fmt) return null;
  if (STEP_FORMATS.has(fmt)) return { engine: 'cad3d', format: fmt };
  if (MESH_FORMATS.has(fmt)) return { engine: 'mesh3d', format: fmt };
  if (MARKDOWN_FORMATS.has(fmt)) return { engine: 'markdown', format: fmt };
  if (CSV_FORMATS.has(fmt)) return { engine: 'csv', format: fmt };
  if (fmt === 'json') return { engine: 'json', format: fmt };
  if (fmt in CODE_LANGUAGES) return { engine: 'code', format: fmt, language: CODE_LANGUAGES[fmt] };
  if (KICAD_FORMATS.has(fmt)) return { engine: 'kicad', format: fmt };
  if (GERBER_FORMATS.has(fmt)) return { engine: 'gerber', format: fmt };
  if (fmt === 'dxf') return { engine: 'dxf', format: fmt };
  if (IMAGE_FORMATS.has(fmt)) return { engine: 'image', format: fmt };
  if (fmt === 'pdf') return { engine: 'pdf', format: fmt };
  if (fmt === 'html' || fmt === 'htm') return { engine: 'html', format: fmt };
  if (TEXT_FORMATS.has(fmt)) return { engine: 'text', format: fmt };
  return null;
}

/** Is this format readable as markdown-ish text (or simply unrecorded)? */
function isTextualOrUnknown(fmt: string): boolean {
  return fmt === '' || MARKDOWN_FORMATS.has(fmt) || fmt === 'txt';
}

/** Pick the preview engine for a work product. Pure; safe to call per render. */
export function previewEngineFor(subject: PreviewSubject): PreviewResolution {
  const wpType = str(subject.properties?.wp_type);
  const fmt = formatOf(subject);

  // 1. Type beats format where the type carries the meaning.
  if (wpType === 'robot_description') return { engine: 'robot', format: fmt };
  if (wpType === 'simulation_result') return { engine: 'sim', format: fmt };
  if (wpType === 'design_decision' && isTextualOrUnknown(fmt)) return { engine: 'decision', format: fmt };
  if (wpType === 'constraint_set' && isTextualOrUnknown(fmt)) return { engine: 'requirements', format: fmt };
  if (wpType === 'bom' && (fmt === '' || CSV_FORMATS.has(fmt))) return { engine: 'bom', format: fmt };
  if ((subject.assemblyParts?.length ?? 0) > 0 && !MESH_FORMATS.has(fmt)) return { engine: 'cad3d', format: fmt };

  // 2. The file format.
  const fromFormat = byFormat(fmt);
  if (fromFormat) return fromFormat;

  // 3. Per-type defaults for a missing or unrecognised format. A binary
  //    format never falls through to a text engine here.
  if (!BINARY_FORMATS.has(fmt)) {
    if (wpType === 'cad_model') return { engine: 'cad3d', format: fmt };
    if (DOC_TYPES.has(wpType)) return { engine: 'markdown', format: fmt };
    if (wpType === 'firmware_source' || wpType === 'cad_source_script') {
      return { engine: 'code', format: fmt, language: wpType === 'cad_source_script' ? 'python' : 'c' };
    }
    if (wpType === 'schematic' || wpType === 'pcb_layout') return { engine: 'kicad', format: fmt };
    if (wpType === 'gerber') return { engine: 'gerber', format: fmt };
    if (wpType === 'design_sketch') return { engine: 'html', format: fmt };
  }

  return { engine: 'none', format: fmt };
}

/** Engines whose preview is a 3D scene. */
export function is3dEngine(engine: PreviewEngine): boolean {
  return engine === 'cad3d' || engine === 'mesh3d';
}
