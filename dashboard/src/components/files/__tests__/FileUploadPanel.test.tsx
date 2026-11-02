import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '../../../test/test-utils';
import { FileUploadPanel } from '../FileUploadPanel';
import * as uploads from '../../../api/endpoints/uploads';
import type { StagedUpload } from '../../../api/endpoints/uploads';

function upload(overrides: Partial<StagedUpload> = {}): StagedUpload {
  return {
    id: 'u1',
    status: 'approved',
    source: 'dashboard',
    project_id: 'p1',
    description: '',
    requested_by: 'local:dashboard',
    agent: null,
    created_at: '2026-11-02T10:00:00Z',
    expires_at: null,
    files: [
      {
        name: 'ref.png',
        size: 2048,
        sha256: 'x',
        format: 'png',
        wp_type: 'image',
        domain: 'systems',
        error: null,
        work_product_id: 'wp-1',
      },
      {
        name: 'notes.exe',
        size: 10,
        sha256: '',
        format: '',
        wp_type: '',
        domain: '',
        error: 'Unsupported file type',
        work_product_id: null,
      },
    ],
    ...overrides,
  };
}

async function choose(testId: string, files: File[]) {
  const input = screen.getByTestId(testId) as HTMLInputElement;
  await act(async () => {
    Object.defineProperty(input, 'files', { value: files, configurable: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

describe('FileUploadPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(uploads, 'listUploads').mockResolvedValue([]);
  });

  it('imports chosen files at once by default and reports what was left out', async () => {
    const create = vi.spyOn(uploads, 'createUpload').mockResolvedValue(upload());
    render(<FileUploadPanel projectId="p1" />);
    const png = new File(['x'], 'ref.png', { type: 'image/png' });
    await choose('upload-files-input', [png]);
    await waitFor(() => expect(screen.getByTestId('upload-result')).toBeInTheDocument());
    expect(create).toHaveBeenCalledWith([png], {
      projectId: 'p1',
      description: '',
      approve: true,
    });
    expect(screen.getByText(/1 file\(s\) imported/)).toBeInTheDocument();
    expect(screen.getByText(/notes.exe: Unsupported file type/)).toBeInTheDocument();
  });

  it('sends for review when import-now is off', async () => {
    const create = vi
      .spyOn(uploads, 'createUpload')
      .mockResolvedValue(upload({ status: 'pending', files: [upload().files[0]!] }));
    render(<FileUploadPanel projectId="p1" />);
    await act(async () => {
      screen.getByTestId('import-now').click();
    });
    await choose('upload-folder-input', [new File(['x'], 'a.step')]);
    await waitFor(() => expect(screen.getByText(/waiting for approval/)).toBeInTheDocument());
    expect(create.mock.calls[0]![1]).toMatchObject({ approve: false });
    expect(screen.getByText(/Review on the Approvals page/)).toBeInTheDocument();
  });

  it('lists recent uploads with their state', async () => {
    vi.spyOn(uploads, 'listUploads').mockResolvedValue([upload({ status: 'pending' })]);
    render(<FileUploadPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByTestId('recent-uploads')).toBeInTheDocument());
    expect(screen.getByText('pending')).toBeInTheDocument();
    expect(screen.getByText('review')).toBeInTheDocument();
  });

  it('accepts images, G-code and zips in the picker', () => {
    render(<FileUploadPanel />);
    const accept = (screen.getByTestId('upload-files-input') as HTMLInputElement).accept;
    for (const ext of ['.png', '.jpg', '.gcode', '.3mf', '.zip', '.kicad_pcb', '.pdf']) {
      expect(accept).toContain(ext);
    }
  });
});
