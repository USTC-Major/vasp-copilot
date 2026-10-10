import type { EnergyCollection } from '../../api/energy';
import type { EnergyDraft } from './energyDraft';

export type EnergyEditor = { collection: EnergyCollection; draft: EnergyDraft; dirty: boolean; conflict: boolean; resultExpired?: boolean };
export const energyDraftSessions = new Map<string, EnergyEditor>();
export const clearEnergyDraftSessions = () => energyDraftSessions.clear();
