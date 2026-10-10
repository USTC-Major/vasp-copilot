import type { EnergyCollection } from '../../api/energy';
import type { EnergyDraft } from './energyDraft';

export type EnergyEditor = { collection: EnergyCollection; draft: EnergyDraft; dirty: boolean; conflict: boolean; resultExpired?: boolean; activeCardId?: string; acceptedRisks?: Record<string, boolean> };
const storageKey = 'vasp-copilot.energy-card-drafts.v1';
class DraftSessions extends Map<string, EnergyEditor> {
  private restore() {
    if (this.size || typeof sessionStorage === 'undefined') return;
    try { const entries: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? '[]'); if (Array.isArray(entries)) for (const entry of entries) if (Array.isArray(entry) && typeof entry[0] === 'string' && entry[1]?.collection?.id === entry[0] && Array.isArray(entry[1]?.draft?.groups)) super.set(entry[0], entry[1] as EnergyEditor); } catch { /* Unavailable/corrupt browser storage does not block editing. */ }
  }
  override get(id: string) { this.restore(); return super.get(id); }
  override set(id: string, editor: EnergyEditor) { super.set(id, editor); try { sessionStorage.setItem(storageKey, JSON.stringify([...this])); } catch { /* In-memory draft remains available. */ } return this; }
  override clear() { super.clear(); try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be disabled. */ } }
}
export const energyDraftSessions = new DraftSessions();
export const clearEnergyDraftSessions = () => energyDraftSessions.clear();
