import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import * as THREE from 'three';
import { render, screen } from '../../../test/test-utils';
import * as cls from '../../../api/endpoints/changeClassification';
import type { ChangeClassification } from '../../../api/endpoints/changeClassification';
import { ChangeClassificationLine, PendingClassificationLine } from '../ChangeClassificationLine';
import { MessageBody } from '../../chat/MessageBody';
import { displayText } from '../../../store/create-store';
import { appearanceFor, lookMaterial, plainClassificationLine } from '../../../lib/appearance';

const P = 'p1';

function classification(over: Partial<ChangeClassification>): ChangeClassification {
  return {
    change_class: 'appearance',
    confidence: 0.92,
    stage: 'rules',
    also: [],
    target_component: null,
    target_component_id: null,
    target_feature: null,
    handling: 'update_appearance',
    message: 'Colour change only. Your engineering model stays the same.',
    signals: ['red'],
    appearance: { colour: '#c62828', colour_name: 'red' },
    proposed_context_update: null,
    choices: [],
    stated_dimensions: [],
    ...over,
  };
}

describe('the Create one-liner', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('says an appearance change leaves the engineering model alone', () => {
    render(<ChangeClassificationLine projectId={P} classification={classification({})} />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Colour change only. Your engineering model stays the same.',
    );
    // No technical detail in Create: no class name, no confidence.
    expect(screen.queryByText(/appearance|0\.92/)).not.toBeInTheDocument();
  });

  it('shows nothing for a request it did not recognise', () => {
    const { container } = render(
      <ChangeClassificationLine
        projectId={P}
        classification={classification({ change_class: 'not_a_change', confidence: 0.4, message: 'x' })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('lets the person confirm a proposed material', async () => {
    const confirm = vi.spyOn(cls, 'confirmMaterial').mockResolvedValue();
    render(
      <ChangeClassificationLine
        projectId={P}
        classification={classification({
          change_class: 'material_engineering',
          handling: 'propose_material_update',
          message: 'Material change to Aluminium. This changes the engineering model.',
          proposed_context_update: { selected_material: 'Aluminium' },
        })}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Use Aluminium' }));
    expect(confirm).toHaveBeenCalledWith(P, 'Aluminium');
    expect(await screen.findByText(/Aluminium confirmed/)).toBeInTheDocument();
  });

  it('shows the plain part of the classified event while the reply comes', () => {
    const line = plainClassificationLine(
      'Colour change only. Your engineering model stays the same. [appearance, confidence 0.92 (rules); routing: update_appearance]',
    );
    render(<PendingClassificationLine line={line} />);
    expect(screen.getByRole('status')).toHaveTextContent(/^Colour change only\. Your engineering model stays the same\.$/);
  });
});

describe('the look or the material', () => {
  const look =
    'The look.\n\nChange kind (chosen by the person): appearance\nRequest: make it metallic';
  const material =
    'The material.\n\nChange kind (chosen by the person): material\nRequest: make it metallic';
  const blocks = [
    {
      type: 'choice' as const,
      prompt: 'Do you mean the look, or the actual material it is made from?',
      options: [
        { label: 'The look', value: look },
        { label: 'The material', value: material },
      ],
    },
  ];

  it('offers the two answers and sends the chosen one with the request', async () => {
    const onReply = vi.fn();
    render(
      <MessageBody
        text="Do you mean the look, or the actual material it is made from?"
        blocks={blocks}
        interactive
        onReply={onReply}
        projectId={P}
      />,
    );
    expect(screen.getByRole('button', { name: 'The material' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'The look' }));
    expect(onReply).toHaveBeenCalledWith(look);
  });

  it('shows the answer as the person chose it, not the marker', () => {
    expect(displayText(`${look}\nDiscuss using the supplied context.`)).toBe('The look.');
    expect(displayText(material)).toBe('The material.');
  });
});

describe('the appearance in the viewer', () => {
  const record = {
    project_id: P,
    product: { colour: '#151515' },
    components: [{ key: 'lamp-shade', name: 'Lamp shade', node_id: 'n-l1', colour: '#2e7d32', finish: 'gloss' }],
  };

  it('follows the component to a regenerated mesh, else the product', () => {
    expect(appearanceFor(record, { name: 'Lamp shade', nodeId: 'n-l3-new' })?.colour).toBe('#2e7d32');
    expect(appearanceFor(record, { meshName: 'lamp_shade' })?.colour).toBe('#2e7d32');
    expect(appearanceFor(record, { name: 'Base' })?.colour).toBe('#151515');
    expect(appearanceFor(null, { name: 'Base' })).toBeNull();
  });

  it('builds a display material from the mesh own material', () => {
    const original = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.5 });
    const m = lookMaterial(original, { colour: '#2e7d32', finish: 'gloss' }) as THREE.MeshStandardMaterial;
    expect(m).not.toBe(original);
    expect(m.color.getHexString()).toBe(new THREE.Color('#2e7d32').getHexString());
    expect(m.roughness).toBeCloseTo(0.15);
    expect(original.roughness).toBe(0.5);
    const chrome = lookMaterial(original, { render_material: 'chrome' }) as THREE.MeshStandardMaterial;
    expect(chrome.metalness).toBe(1);
  });
});
