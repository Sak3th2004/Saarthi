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
  if (ids.size !== nodes.length || nodes.length > 301) throw invalid();
  const roots = nodes.filter(n => n.kind === 'person');
  if (roots.length !== 1) throw invalid();
  const member = person(roots[0].record);
  if (member.id !== owner) throw invalid();
  const edges = list(g.edges).map(value => {
    const e = object(value);
    if (!['TAKES', 'HAS_APPOINTMENT', 'EXPERIENCED'].includes(text(e.relation))) throw invalid();
    const source = text(e.source), target = text(e.target);
    if (source !== roots[0].id || !ids.has(target) || target === source) throw invalid();
    const targetNode = nodes.find(n => n.id === target)!;
    const expected = { medication: 'TAKES', appointment: 'HAS_APPOINTMENT', event: 'EXPERIENCED', person: '' }[targetNode.kind];
    if (e.relation !== expected) throw invalid();
    return { source, target, relation: e.relation as MemoryGraph['edges'][number]['relation'] };
  });
  if (edges.length !== nodes.length - 1 || new Set(edges.map(e => e.target)).size !== edges.length) throw invalid();
  const truncated = object(g.truncated);
  for (const kind of ['medication', 'appointment', 'event']) if (typeof truncated[kind] !== 'boolean') throw invalid();
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
    async queryMemory(savedPerson, question): Promise<MemoryAnswer> {
      const r = object(await call('query_memory', { person: savedPerson, question }));
      const member = person(r.person);
      if (member.id !== savedPerson || r.question !== question) throw invalid();
      return { person: member, question: text(r.question), answer: text(r.answer), supporting_events: list(r.supporting_events).map(event), speech: text(r.speech) };
    },
  };
}

// Development uses a loopback-only Vite proxy. No AWS or database credentials enter the browser.
export const client = createDashboardClient(httpCaller(() => new URL('/mcp', window.location.href)));
