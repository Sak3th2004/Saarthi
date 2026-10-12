/**
 * Caregiver medication schedule entry via household manage.
 *
 * Live contract (mcp-server directory / household_management):
 *   POST /household/manage/review
 *   { expected_revision, change: {
 *       kind: 'add_medication',
 *       person_id: string,
 *       name: string,          // caregiver-typed fact
 *       dose: string,          // caregiver-typed fact — not advice
 *       schedule: string[]     // HH:MM 24h times, or [] if unknown
 *       supply_count?: number | null  // optional remaining doses; omit if unknown
 *     }}
 *   Review may include notice (caregiver fact disclaimer).
 *   POST /household/manage/confirm { review_token, confirmed: true }
 *   → { status: 'saved', directory, medication: { person_id, name, dose, schedule, supply_count? }, notice }
 * Directory revision is unchanged; confirm must echo medication, not only status:"saved".
 */
export const ADD_MEDICATION_MANAGE_AVAILABLE = true;

export const ADD_MEDICATION_CHANGE_KIND = 'add_medication' as const;

const SCHEDULE_TIME = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;

export type AddMedicationChange = {
  kind: typeof ADD_MEDICATION_CHANGE_KIND;
  person_id: string;
  name: string;
  dose: string;
  schedule: string[];
  supply_count?: number | null;
};

/** Low-supply threshold matches MCP check_in (saved count ≤ 10). */
export const LOW_SUPPLY_THRESHOLD = 10;

export function supplyLabel(supply: number | null | undefined): string | null {
  if (supply === null || supply === undefined) return null;
  if (supply <= LOW_SUPPLY_THRESHOLD) return `${supply} left · low supply`;
  return `${supply} doses remaining`;
}

/** Empty-state copy for the medication schedule card. */
export function medicationEmptyCopy(opts: { canManageHousehold: boolean }) {
  return {
    title: 'No medication schedule saved.',
    body: 'A caregiver can add a saved schedule fact — the name, dose text, and times your family already uses. This stores what you type; it is not medical advice.',
    nextStep: opts.canManageHousehold
      ? 'Caregiver entry: open Update family details above, choose “Add a medication schedule fact,” type the name, dose, and HH:MM times, then review and confirm.'
      : 'Caregiver entry: after sign-in, use Update family details → Add a medication schedule fact to save what your family typed.',
  };
}

/** Split caregiver-typed schedule text into HH:MM slots (rejects non-clock text). */
export function parseScheduleSlots(text: string): string[] {
  return text
    .split(/[,;|]+/)
    .map(part => part.trim())
    .filter(part => SCHEDULE_TIME.test(part))
    .slice(0, 24);
}

export function isScheduleTime(value: string): boolean {
  return SCHEDULE_TIME.test(value);
}

export function describeAddMedication(change: AddMedicationChange, personName: string): string {
  const times = change.schedule.length ? change.schedule.join(' · ') : 'time not recorded';
  const supply = change.supply_count === null || change.supply_count === undefined
    ? 'remaining doses not recorded'
    : `${change.supply_count} remaining doses`;
  return `Save medication fact for ${personName}: ${change.name}, ${change.dose}, ${times}, ${supply}. Stores your text only — not a prescription or dose advice.`;
}

/** Parse optional remaining-dose field; blank means omit (unknown). */
export function parseSupplyCount(text: string): number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  if (!/^\d{1,4}$/.test(trimmed)) throw new Error('Remaining doses must be a whole number from 0 to 9999, or left blank.');
  return Number(trimmed);
}
