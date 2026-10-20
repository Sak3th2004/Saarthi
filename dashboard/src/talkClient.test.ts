import { describe, expect, it, vi } from 'vitest';
import { createTalkClient, parseTalkReply, type TalkReply } from './talkClient';
import { microphoneError, recognitionFactory } from './speech';

export const reply = (): TalkReply => ({
  person: { id: 'parent', name: 'Parent', role: 'elder' },
  question: 'Where is the folder?', conversation_id: 'session',
  answer: 'Saved: folder is by the door.', speech: 'Saved: folder is by the door.',
  evidence: [{ id: 'note-1', kind: 'note', text: 'folder is by the door.', at: '2026-10-20T10:00:00Z' }],
  action: null, sources_checked: ['notes'], notice: 'Saved records only.', model_id: 'configured-model',
});
describe('Talk to Saarthi verification', () => {
  it('calls the real MCP tool and preserves the conversation ID for followups', async () => {
    const call = vi.fn().mockResolvedValue(reply());
    const client = createTalkClient(call);
    await client.ask('parent', reply().question, 'Asia/Calcutta', 'session');
    expect(call).toHaveBeenCalledExactlyOnceWith('talk_to_saarthi', {
      person: 'parent', question: reply().question, time_zone: 'Asia/Calcutta', conversation_id: 'session',
    });
  });
  it.each([
    { person: { id: 'other', name: 'Other', role: 'elder' } },
    { question: 'different' }, { conversation_id: 'different' }, { speech: 'unsupported speech' },
    { evidence: [{ id: 'one', kind: 'imagined', text: 'invented', at: null }] },
    { evidence: [...reply().evidence, ...reply().evidence] },
    { action: { type: 'send_now', message: 'unreviewed' } },
    { action: { type: 'family_message', message: 'x'.repeat(4001) } },
    { action: { type: 'appointment', title: 'Walk', start_local: '2026-10-22T13:00', end_local: '2026-10-22T12:00', time_zone: 'UTC' } },
  ])('rejects mismatched or unsafe response %j', change => {
    expect(() => parseTalkReply({ ...reply(), ...change }, 'parent', reply().question, 'session')).toThrow();
  });
  it('accepts an editable draft without calling a write tool', async () => {
    const call = vi.fn().mockResolvedValue({ ...reply(), action: { type: 'family_message', message: 'Saved update' } });
    const result = await createTalkClient(call).ask('parent', reply().question, 'UTC');
    expect(result.action).toEqual({ type: 'family_message', message: 'Saved update' });
    expect(call).toHaveBeenCalledTimes(1);
  });
  it('provides typed fallback and does not start recognition during feature detection', () => {
    expect(recognitionFactory({} as Window)).toBeNull();
    const start = vi.fn();
    class Speech { start = start; }
    const factory = recognitionFactory({ webkitSpeechRecognition: Speech } as unknown as Window);
    factory!();
    expect(start).not.toHaveBeenCalled();
    expect(microphoneError('not-allowed')).toMatch(/type your question/);
  });
});
