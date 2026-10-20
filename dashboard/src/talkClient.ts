import type { ToolCaller } from './client';
import type { Person } from './contracts';

export type TalkAction = { type: 'family_message'; message: string } | {
  type: 'appointment'; title: string; start_local: string; end_local: string; time_zone: string;
};
export type TalkReply = {
  person: Person; question: string; conversation_id: string; answer: string; speech: string;
  evidence: { id: string; kind: 'note'|'appointment'|'medication'|'relationship'; text: string; at: string|null }[];
  action: TalkAction|null; sources_checked: string[]; notice: string; model_id: string;
};
export type TalkClient = {
  ask(person: string, question: string, timeZone: string, conversationId?: string): Promise<TalkReply>;
};
const invalid = () => new Error('The conversation response could not be verified.');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 16000): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw invalid();
  return value;
}
export function parseTalkReply(value: unknown, person: string, question: string, conversationId?: string): TalkReply {
  const r = object(value), p = object(r.person);
  if (p.id !== person || !['elder', 'family'].includes(String(p.role)) || r.question !== question) throw invalid();
  const id = text(r.conversation_id, 200), answer = text(r.answer);
  if (conversationId && id !== conversationId) throw invalid();
  if (!Array.isArray(r.evidence) || r.evidence.length > 20 || !Array.isArray(r.sources_checked) || r.sources_checked.length > 20) throw invalid();
  const evidence = r.evidence.map(value => {
    const e = object(value);
    if (!['note', 'appointment', 'medication', 'relationship'].includes(String(e.kind))) throw invalid();
    if (e.at !== null && (typeof e.at !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(e.at) || !Number.isFinite(Date.parse(e.at)))) throw invalid();
    return { id: text(e.id, 300), kind: e.kind as TalkReply['evidence'][number]['kind'], text: text(e.text), at: e.at as string|null };
  });
  if (new Set(evidence.map(e => e.id)).size !== evidence.length) throw invalid();
  let action: TalkAction|null = null;
  if (r.action !== null) {
    const a = object(r.action);
    if (a.type === 'family_message') action = { type: a.type, message: text(a.message, 4000) };
    else if (a.type === 'appointment') {
      const start = text(a.start_local, 30), end = text(a.end_local, 30);
      if (![start, end].every(t => /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?$/.test(t) && Number.isFinite(Date.parse(t))) || end <= start) throw invalid();
      const zone = text(a.time_zone, 80);
      try { new Intl.DateTimeFormat('en', { timeZone: zone }); } catch { throw invalid(); }
      action = { type: a.type, title: text(a.title, 200), start_local: start, end_local: end, time_zone: zone };
    } else throw invalid();
  }
  return {
    person: { id: person, name: text(p.name, 200), role: p.role as Person['role'] },
    question, conversation_id: id, answer, speech: text(r.speech, 2000), evidence, action,
    sources_checked: r.sources_checked.map(v => text(v, 200)), notice: text(r.notice), model_id: text(r.model_id, 300),
  };
}
export function createTalkClient(call: ToolCaller): TalkClient {
  return { async ask(person, question, timeZone, conversationId) {
    return parseTalkReply(await call('talk_to_saarthi', {
      person, question, time_zone: timeZone, conversation_id: conversationId ?? null,
    }), person, question, conversationId);
  } };
}
