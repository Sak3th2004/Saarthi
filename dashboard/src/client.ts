import type { DashboardClient, EventResult, HouseholdView, MemoryAnswer, MemoryGraph, Person, SavedEvent } from './contracts';

type ObjectValue = Record<string, unknown>;
export type ToolCaller = (name: string, args: ObjectValue) => Promise<unknown>;
const invalid = () => new Error('The server returned an incomplete or inconsistent record.');
function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as ObjectValue;
}
function text(value: unknown): string { if (typeof value !== 'string') throw invalid(); return value; }
function list(value: unknown): unknown[] { if (!Array.isArray(value)) throw invalid(); return value; }
function date(value: unknown): string {
  const result = text(value);
  if (!/(Z|[+-]\d\d:\d\d)$/.test(result) || !Number.isFinite(Date.parse(result))) throw invalid();
  return result;
}
function person(value: unknown): Person {
  const v = object(value);
  if (v.role !== 'elder' && v.role !== 'family') throw invalid();
  return { id: text(v.id), name: text(v.name), role: v.role };
}
function event(value: unknown): SavedEvent {
  const v = object(value);
  return { type: text(v.type), detail: text(v.detail), at: date(v.at) };
}

export function parseView(value: unknown): HouseholdView {
  const g = object(value);
  if (g.schema_version !== 1) throw invalid();
  const owner = text(g.person_id);
  const nodes = list(g.nodes).map(value => {
    const n = object(value);
    if (!['person', 'medication', 'appointment', 'event'].includes(text(n.kind))) throw invalid();
    return { id: text(n.id), kind: n.kind as MemoryGraph['nodes'][number]['kind'], label: text(n.label), record: object(n.record) };
  });
  const ids = new Set(nodes.map(n => n.id));
  if (ids.size !== nodes.length || nodes.length > 401) throw invalid();
  const people = nodes.filter(n => n.kind === 'person');
  const personIds = new Set<string>();
  for (const node of people) {
    const saved = person(node.record);
    if (!saved.id.trim() || !saved.name.trim() || node.label !== saved.name || personIds.has(saved.id) ||
        Object.keys(node.record).some(key => !['id', 'name', 'role'].includes(key))) throw invalid();
    personIds.add(saved.id);
  }
  if (people.length > 101) throw invalid();
  const roots = people.filter(n => n.record.id === owner);
  if (roots.length !== 1) throw invalid();
  const member = person(roots[0].record);
  if (member.id !== owner) throw invalid();
  const edges = list(g.edges).map(value => {
    const e = object(value);
    if (!['TAKES', 'HAS_APPOINTMENT', 'EXPERIENCED', 'RELATED_TO'].includes(text(e.relation))) throw invalid();
    const source = text(e.source), target = text(e.target);
    if (!ids.has(source) || !ids.has(target) || target === source) throw invalid();
    const sourceNode = nodes.find(n => n.id === source)!;
    const targetNode = nodes.find(n => n.id === target)!;
    if (e.relation === 'RELATED_TO') {
      if (sourceNode.kind !== 'person' || targetNode.kind !== 'person' ||
          (source !== roots[0].id && target !== roots[0].id) || typeof e.detail !== 'string' || !e.detail.trim()) throw invalid();
      return { source, target, relation: 'RELATED_TO' as const, detail: e.detail };
    }
    if (source !== roots[0].id || (e.detail !== undefined && e.detail !== null)) throw invalid();
    const expected = { medication: 'TAKES', appointment: 'HAS_APPOINTMENT', event: 'EXPERIENCED', person: '' }[targetNode.kind];
    if (e.relation !== expected) throw invalid();
    return { source, target, relation: e.relation as MemoryGraph['edges'][number]['relation'] };
  });
  const edgeKeys = new Set(edges.map(e => JSON.stringify([e.source, e.target, e.relation, e.detail ?? null])));
  if (edgeKeys.size !== edges.length || edges.filter(e => e.relation === 'RELATED_TO').length > 100) throw invalid();
  for (const node of nodes) {
    if (node.id === roots[0].id) continue;
    const links = edges.filter(edge => edge.source === node.id || edge.target === node.id);
    if (!links.length || (node.kind !== 'person' && links.length !== 1)) throw invalid();
  }
  const truncated = object(g.truncated);
  for (const kind of ['medication', 'appointment', 'event']) if (typeof truncated[kind] !== 'boolean') throw invalid();
  if (truncated.person !== undefined && typeof truncated.person !== 'boolean') throw invalid();
  if (Object.values(truncated).some(value => typeof value !== 'boolean')) throw invalid();
  const graph: MemoryGraph = {
    schema_version: 1, person_id: owner, generated_at: date(g.generated_at),
    nodes, edges, truncated: truncated as Record<string, boolean>, speech: text(g.speech),
  };
  return {
    person: member, graph,
    medications: nodes.filter(n => n.kind === 'medication').map(({ record: r }) => {
      if (r.supply_count !== null && r.supply_count !== undefined && (typeof r.supply_count !== 'number' || !Number.isInteger(r.supply_count) || r.supply_count < 0)) throw invalid();
      return { name: text(r.name), dose: text(r.dose), schedule: list(r.schedule).map(text), supply_count: r.supply_count as number | null | undefined };
    }),
    appointments: nodes.filter(n => n.kind === 'appointment').map(({ record: r }) => ({ id: text(r.id), kind: text(r.kind), when: date(r.when), status: text(r.status) })),
    events: nodes.filter(n => n.kind === 'event').map(n => event(n.record)),
  };
}

/** Real MCP calls only. A failed mutation is never automatically retried. */
export function httpCaller(endpoint: () => URL): ToolCaller {
  return async (name, args) => {
    const { Client, StreamableHTTPClientTransport } = await import('@modelcontextprotocol/client');
    const sdk = new Client({ name: 'saarthi-family-dashboard', version: '0.1.0' });
    const transport = new StreamableHTTPClientTransport(endpoint());
    try {
      await sdk.connect(transport, { timeout: 15_000 });
      const available = await sdk.listTools({}, { timeout: 15_000 });
      if (!available.tools.some(tool => tool.name === name)) {
        throw new Error('This server does not provide the requested notebook tool.');
      }
      const result = await sdk.callTool({ name, arguments: args }, { timeout: 45_000 });
      if (result.isError || result.structuredContent === undefined) throw invalid();
      return result.structuredContent;
    } finally {
      // Closing a successful response must not turn a saved record into a failure.
      await sdk.close().catch(() => undefined);
    }
  };
}

export function createDashboardClient(call: ToolCaller): DashboardClient {
  return {
    async load(savedPerson) { return parseView(await call('get_memory_graph', { person: savedPerson, limit: 30 })); },
    async recordEvent(savedPerson, input): Promise<EventResult> {
      const r = object(await call('record_event', { person: savedPerson, ...input }));
      const member = person(r.person);
      if (member.id !== savedPerson) throw invalid();
      return { person: member, event: event(r.event), speech: text(r.speech) };
    },
    async queryMemory(savedPerson, question, timeZone): Promise<MemoryAnswer> {
      const r = object(await call('query_memory', { person: savedPerson, question, ...(timeZone ? { time_zone: timeZone } : {}) }));
      const member = person(r.person);
      if (member.id !== savedPerson || r.question !== question) throw invalid();
      return { person: member, question: text(r.question), answer: text(r.answer), supporting_events: list(r.supporting_events).map(event), speech: text(r.speech) };
    },
  };
}

// Development uses a loopback-only Vite proxy. No AWS or database credentials enter the browser.
export const client = createDashboardClient(httpCaller(() => new URL('/mcp', window.location.href)));
