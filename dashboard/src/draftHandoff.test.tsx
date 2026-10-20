import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppointmentEditor } from './AppointmentEditor';
import { FamilyNotify } from './FamilyNotify';

const hooks = vi.hoisted(() => ({ values: [] as any[], index: 0 }));
vi.mock('react', async original => ({
  ...await original<typeof import('react')>(),
  useState(initial: unknown) {
    const i = hooks.index++;
    if (!(i in hooks.values)) hooks.values[i] = typeof initial === 'function' ? initial() : initial;
    return [hooks.values[i], (value: unknown) => { hooks.values[i] = value; }];
  },
  useRef(initial: unknown) {
    const i = hooks.index++;
    if (!(i in hooks.values)) hooks.values[i] = { current: initial };
    return hooks.values[i];
  },
  useEffect(effect: () => void, deps: unknown[]) {
    const i = hooks.index++;
    if (!(i in hooks.values) || deps.some((v, n) => v !== hooks.values[i][n])) {
      hooks.values[i] = deps; effect();
    }
  },
}));
type Props = { children?: ReactNode; [key: string]: unknown };
function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  return [node, ...elements(node.props.children)];
}
const person = { id: 'parent', name: 'Parent', role: 'elder' as const };
beforeEach(() => { hooks.values = []; hooks.index = 0; });
describe('editable conversation draft handoff', () => {
  it('prefills appointment fields without preparing or confirming an external write', () => {
    const calendar = { connect: vi.fn(), events: vi.fn(), reviewAppointment: vi.fn(), confirmAppointment: vi.fn() };
    const draft = { id: '1', title: 'Family walk', start_local: '2026-10-22T12:00', end_local: '2026-10-22T12:15', time_zone: 'Asia/Calcutta' };
    const render = () => { hooks.index = 0; return elements(AppointmentEditor({ person, calendar, draft, onSaved: vi.fn() })); };
    render();
    const tree = render();
    expect(tree.find(n => n.props.id === 'appointment-title')?.props.value).toBe(draft.title);
    expect(tree.find(n => n.props.id === 'appointment-start')?.props.value).toBe(draft.start_local);
    expect(calendar.reviewAppointment).not.toHaveBeenCalled();
    expect(calendar.confirmAppointment).not.toHaveBeenCalled();
  });
  it('does not let an old family review authorize a newly supplied draft', async () => {
    let resolve!: (value: any) => void;
    const client = { review: vi.fn(() => new Promise<any>(done => { resolve = done; })), reviewDigest: vi.fn(), confirm: vi.fn() };
    let draft = { id: '1', message: 'First draft' };
    const render = () => { hooks.index = 0; return elements(FamilyNotify({ person, client, draft })); };
    render();
    expect(client.review).not.toHaveBeenCalled();
    (render().find(n => n.type === 'form')!.props.onSubmit as Function)({ preventDefault() {} });
    draft = { id: '2', message: 'New draft' }; render(); render();
    resolve({ review_token: 'old-token', person, message: 'First draft', urgency: 'info', recipient_emails: ['someone@example.test'], delivery_status: 'pending', notice: 'Review' });
    await Promise.resolve(); await Promise.resolve();
    const tree = render();
    expect(tree.find(n => n.props.id === 'family-notify-message')?.props.value).toBe('New draft');
    expect(tree.some(n => n.props.children === 'Confirm and send email')).toBe(false);
    expect(client.confirm).not.toHaveBeenCalled();
  });
});
