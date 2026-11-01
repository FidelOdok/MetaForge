import apiClient from '../client';

export type ManufactureProcess = '3d_print' | 'cnc';

export interface ManufactureReleaseResult {
  workProductId: string;
  process: ManufactureProcess;
  format: string;
  filename: string;
  fileSizeBytes: number;
  contentBase64: string;
}

interface ManufactureReleaseApiResponse {
  work_product_id: string;
  process: string;
  format: string;
  filename: string;
  file_size_bytes: number;
  content_base64: string;
}

const CONTENT_TYPES: Record<string, string> = {
  stl: 'model/stl',
  step: 'application/step',
};

/** FORGE-294: chain a real committed work product's geometry into a real
 * manufacturing output file (STL for 3D printing, STEP for CNC) via
 * twin.stage_work_product_file -> cadquery.export_geometry. Returns the
 * file base64-encoded (same shape cadquery.export_geometry's own
 * step_base64 field already uses) rather than a raw binary response, so
 * the request works identically through the sample-mode axios adapter
 * (see sample-workspace.ts) and is Playwright-testable. */
export async function releaseForManufacture(
  workProductId: string,
  process: ManufactureProcess,
): Promise<ManufactureReleaseResult> {
  const { data } = await apiClient.get<ManufactureReleaseApiResponse>('/manufacture/release', {
    params: { work_product_id: workProductId, process },
  });
  return {
    workProductId: data.work_product_id,
    process: data.process as ManufactureProcess,
    format: data.format,
    filename: data.filename,
    fileSizeBytes: data.file_size_bytes,
    contentBase64: data.content_base64,
  };
}

function base64ToBlob(base64: string, mimeType: string): Blob {
  const byteChars = atob(base64);
  const byteNumbers = new Array<number>(byteChars.length);
  for (let i = 0; i < byteChars.length; i += 1) {
    byteNumbers[i] = byteChars.charCodeAt(i);
  }
  return new Blob([new Uint8Array(byteNumbers)], { type: mimeType });
}

/** Decode the base64 body into a Blob and trigger the browser's own
 * download flow -- same client-built-blob pattern BomPage.tsx's CSV
 * export already uses, just from real decoded bytes instead of a CSV
 * string. */
export function triggerManufactureDownload(result: ManufactureReleaseResult): void {
  const blob = base64ToBlob(result.contentBase64, CONTENT_TYPES[result.format] ?? 'application/octet-stream');
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = result.filename;
  a.click();
  URL.revokeObjectURL(url);
}
