export interface Person { id: string; name: string; role: 'elder' | 'family'; email?: string | null }
export interface SavedEvent { type: string; detail: string; at: string }
export interface Medication { name: string; dose: string; schedule: string[]; supply_count?: number | null }
export type ExternalBookingStatus = 'not_requested' | 'calendar_saved';
export interface Appointment {
  id: string;
  kind: string;
  when: string;
  /** Local schedule status (e.g. scheduled) — not provider confirmation. */
  status: string;
  /** Same honesty as list_appointments: calendar-linked vs record-only. */
  external_booking_status: ExternalBookingStatus;
}
export interface GraphNode {
  id: string;
  kind: 'person' | 'medication' | 'appointment' | 'event';
  label: string;
  record: Record<string, unknown>;
}
export interface MemoryGraph {
  schema_version: 1;
  person_id: string;
  generated_at: string;
  nodes: GraphNode[];
  edges: { source: string; target: string; relation: 'TAKES' | 'HAS_APPOINTMENT' | 'EXPERIENCED' | 'RELATED_TO'; detail?: string }[];
  truncated: Record<string, boolean>;
  speech: string;
}
export interface HouseholdView {
  person: Person;
  medications: Medication[];
  appointments: Appointment[];
  events: SavedEvent[];
  graph: MemoryGraph;
}
export interface EventResult { person: Person; event: SavedEvent; speech: string }
export interface MemoryAnswer {
  person: Person;
  question: string;
  answer: string;
  supporting_events: SavedEvent[];
  speech: string;
}
export interface DoseInput { med: string; taken: boolean; at: string }
export interface DoseResult {
  person: Person;
  dose: { med: string; status: 'taken' | 'missed'; at: string };
  already_logged: boolean;
  speech: string;
}
export interface CheckInResult {
  person: Person;
  ok: false | null;
  missed_doses_today: number;
  last_activity: string | null;
  concerns: string[];
  speech: string;
}
export interface DashboardClient {
  load(person: string): Promise<HouseholdView>;
  recordEvent(person: string, event: { type: string; detail: string }): Promise<EventResult>;
  queryMemory(person: string, question: string, timeZone?: string): Promise<MemoryAnswer>;
  logDose(person: string, input: DoseInput): Promise<DoseResult>;
  checkIn(person: string): Promise<CheckInResult>;
}
