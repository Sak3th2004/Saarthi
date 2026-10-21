import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MemoryMap } from './Graph';
import { CompanionFace } from './CompanionFace';
import type { MemoryGraph } from './contracts';
import type { TalkReply } from './talkClient';

const graph: MemoryGraph = {
  schema_version: 1, person_id: 'parent', generated_at: '2026-10-20T10:00:00Z', truncated: {}, speech: 'Saved records',
  nodes: [
    { id: 'root', kind: 'person', label: 'Parent', record: { id: 'parent', name: 'Parent', role: 'elder' } },
    { id: 'event:exact', kind: 'event', label: 'note', record: { type: 'note', detail: 'The folder is by the door.', at: '2026-10-20T10:00:00Z' } },
  ], edges: [{ source: 'root', target: 'event:exact', relation: 'EXPERIENCED' }],
};
const evidence: TalkReply['evidence'][number] = { id: 'citation', kind: 'note', text: 'The folder is by the door.', at: '2026-10-20T10:00:00Z', graph_node_ids: ['event:exact'] };
describe('answer to memory graph connection', () => {
  it('highlights and opens only an exact saved record', () => {
    const html = renderToStaticMarkup(createElement(MemoryMap, { graph, evidence }));
    expect(html).toContain('1 matching saved record is outlined');
    expect(html).toContain('note, cited in this answer');
    expect(html).toContain('evidence-ring');
    expect(html).toContain('record-detail');
    expect(html).toContain('Back to conversation');
  });
  it('does not match similar text when the cited record has changed or left the loaded view', () => {
    const html = renderToStaticMarkup(createElement(MemoryMap, { graph, evidence: { ...evidence, graph_node_ids: ['event:old'] } }));
    expect(html).toContain('This exact record is not in the loaded map');
    expect(html).not.toContain('evidence-ring');
    expect(html).not.toContain('record-detail');
  });
  it('gives each companion its own paint IDs and keeps the decorative face out of the accessibility tree', () => {
    const html = renderToStaticMarkup(<><CompanionFace mode="ready"/><CompanionFace mode="listening"/></>);
    const ids = [...html.matchAll(/ id="([^"]+)"/g)].map(match => match[1]);
    expect(new Set(ids).size).toBe(ids.length);
    expect(html.match(/aria-hidden="true"/g)).toHaveLength(2);
  });
});
