import { describe, expect, it } from 'vitest';
import { formatMaterialId } from './materialId';

describe('formatMaterialId', () => {
  it('formats lowercase AlphaIDs as their legacy decimal MPID for display', () => {
    expect(formatMaterialId('mp-aaaabwmb')).toBe('mp-32761');
    expect(formatMaterialId('mp-a')).toBe('mp-0');
  });

  it('keeps numeric MPIDs unchanged', () => {
    expect(formatMaterialId('mp-32761')).toBe('mp-32761');
  });

  it('formats the inclusive legacy cutoff and preserves the first newer AlphaID', () => {
    expect(formatMaterialId('mp-hilzd')).toBe('mp-3347529');
    expect(formatMaterialId('mp-hilze')).toBe('mp-hilze');
  });

  it.each([
    'not-an-mpid',
    'MP-aaa',
    'mp-Abc',
    'mp-ab1',
    'mvc-abc',
    'mp-abcdefghijk',
  ])('preserves unrecognized or out-of-scope ID %s', (raw) => {
    expect(formatMaterialId(raw)).toBe(raw);
  });
});
