const ALPHA_ID_PATTERN = /^mp-[a-z]{1,8}$/;
const LEGACY_MPID_CUTOFF = 3_347_529;

/**
 * Format lowercase Materials Project AlphaIDs for display while preserving their API identity.
 * Emmet's AlphaID uses a=0..z=25; values through the inclusive cutoff retain legacy mp-<integer> strings.
 * Source: https://raw.githubusercontent.com/materialsproject/emmet/main/emmet-core/emmet/core/mpid.py
 * The cutoff rule was checked on 2026-09-28.
 */
export function formatMaterialId(raw: string): string {
  if (!ALPHA_ID_PATTERN.test(raw)) return raw;

  let value = 0;
  for (const character of raw.slice(3)) {
    value = value * 26 + character.charCodeAt(0) - 97;
    if (value > LEGACY_MPID_CUTOFF) return raw;
  }

  return `mp-${value}`;
}
