/**
 * RFC 4180 style CSV/TSV parsing for the CSV and BOM table engines: quoted
 * fields, doubled quotes, delimiters and newlines inside quotes. The
 * delimiter is sniffed from the first line when not given.
 */

export interface ParsedTable {
  header: string[];
  rows: string[][];
}

export function sniffDelimiter(text: string): string {
  const first = text.split(/\r?\n/, 1)[0] ?? '';
  let best = ',';
  let bestCount = 0;
  for (const d of [',', '\t', ';', '|']) {
    const count = first.split(d).length - 1;
    if (count > bestCount) {
      best = d;
      bestCount = count;
    }
  }
  return best;
}

export function parseCsv(text: string, delimiter?: string): ParsedTable {
  const src = text.replace(/^\uFEFF/, '');
  const d = delimiter ?? sniffDelimiter(src);
  const records: string[][] = [];
  let field = '';
  let record: string[] = [];
  let inQuotes = false;

  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"' && field === '') {
      inQuotes = true;
    } else if (ch === d) {
      record.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i++;
      record.push(field);
      records.push(record);
      record = [];
      field = '';
    } else {
      field += ch;
    }
  }
  if (field !== '' || record.length > 0) {
    record.push(field);
    records.push(record);
  }

  const nonEmpty = records.filter((r) => r.some((c) => c.trim() !== ''));
  if (nonEmpty.length === 0) return { header: [], rows: [] };
  const header = (nonEmpty[0] ?? []).map((h) => h.trim());
  const width = header.length;
  const rows = nonEmpty.slice(1).map((r) => {
    const padded = r.map((c) => c.trim());
    while (padded.length < width) padded.push('');
    return padded;
  });
  return { header, rows };
}
