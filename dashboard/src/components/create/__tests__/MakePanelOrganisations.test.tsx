import { describe, it, expect } from 'vitest';
import { blockedOrganisation } from '../MakePanel';
import type { LibraryOrganisation } from '../../../api/endpoints/library';

function org(title: string, extra: Partial<LibraryOrganisation> = {}): LibraryOrganisation {
  return {
    id: `or_${title}`,
    name: title.toLowerCase().replace(/\W+/g, '_'),
    title,
    roles: ['fabricator'],
    aliases: [],
    status: 'unreviewed',
    status_reason: '',
    country: '',
    regions: [],
    website: '',
    quote_url: '',
    certifications: [],
    contacts: [],
    lifecycle_policy: '',
    connector: '',
    notes: '',
    version: 1,
    ...extra,
  };
}

describe('blockedOrganisation (FORGE-450)', () => {
  const orgs = [
    org('Protolabs UK'),
    org('Shady Fab', { status: 'blocked', status_reason: 'missed deliveries', aliases: ['ShadyFab Ltd'] }),
  ];

  it('finds a blocked fabricator by its title or any alias, ignoring case', () => {
    expect(blockedOrganisation(orgs, 'shady fab')?.status_reason).toBe('missed deliveries');
    expect(blockedOrganisation(orgs, ' ShadyFab Ltd ')?.title).toBe('Shady Fab');
  });

  it('says nothing for an approved or unknown name, or an empty field', () => {
    expect(blockedOrganisation(orgs, 'Protolabs UK')).toBeUndefined();
    expect(blockedOrganisation(orgs, 'Someone else')).toBeUndefined();
    expect(blockedOrganisation(orgs, '')).toBeUndefined();
  });
});
