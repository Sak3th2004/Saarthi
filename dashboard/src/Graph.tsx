import { useId, useState } from 'react';
import type { MemoryGraph } from './contracts';

export function MemoryMap({ graph }: { graph: MemoryGraph }) {
  const [selected, select] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const arrowId = useId().replaceAll(':', '') + '-relationship-arrow';
  const root = graph.nodes.find(n => n.kind === 'person' && n.record.id === graph.person_id);
  const visible = graph.nodes.filter(n => n === root || filter === 'all' || n.kind === filter);
  const children = visible.filter(n => n !== root);
  const positions = new Map<string, { x: number; y: number }>();
  if (root) positions.set(root.id, { x: 300, y: 190 });
  children.forEach((node, index) => {
    const angle = (index / children.length) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.id, { x: 300 + Math.cos(angle) * 228, y: 190 + Math.sin(angle) * 135 });
  });
  const active = graph.nodes.find(n => n.id === selected);
  const relationships = graph.edges.filter(edge => edge.relation === 'RELATED_TO' &&
    (edge.source === active?.id || edge.target === active?.id));
  const label = (id: string) => graph.nodes.find(node => node.id === id)?.label ?? id;
  return <>
    <div className="graph-toolbar"><span className="muted">Select a record to see its saved details.</span><label className="sr-only" htmlFor="graph-filter">Filter memory map</label><select id="graph-filter" value={filter} onChange={event => { setFilter(event.target.value); select(null); }}><option value="all">All records</option><option value="person">People</option><option value="event">Notes</option><option value="medication">Medications</option><option value="appointment">Appointments</option></select></div>
    {graph.nodes.length === 0 ? <p className="empty">No graph records were returned.</p> : <svg viewBox="0 0 600 380" className="memory-map" role="group" aria-label="Saved record relationships">
      <title>Saved records connected to this person</title>
      <defs><marker id={arrowId} markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6" fill="none" stroke="#82977d" /></marker></defs>
      {graph.edges.map(edge => {
        const source = positions.get(edge.source), target = positions.get(edge.target);
        if (!source || !target) return null;
        const key = JSON.stringify([edge.source, edge.target, edge.relation, edge.detail]);
        if (edge.relation !== 'RELATED_TO') return <line key={key} x1={source.x} y1={source.y} x2={target.x} y2={target.y} />;
        const length = Math.hypot(target.x - source.x, target.y - source.y);
        const dx = (target.x - source.x) / length, dy = (target.y - source.y) / length;
        // Separate opposite directed relationships so neither arrow hides the other.
        const reverse = graph.edges.some(other => other.relation === 'RELATED_TO' && other.source === edge.target && other.target === edge.source);
        const shift = reverse ? 7 : 0;
        const x1 = source.x + dx * 32 - dy * shift, y1 = source.y + dy * 32 + dx * shift;
        const x2 = target.x - dx * 32 - dy * shift, y2 = target.y - dy * 32 + dx * shift;
        return <g key={key}><title>{`${label(edge.source)} → ${label(edge.target)}: ${edge.detail}`}</title><line x1={x1} y1={y1} x2={x2} y2={y2} markerEnd={`url(#${arrowId})`} /><text x={(x1 + x2) / 2 - dy * 8} y={(y1 + y2) / 2 + dx * 8} textAnchor="middle" fontSize="10" fill="#526b58">{edge.detail!.length > 20 ? edge.detail!.slice(0, 18) + '…' : edge.detail}</text></g>;
      })}
      {visible.map(node => { const point = positions.get(node.id)!; return <g key={node.id} role="button" tabIndex={0} aria-label={`${node.kind}: ${node.label}`} aria-pressed={selected === node.id} onClick={() => select(node.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(node.id); } }} className={`graph-node ${node.kind} ${selected === node.id ? 'selected' : ''}`} transform={`translate(${point.x}, ${point.y})`}><title>{node.label}</title><circle r={node.kind === 'person' ? 28 : 10} /><text y={node.kind === 'person' ? 47 : 27} textAnchor="middle">{node.label.length > 22 ? node.label.slice(0, 20) + '…' : node.label}</text></g>; })}
    </svg>}
    <div className="graph-legend"><span><i className="dot person" />People</span><span><i className="dot event" />Note</span><span><i className="dot medication" />Medication</span><span><i className="dot appointment" />Appointment</span></div>
    {graph.edges.some(edge => edge.relation === 'RELATED_TO') && <p className="view-note">Arrows follow each saved relationship from its source person to its target. Select a person to read the full relationship.</p>}
    {active && <div className="record-detail"><h3>{active.label}</h3><dl>{Object.entries(active.record).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{Array.isArray(value) ? value.join(', ') : value === null ? 'Not recorded' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></div>}
    {active && relationships.length > 0 && <div className="record-detail"><h3>Saved relationships</h3><ul>{relationships.map(edge => <li key={JSON.stringify([edge.source, edge.target, edge.detail])}><strong>{label(edge.source)} → {label(edge.target)}</strong><p>Relationship: {edge.detail}</p></li>)}</ul></div>}
    {Object.values(graph.truncated).some(Boolean) && <p className="view-note">This map shows a limited selection. Older notes may still be found through “Find a saved detail”.</p>}
  </>;
}
