import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '../../../test/test-utils';
import { AssemblyExportPanel } from '../AssemblyExportPanel';
import type { TwinNode } from '../../../types/twin';

const mockUrdfAssemblyMutate = vi.fn();
const mockSdfAssemblyMutate = vi.fn();
const mockUsdAssemblyMutate = vi.fn();
const mockRos2LaunchMutate = vi.fn();
let mockSessionSummary: { data: unknown; isError: boolean } = { data: undefined, isError: false };
let mockSessionJoints: { data: unknown; isError: boolean } = { data: undefined, isError: false };

vi.mock('../../../hooks/use-cad-export', () => ({
  useExportUrdfAssembly: () => ({ mutate: mockUrdfAssemblyMutate, isPending: false }),
  useExportSdfAssembly: () => ({ mutate: mockSdfAssemblyMutate, isPending: false }),
  useExportUsdAssembly: () => ({ mutate: mockUsdAssemblyMutate, isPending: false }),
  useGenerateRos2Launch: () => ({ mutate: mockRos2LaunchMutate, isPending: false }),
  useSessionSummary: () => mockSessionSummary,
  useSessionJoints: () => mockSessionJoints,
}));

function cadNode(id: string, name: string): TwinNode {
  return {
    id,
    name,
    type: 'work_product',
    domain: 'mechanical',
    status: 'valid',
    properties: { wp_type: 'cad_model' },
    updatedAt: new Date().toISOString(),
  };
}

const NODES = [cadNode('n1', 'Base Plate.step'), cadNode('n2', 'Arm Link.step')];

describe('AssemblyExportPanel', () => {
  beforeEach(() => {
    mockUrdfAssemblyMutate.mockClear();
    mockSdfAssemblyMutate.mockClear();
    mockUsdAssemblyMutate.mockClear();
    mockRos2LaunchMutate.mockClear();
    mockSessionSummary = { data: undefined, isError: false };
    mockSessionJoints = { data: undefined, isError: false };
  });

  it('adds a part with a slugified default link name from the Twin node picker', () => {
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);

    fireEvent.change(screen.getByText('+ Add part…').closest('select')!, { target: { value: 'n1' } });

    expect(screen.getByText('Parts (1)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('base_plate')).toBeInTheDocument();
  });

  it('blocks submit until every part has a Twin node and link name', () => {
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Export URDF assembly' }));

    expect(mockUrdfAssemblyMutate).not.toHaveBeenCalled();
  });

  it('submits a URDF assembly export with the picked parts', () => {
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);

    fireEvent.change(screen.getByText('+ Add part…').closest('select')!, { target: { value: 'n1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Export URDF assembly' }));

    expect(mockUrdfAssemblyMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        parts: [expect.objectContaining({ node_id: 'n1', link_name: 'base_plate' })],
        joints: [],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it('shows an error when the session lookup fails, without blocking manual entry', () => {
    mockSessionSummary = { data: undefined, isError: true };
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText('FreeCAD session id'), { target: { value: 'sess-x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Fetch' }));

    expect(screen.getByText(/No live session found for "sess-x"/)).toBeInTheDocument();
  });

  it('imports joints from a fetched session', () => {
    mockSessionSummary = {
      data: { session_id: 'sess-1', name: 'my_asm', object_count: 2, objects: [] },
      isError: false,
    };
    mockSessionJoints = {
      data: {
        joints: [
          { name: 'j1', type: 'revolute', base: 'base_plate', follower: 'arm_link', axis: [0, 0, 1], anchor: [0, 0, 10] },
        ],
      },
      isError: false,
    };
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Import 1 joint(s)' }));

    expect(screen.getByText('Joints (1)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('base_plate')).toBeInTheDocument();
    expect(screen.getByDisplayValue('arm_link')).toBeInTheDocument();
  });

  it('offers a ROS2 launch follow-on only after a successful URDF export', () => {
    render(<AssemblyExportPanel items={NODES} onClose={vi.fn()} />);
    fireEvent.change(screen.getByText('+ Add part…').closest('select')!, { target: { value: 'n1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Export URDF assembly' }));

    const onSuccess = mockUrdfAssemblyMutate.mock.calls[0]?.[1].onSuccess as (data: unknown) => void;
    act(() => {
      onSuccess({
        output_file: { filename: 'robot.urdf', download_url: '/v1/cad-export/download/abc/robot.urdf' },
        mesh_files: [{ filename: 'base_plate.stl', download_url: '/v1/cad-export/download/abc/base_plate.stl' }],
        robot_name: 'robot',
        link_names: ['base_plate'],
        joint_names: [],
      });
    });

    expect(screen.getByRole('button', { name: 'Generate ROS2 launch file' })).toBeInTheDocument();
  });
});
