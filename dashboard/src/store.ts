import type { DashboardClient, HouseholdView, MemoryAnswer } from './contracts';

export interface DashboardState {
  phase: 'idle' | 'loading' | 'ready' | 'error';
  view: HouseholdView | null;
  error: string;
  saving: boolean;
  searching: boolean;
  notice: string;
  answer: MemoryAnswer | null;
  queryError: string;
  saveError: string;
}
const initial = (): DashboardState => ({ phase: 'idle', view: null, error: '', saving: false, searching: false, notice: '', answer: null, queryError: '', saveError: '' });

export function createDashboardStore(client: DashboardClient) {
  let state = initial();
  let generation = 0;
  let queryGeneration = 0;
  const listeners = new Set<() => void>();
  const update = (patch: Partial<DashboardState>) => {
    state = { ...state, ...patch };
    for (const listener of listeners) listener();
  };
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async load(person: string) {
      const request = ++generation;
      ++queryGeneration;
      state = initial();
      if (!person.trim()) { update({ phase: 'error', error: 'Enter the saved person name or ID.' }); return; }
      update({ phase: 'loading' });
      try {
        const view = await client.load(person.trim());
        if (request === generation) update({ view, phase: 'ready' });
      } catch {
        if (request === generation) update({ phase: 'error', error: 'Could not open this notebook. Check the saved person name or ID and that the server is available.' });
      }
    },
    async query(question: string) {
      if (!state.view || !question.trim()) return;
      const request = ++queryGeneration;
      const owner = generation;
      const person = state.view.person.id;
      update({ searching: true, answer: null, queryError: '' });
      try {
        const answer = await client.queryMemory(person, question.trim());
        if (owner === generation && request === queryGeneration) update({ answer, searching: false });
      } catch {
        if (owner === generation && request === queryGeneration) update({ searching: false, queryError: 'Could not retrieve the saved records. Please try again.' });
      }
    },
    async save(type: string, detail: string): Promise<boolean> {
      if (!state.view || state.saving || !type.trim() || !detail.trim()) return false;
      const owner = generation;
      const person = state.view.person.id;
      update({ saving: true, saveError: '', notice: '' });
      try {
        await client.recordEvent(person, { type: type.trim(), detail: detail.trim() });
      } catch {
        if (owner === generation) update({ saving: false, saveError: 'Save was not confirmed. Refresh and check the timeline before submitting again.' });
        return false;
      }
      if (owner !== generation) return true;
      update({ notice: 'Your note was saved.', answer: null });
      ++queryGeneration;
      update({ searching: false });
      try {
        const view = await client.load(person);
        if (owner === generation) update({ view, saving: false });
      } catch {
        if (owner === generation) update({ saving: false, notice: 'Your note was saved, but the view could not refresh. Refresh to see the updated records.' });
      }
      return true;
    },
  };
}
