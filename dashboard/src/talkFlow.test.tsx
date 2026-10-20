import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { TalkToSaarthi } from './TalkToSaarthi';
import type { TalkReply } from './talkClient';

const hooks = vi.hoisted(() => ({ values: [] as unknown[], index: 0, cleanups: [] as (() => void)[] }));
vi.mock('react', async original => ({
  ...await original<typeof import('react')>(),
  useState(initial: unknown) {
    const i = hooks.index++;
    if (!(i in hooks.values)) hooks.values[i] = typeof initial === 'function' ? initial() : initial;
    return [hooks.values[i], (value: unknown) => { hooks.values[i] = typeof value === 'function' ? value(hooks.values[i]) : value; }];
  },
  useRef(initial: unknown) {
    const i = hooks.index++;
    if (!(i in hooks.values)) hooks.values[i] = { current: initial };
    return hooks.values[i];
  },
  useEffect(effect: () => (() => void), deps: unknown[]) {
    const i = hooks.index++;
    if (!(i in hooks.values)) { hooks.values[i] = deps; hooks.cleanups.push(effect()); }
  },
}));
type Props = { children?: ReactNode; [key: string]: unknown };
function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  return [node, ...elements(node.props.children)];
}
const person = { id: 'parent', name: 'Parent', role: 'elder' as const };
const reply: TalkReply = {
  person, question: 'A unique question', conversation_id: 'context',
  answer: 'Saved evidence', speech: 'Saved evidence', evidence: [], sources_checked: ['notes'],
  action: { type: 'family_message', message: 'Draft only' }, notice: 'No message sent', model_id: 'model',
};
beforeEach(() => { hooks.index = 0; hooks.values = []; hooks.cleanups = []; vi.stubGlobal('window', {}); });
afterEach(() => { hooks.cleanups.forEach(cleanup => cleanup?.()); vi.unstubAllGlobals(); });
describe('conversation controls', () => {
  it('requires submit, carries followup context, and only hands off draft on explicit click', async () => {
    const client = { ask: vi.fn().mockResolvedValue(reply) }, onDraft = vi.fn();
    const render = () => { hooks.index = 0; return elements(TalkToSaarthi({ person, client, onDraft })); };
    const change = (text: string) => (render().find(n => n.type === 'textarea')!.props.onChange as Function)({ target: { value: text } });
    const submit = () => (render().find(n => n.type === 'form')!.props.onSubmit as Function)({ preventDefault() {} });
    render(); expect(client.ask).not.toHaveBeenCalled();
    change('A unique question'); submit();
    await vi.waitFor(() => expect(render().some(n => n.props.children === 'Use this draft')).toBe(true));
    expect(onDraft).not.toHaveBeenCalled();
    (render().find(n => n.props.children === 'Use this draft')!.props.onClick as Function)();
    expect(onDraft).toHaveBeenCalledExactlyOnceWith(reply.action);
    change('Tell me about that'); submit();
    await vi.waitFor(() => expect(client.ask).toHaveBeenCalledTimes(2));
    expect(client.ask.mock.calls[1]).toEqual(['parent', 'Tell me about that', expect.any(String), 'context']);
  });
  it('ignores a late answer after the notebook is closed', async () => {
    let resolve!: (value: TalkReply) => void;
    const client = { ask: vi.fn(() => new Promise<TalkReply>(done => { resolve = done; })) };
    const render = () => { hooks.index = 0; return elements(TalkToSaarthi({ person, client, onDraft: vi.fn() })); };
    (render().find(n => n.type === 'textarea')!.props.onChange as Function)({ target: { value: 'question' } });
    (render().find(n => n.type === 'form')!.props.onSubmit as Function)({ preventDefault() {} });
    hooks.cleanups.forEach(cleanup => cleanup?.());
    resolve(reply); await Promise.resolve(); await Promise.resolve();
    expect(hooks.values[2]).toEqual([]);
  });
  it('voice transcription fills an editable question without contacting backend', () => {
    let recognition: any;
    const abort = vi.fn();
    class Recognition { onresult: Function|undefined; onend: Function|undefined; abort = abort; start() {} constructor() { recognition = this; } }
    vi.stubGlobal('window', { SpeechRecognition: Recognition });
    const client = { ask: vi.fn() };
    const render = () => { hooks.index = 0; return elements(TalkToSaarthi({ person, client, onDraft: vi.fn() })); };
    (render().find(n => n.props['aria-label'] === 'Use microphone')!.props.onClick as Function)();
    recognition.onresult({ results: [[{ transcript: 'Where is my notebook?' }]] });
    expect(render().find(n => n.type === 'textarea')!.props.value).toBe('Where is my notebook?');
    expect(client.ask).not.toHaveBeenCalled();
    hooks.cleanups.forEach(cleanup => cleanup?.());
    expect(abort).toHaveBeenCalled();
  });
});
