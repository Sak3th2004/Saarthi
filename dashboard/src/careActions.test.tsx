import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CareActions, doseTimeToISO } from './CareActions';
import { createDashboardClient } from './client';

// Handler and state tests with existing dependencies; not a browser-rendering test.
const hooks = vi.hoisted(() => ({ values: [] as unknown[], index: 0 }));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState(initial: unknown) {
    const index = hooks.index++;
    if (!(index in hooks.values)) hooks.values[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks.values[index], (value: unknown) => { hooks.values[index] = value; }];
  },
  useRef(initial: unknown) {
    const index = hooks.index++;
    if (!(index in hooks.values)) hooks.values[index] = { current: initial };
    return hooks.values[index];
  },
}));
type Props = { children?: ReactNode; [key: string]: unknown };
function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  return [node, ...elements(node.props.children)];
}
function words(node: ReactNode): string {
  if (Array.isArray(node)) return node.map(words).join('');
  if (isValidElement<Props>(node)) return words(node.props.children);
  return typeof node === 'string' || typeof node === 'number' ? String(node) : '';
}
const person = { id: 'caregiver-saved-person', name: 'Saved parent', role: 'elder' as const };
const input = { med: 'Caregiver entered medicine', taken: true, at: '2026-10-17T06:30:00.000Z' };
const doseResult = { person, dose: { med: input.med, status: 'taken', at: input.at }, already_logged: false, speech: 'Recorded' };
const checkResult = { person, ok: null, missed_doses_today: 0, last_activity: null, concerns: [], speech: 'Not enough information to confirm how they are doing.' };
beforeEach(() => { hooks.values = []; hooks.index = 0; });

describe('care tool contracts', () => {
  it.each([true, false])('passes the explicit dose outcome and timestamp to MCP: taken=%s', async taken => {
    const call = vi.fn().mockResolvedValue({ ...doseResult, dose: { ...doseResult.dose, status: taken ? 'taken' : 'missed' } });
    const result = await createDashboardClient(call).logDose(person.id, { ...input, taken });
    expect(call).toHaveBeenCalledExactlyOnceWith('log_dose', { person: person.id, ...input, taken });
    expect(result.already_logged).toBe(false);
  });
  it('returns the original timestamp when the server detects an existing dose', async () => {
    const at = '2026-10-17T06:29:00Z';
    const call = vi.fn().mockResolvedValue({ ...doseResult, already_logged: true, dose: { ...doseResult.dose, at } });
    expect(await createDashboardClient(call).logDose(person.id, input)).toMatchObject({ already_logged: true, dose: { at } });
  });
  it.each(['person', 'med', 'status', 'at', 'duplicate'])('rejects inconsistent dose results: %s', field => {
    const result = structuredClone(doseResult);
    if (field === 'person') result.person.id = 'someone-else';
    if (field === 'med') result.dose.med = 'different';
    if (field === 'status') result.dose.status = 'missed';
    if (field === 'at') result.dose.at = '2026-10-17T12:30:00Z';
    if (field === 'duplicate') delete (result as Partial<typeof result>).already_logged;
    return expect(createDashboardClient(vi.fn().mockResolvedValue(result)).logDose(person.id, input)).rejects.toThrow();
  });
  it('rejects a timestamp without timezone before making a write', async () => {
    const call = vi.fn();
    await expect(createDashboardClient(call).logDose(person.id, { ...input, at: '2026-10-17T12:00:00' })).rejects.toThrow();
    expect(call).not.toHaveBeenCalled();
  });
  it('never retries a dose write on connection failure', async () => {
    const call = vi.fn().mockRejectedValue(new Error('Disconnected'));
    await expect(createDashboardClient(call).logDose(person.id, input)).rejects.toThrow();
    expect(call).toHaveBeenCalledTimes(1);
  });
  it('calls the real check_in tool and preserves unknown wellbeing', async () => {
    const call = vi.fn().mockResolvedValue(checkResult);
    expect((await createDashboardClient(call).checkIn(person.id)).ok).toBeNull();
    expect(call).toHaveBeenCalledExactlyOnceWith('check_in', { person: person.id });
  });
  it.each([
    { ok: true }, { ok: false }, { concerns: ['recorded concern'] }, { missed_doses_today: -1 },
    { missed_doses_today: 0.5 }, { person: { ...person, id: 'another' } }, { last_activity: 'missing timezone' },
  ])('rejects invalid or contradictory Watch results: %j', async change => {
    await expect(createDashboardClient(vi.fn().mockResolvedValue({ ...checkResult, ...change })).checkIn(person.id)).rejects.toThrow();
  });
});

describe('caregiver review and confirmation', () => {
  function setup() {
    const client = { load: vi.fn(), recordEvent: vi.fn(), queryMemory: vi.fn(), logDose: vi.fn().mockResolvedValue(doseResult), checkIn: vi.fn().mockResolvedValue(checkResult) };
    const onSaved = vi.fn();
    const render = () => { hooks.index = 0; return elements(CareActions({ person, medications: [{ name: input.med, dose: 'Saved instructions', schedule: [] }], client, onSaved })); };
    const change = (id: string, value: string) => (render().find(node => node.props.id === id)!.props.onChange as (e: unknown) => void)({ target: { value } });
    const prepare = () => {
      change('dose-medication', input.med); change('dose-status', 'taken'); change('dose-time', '2026-10-17T12:00');
      (render().find(node => node.type === 'form')!.props.onSubmit as (e: unknown) => void)({ preventDefault() {} });
    };
    const click = (label: string) => (render().find(node => node.type === 'button' && words(node.props.children) === label)!.props.onClick as () => void)();
    return { client, onSaved, render, change, prepare, click };
  }
  it('requires review, confirms only once and refreshes the saved notebook', async () => {
    const flow = setup(); flow.prepare();
    expect(flow.client.logDose).not.toHaveBeenCalled();
    expect(flow.render().some(node => words(node.props.children) === 'Review before saving')).toBe(true);
    flow.click('Confirm dose record');
    await vi.waitFor(() => expect(flow.onSaved).toHaveBeenCalledExactlyOnceWith(person.id));
    expect(flow.client.logDose).toHaveBeenCalledExactlyOnceWith(person.id, { ...input, at: doseTimeToISO('2026-10-17T12:00') });
    expect(flow.render().some(node => node.props.role === 'status' && words(node).includes('Dose record saved.'))).toBe(true);
  });
  it('invalidates a review when the caregiver changes a field', () => {
    const flow = setup(); flow.prepare(); flow.change('dose-status', 'missed');
    expect(flow.render().some(node => words(node.props.children) === 'Confirm dose record')).toBe(false);
    expect(flow.client.logDose).not.toHaveBeenCalled();
  });
  it('shows a duplicate truthfully and still refreshes actual records', async () => {
    const flow = setup(); flow.client.logDose.mockResolvedValue({ ...doseResult, already_logged: true });
    flow.prepare(); flow.click('Confirm dose record');
    await vi.waitFor(() => expect(flow.onSaved).toHaveBeenCalledTimes(1));
    expect(flow.render().some(node => node.props.role === 'status' && words(node).includes('Already recorded; no new dose was added.'))).toBe(true);
  });
  it('keeps entered data and never retries when saving is unconfirmed', async () => {
    const flow = setup(); flow.client.logDose.mockRejectedValue(new Error('private error contents'));
    flow.prepare(); flow.click('Confirm dose record');
    await vi.waitFor(() => expect(flow.render().some(node => node.props.role === 'alert')).toBe(true));
    expect(flow.client.logDose).toHaveBeenCalledTimes(1);
    expect(flow.onSaved).not.toHaveBeenCalled();
    expect(flow.render().find(node => node.props.id === 'dose-time')?.props.value).toBe('2026-10-17T12:00');
    expect(flow.render().some(node => words(node).includes('private error contents'))).toBe(false);
  });
  it('blocks repeated confirmation while the first request is pending', async () => {
    const flow = setup(); let finish!: (value: typeof doseResult) => void;
    flow.client.logDose.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    flow.prepare();
    const confirm = flow.render().find(node => words(node.props.children) === 'Confirm dose record')!.props.onClick as () => void;
    confirm(); confirm();
    expect(flow.client.logDose).toHaveBeenCalledTimes(1);
    finish(doseResult);
    await vi.waitFor(() => expect(flow.onSaved).toHaveBeenCalledTimes(1));
  });
  it('leaves wellbeing unknown and does not send a message after a record check', async () => {
    const flow = setup(); flow.click('Check for recorded concerns');
    await vi.waitFor(() => expect(flow.render().some(node => words(node.props.children) === 'No concerns returned by this check')).toBe(true));
    expect(flow.client.checkIn).toHaveBeenCalledExactlyOnceWith(person.id);
    expect(flow.client.logDose).not.toHaveBeenCalled();
    expect(flow.render().some(node => words(node).includes("These records cannot confirm anyone's wellbeing. No family message has been sent."))).toBe(true);
  });
});

describe('explicit caregiver time', () => {
  it('converts device-local input to the same instant with an explicit UTC offset', () => {
    expect(doseTimeToISO('2026-10-17T12:30')).toBe(new Date(2026, 9, 17, 12, 30).toISOString());
  });
  it.each(['', '2026-02-30T12:00', '2026-13-01T12:00', '2026-10-17T24:00', '2026-10-17', '2026-10-17T12:30Z'])(
    'rejects missing, normalized or ambiguous-format input: %s', value => expect(() => doseTimeToISO(value)).toThrow(),
  );
});
