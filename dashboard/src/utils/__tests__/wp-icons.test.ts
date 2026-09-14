import { describe, it, expect } from 'vitest';
import { WP_TYPE_ICONS, iconForNode } from '../wp-icons';

describe('iconForNode', () => {
  it('returns the wp_type-specific icon for a work_product node', () => {
    const node = { type: 'work_product' as const, properties: { wp_type: 'design_sketch' } };
    expect(iconForNode(node)).toBe('design_services');
  });

  it('returns the cad_model icon', () => {
    const node = { type: 'work_product' as const, properties: { wp_type: 'cad_model' } };
    expect(iconForNode(node)).toBe('view_in_ar');
  });

  it('falls back to the generic icon for an unlisted wp_type', () => {
    const node = { type: 'work_product' as const, properties: { wp_type: 'some_future_type' } };
    expect(iconForNode(node)).toBe('description');
  });

  it('falls back to the generic icon when wp_type is missing', () => {
    const node = { type: 'work_product' as const, properties: {} };
    expect(iconForNode(node)).toBe('description');
  });

  it('uses the coarse node-type icon for non-work_product nodes', () => {
    const node = { type: 'constraint' as const, properties: {} };
    expect(iconForNode(node)).toBe('rule');
  });

  it('every entry in WP_TYPE_ICONS is a non-empty string', () => {
    for (const [wpType, icon] of Object.entries(WP_TYPE_ICONS)) {
      expect(icon.length, `icon for ${wpType}`).toBeGreaterThan(0);
    }
  });
});
