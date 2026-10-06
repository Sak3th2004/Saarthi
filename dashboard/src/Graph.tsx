import { useState } from 'react';
import type { MemoryGraph } from './contracts';

export function MemoryMap({ graph }: { graph: MemoryGraph }) {
  const [selected, select] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const visible = graph.nodes.filter(n => n.kind === 'person' || filter === 'all' || n.kind === filter);
  const root = visible.find(n => n.kind === 'person');
  const children = visible.filter(n => n !== root);
  const positions = new Map<string, { x: number; y: number }>();
  if (root) positions.set(root.id, { x: 300, y: 190 });
  children.forEach((node, index) => {
    const angle = (index / children.length) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.id, { x: 300 + Math.cos(angle) * 228, y: 190 + Math.sin(angle) * 135 });
  });
  const active = graph.nodes.find(n => n.id === selected);
  return <>
    <div className="graph-toolbar"><span className="muted">Select a record to see its saved details.</span><label className="sr-only" htmlFor="graph-filter">Filter memory map</label><select id="graph-filter" value={filter} onChange={event => { setFilter(event.target.value); select(null); }}><option value="all">All records</option><option value="event">Notes</option><option value="medication">Medications</option><option value="appointment">Appointments</option></select></div>
    {graph.nodes.length === 0 ? <p className="empty">No graph records were returned.</p> : <svg viewBox="0 0 600 380" className="memory-map" role="group" aria-label="Saved record relationships">
      <title>Saved records connected to this person</title>
      {graph.edges.map(edge => { const source = positions.get(edge.source); const target = positions.get(edge.target); return source && target ? <line key={edge.source + edge.target} x1={source.x} y1={source.y} x2={target.x} y2={target.y} /> : null; })}
      {visible.map(node => { const point = positions.get(node.id)!; return <g key={node.id} role="button" tabIndex={0} aria-label={`${node.kind}: ${node.label}`} aria-pressed={selected === node.id} onClick={() => select(node.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(node.id); } }} className={`graph-node ${node.kind} ${selected === node.id ? 'selected' : ''}`} transform={`translate(${point.x}, ${point.y})`}><title>{node.label}</title><circle r={node.kind === 'person' ? 28 : 10} /><text y={node.kind === 'person' ? 47 : 27} textAnchor="middle">{node.label.length > 22 ? node.label.slice(0, 20) + '…' : node.label}</text></g>; })}
    </svg>}
    <div className="graph-legend"><span><i className="dot person" />Person</span><span><i className="dot event" />Note</span><span><i className="dot medication" />Medication</span><span><i className="dot appointment" />Appointment</span></div>
    {active && <div className="record-detail"><h3>{active.label}</h3><dl>{Object.entries(active.record).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{Array.isArray(value) ? value.join(', ') : value === null ? 'Not recorded' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></div>}
    {Object.values(graph.truncated).some(Boolean) && <p className="view-note">This map shows a limited selection. Older notes may still be found through “Find a saved detail”.</p>}
  </>;
}
