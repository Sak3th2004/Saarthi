import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Calendar } from './Calendar';
import { AppointmentEditor } from './AppointmentEditor';

// Exercise the component's event handlers without adding a DOM implementation.
// State is retained between explicit renders; this does not verify browser rendering.
const hooks = vi.hoisted(() => ({ values: [] as unknown[], index: 0 }));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState(initial: unknown) {
    const index = hooks.index++;
    if (!(index in hooks.values)) hooks.values[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks.values[index], (value: unknown) => { hooks.values[index] = value; }];
  },
}));

type ElementProps = { children?: ReactNode; [key: string]: unknown };
function elements(node: ReactNode): ReactElement<ElementProps>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<ElementProps>(node)) return [];
  return [node, ...elements(node.props.children)];
}
const person = { id: 'person-entered-by-user', name: 'Entered parent', role: 'elder' as const };
function client() {
  return {
    connect: vi.fn().mockResolvedValue('http://127.0.0.1:8080/oauth/google/household-connect?h=review-token'),
    events: vi.fn().mockResolvedValue({ source: 'google_calendar', events: [], more_available: false }),
    reviewAppointment: vi.fn(), confirmAppointment: vi.fn(),
  };
}
beforeEach(() => { hooks.values = []; hooks.index = 0; });

describe('calendar connection and notebook refresh', () => {
  it('presents an explicit Continue to Google link after preparing the handoff', async () => {
    const calendar = client();
    const render = () => { hooks.index = 0; return elements(Calendar({ calendar })); };
    let tree = render();
    expect(tree.some(node => node.type === 'a')).toBe(false);
    const connect = tree.find(node => node.type === 'button' && node.props.children === 'Connect Google Calendar')!;
    (connect.props.onClick as () => void)();
    await vi.waitFor(() => expect(calendar.connect).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => {
      tree = render();
      const link = tree.find(node => node.type === 'a' && node.props.children === 'Continue to Google');
      expect(link?.props).toMatchObject({
        href: 'http://127.0.0.1:8080/oauth/google/household-connect?h=review-token',
        target: '_blank', rel: 'noopener noreferrer',
      });
    });
    expect(calendar.events).not.toHaveBeenCalled();
  });
  it.each(['saved', 'calendar_saved_memory_pending'] as const)(
    'refreshes the notebook only when household memory has been saved: %s', async status => {
      const calendar = client();
      const onAppointmentSaved = vi.fn();
      const tree = elements(Calendar({ person, calendar, onAppointmentSaved }));
      const editor = tree.find(node => node.type === AppointmentEditor)!;
      (editor.props.onSaved as (value: string) => void)(status);
      await vi.waitFor(() => expect(calendar.events).toHaveBeenCalledTimes(1));
      if (status === 'saved') expect(onAppointmentSaved).toHaveBeenCalledExactlyOnceWith(person.id);
      else expect(onAppointmentSaved).not.toHaveBeenCalled();
    },
  );
});
