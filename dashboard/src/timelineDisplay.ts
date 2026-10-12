import type { SavedEvent } from './contracts';
import { deliveryStatusLabel, type DeliveryStatus } from './householdNotifyClient';

const INTERNAL_TYPES = new Set(['agent_action', 'household_setup']);

/** Caregiver-facing labels for MCP / agent operation ids — never show snake_case tool names. */
const OPERATION_LABELS: Record<string, string> = {
  list_appointments: 'Listed upcoming appointments',
  book_appointment: 'Saved appointment record',
  notify_family: 'Family notification',
  log_dose: 'Dose log',
  get_medication_schedule: 'Medication schedule check',
  query_memory: 'Memory lookup',
  check_in: 'Check-in',
  get_household_summary: 'Household summary',
  record_event: 'Saved note',
  get_memory_graph: 'Memory map',
};

const TOOL_NAME_PATTERN = new RegExp(
  Object.keys(OPERATION_LABELS)
    .sort((a, b) => b.length - a.length)
    .join('|'),
  'g',
);

export function isInternalEvent(event: SavedEvent): boolean {
  return INTERNAL_TYPES.has(event.type);
}

export function operationLabel(operation: string): string {
  return OPERATION_LABELS[operation] ?? operation.replaceAll('_', ' ');
}

/** Replace known tool ids in caregiver-visible text (feed_summary may still embed them). */
export function humanizeAgentText(text: string): string {
  return text.replace(TOOL_NAME_PATTERN, match => OPERATION_LABELS[match] ?? match);
}

type AgentActionDetail = {
  feed_summary?: unknown;
  evidence?: unknown;
  specialist?: unknown;
  operation?: unknown;
  status?: unknown;
  speech?: unknown;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function parseAgentAction(detail: string): AgentActionDetail | null {
  try {
    const parsed: unknown = JSON.parse(detail);
    return asRecord(parsed);
  } catch {
    return null;
  }
}

/** Compact, caregiver-readable flags — never free-form model text. */
export function formatEvidenceLine(evidence: unknown): string | null {
  const record = asRecord(evidence);
  if (!record) return null;
  const parts: string[] = [];
  for (const [key, value] of Object.entries(record)) {
    if (key === 'operation' || value === null || value === undefined) continue;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      const display = typeof value === 'string' ? humanizeAgentText(value) : value;
      parts.push(`${key.replaceAll('_', ' ')}: ${display}`);
    }
  }
  return parts.length ? parts.join(' · ') : null;
}

function fallbackAgentSummary(action: AgentActionDetail): string | null {
  const specialist = typeof action.specialist === 'string' ? action.specialist.trim() : '';
  const operation = typeof action.operation === 'string' ? operationLabel(action.operation.trim()) : '';
  const status = typeof action.status === 'string' ? action.status.trim() : '';
  const parts = [specialist, operation, status].filter(part => part.length > 0);
  return parts.length ? parts.join(' · ') : null;
}

/**
 * Timeline body for one saved event.
 * agent_action prefers feed_summary (+ compact evidence); never surfaces speech, raw JSON,
 * or caregiver-visible tool ids such as list_appointments.
 */
export function timelineEventBody(event: SavedEvent): { summary: string; evidence: string | null } {
  if (event.type === 'notify' && event.detail.trimStart().startsWith('{')) {
    try {
      const record = asRecord(JSON.parse(event.detail));
      if (record && typeof record.message === 'string') {
        const known = ['accepted', 'uncertain', 'failed', 'recorded_only', 'pending'];
        const status = typeof record.status === 'string' && known.includes(record.status)
          ? deliveryStatusLabel(record.status as DeliveryStatus)
          : 'Email delivery status is unavailable';
        return { summary: record.message, evidence: status };
      }
    } catch { /* Keep incomplete audit details out of the family timeline. */ }
    return { summary: 'Family message recorded', evidence: 'Email delivery status is unavailable' };
  }
  if (event.type !== 'agent_action') {
    return { summary: event.detail, evidence: null };
  }
  const action = parseAgentAction(event.detail);
  if (!action) {
    return { summary: 'System activity recorded', evidence: null };
  }
  const feed = typeof action.feed_summary === 'string' ? action.feed_summary.trim() : '';
  const summary = humanizeAgentText(feed || fallbackAgentSummary(action) || 'System activity recorded');
  return { summary, evidence: formatEvidenceLine(action.evidence) };
}
