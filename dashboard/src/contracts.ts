export interface Person { id: string; name: string; role: 'elder' | 'family' }
export interface SavedEvent { type: string; detail: string; at: string }
export interface Medication { name: string; dose: string; schedule: string[]; supply_count?: number | null }
export interface Appointment { id: string; kind: string; when: string; status: string }
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
  edges: { source: string; target: string; relation: 'TAKES' | 'HAS_APPOINTMENT' | 'EXPERIENCED' }[];
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
export interface DashboardClient {
  load(person: string): Promise<HouseholdView>;
  recordEvent(person: string, event: { type: string; detail: string }): Promise<EventResult>;
  queryMemory(person: string, question: string): Promise<MemoryAnswer>;
}
