import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { bookingStatusLabel } from './householdCalendarClient';
import { createHouseholdManagementClient, parseHouseholdChange, parseHouseholdChangeReview, parseHouseholdChangeSaved, type HouseholdChange, type HouseholdDirectory } from './householdManagementClient';
import {
  ADD_MEDICATION_MANAGE_AVAILABLE,
  describeAddMedication,
  medicationEmptyCopy,
  parseScheduleSlots,
} from './medicationEntry';

const directory = (): HouseholdDirectory => ({
  revision: 'a'.repeat(64), persistence: 'persistent', primary_person_id: 'one',
  people: [
    { id: 'one', name: 'Entered older adult', role: 'elder', email: null },
    { id: 'two', name: 'Entered relative', role: 'family', email: null },
  ],
  relationships: [{ from_person: 'two', to_person: 'one', relation: 'son' }],
});

describe('medication entry contract', () => {
  it('marks household manage medication saves as available', () => {
    expect(ADD_MEDICATION_MANAGE_AVAILABLE).toBe(true);
  });

  it('explains empty schedule and points to Update family details entry', () => {
    const copy = medicationEmptyCopy({ canManageHousehold: true });
    expect(copy.title).toBe('No medication schedule saved.');
    expect(copy.body).toMatch(/saved schedule fact/i);
    expect(copy.body).toMatch(/not medical advice/i);
    expect(copy.nextStep).toMatch(/Update family details/i);
    expect(copy.nextStep).toMatch(/Add a medication schedule fact/i);
    expect(copy.nextStep).not.toMatch(/not enabled/i);
    expect(`${copy.title} ${copy.body} ${copy.nextStep}`).not.toMatch(/Metformin|Amlodipine|Atorvastatin/i);
  });

  it('parses only HH:MM caregiver-typed schedule slots', () => {
    expect(parseScheduleSlots('08:00, 20:00')).toEqual(['08:00', '20:00']);
    expect(parseScheduleSlots(' morning ; evening ')).toEqual([]);
    expect(parseScheduleSlots('8:00')).toEqual([]);
    expect(parseScheduleSlots('')).toEqual([]);
  });

  it('describes a pending add as a typed fact, not advice', () => {
    const line = describeAddMedication(
      { kind: 'add_medication', person_id: 'elder-1', name: 'Entered name', dose: 'as written', schedule: ['08:00'], supply_count: 8 },
      'Entered older adult',
    );
    expect(line).toContain('Entered older adult');
    expect(line).toContain('Entered name');
    expect(line).toContain('8 remaining');
    expect(line).toMatch(/not a prescription/i);
  });
});

describe('add_medication manage transport', () => {
  it('reviews and confirms with medication echo without inventing names', async () => {
    const med: HouseholdChange = {
      kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00', '20:00'],
    };
    const notice = 'This is a caregiver-saved medication schedule fact for household recall. It is not medical advice.';
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({
        review_token: 'med-review', change: med, expires_in_seconds: 600, persistence: 'persistent', notice,
      }))
      .mockResolvedValueOnce(Response.json({
        status: 'saved', directory: directory(),
        medication: { person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00', '20:00'] },
        notice,
      }));
    const client = createHouseholdManagementClient(() => 'token', fetcher);
    const pending = await client.review(directory(), med);
    expect(pending.change).toEqual(med);
    expect(pending.notice).toMatch(/not medical advice/i);
    expect(await client.confirm(pending)).toEqual(directory());
    expect(fetcher).toHaveBeenNthCalledWith(1, '/household/manage/review', expect.objectContaining({
      body: JSON.stringify({ expected_revision: directory().revision, change: med }),
    }));
    expect(JSON.stringify(med)).not.toMatch(/Metformin|Ravi/);
  });

  it('rejects confirm without a matching medication echo', () => {
    const med: HouseholdChange = {
      kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: [],
    };
    const pending = parseHouseholdChangeReview({
      review_token: 'opaque', change: med, expires_in_seconds: 600, persistence: 'persistent',
      notice: 'Caregiver-saved fact. It is not medical advice.',
    }, med, 'persistent');
    expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: directory() }, pending)).toThrow();
    expect(parseHouseholdChange(med)).toMatchObject({ kind: 'add_medication', schedule: [] });
  });
});

describe('caregiver-visible appointment labels', () => {
  it('never shows list_appointments in booking status UI', () => {
    expect(bookingStatusLabel('calendar_saved')).toBe('Saved on calendar');
    expect(bookingStatusLabel('not_requested')).toBe('Not requested on a calendar');
    const markup = renderToStaticMarkup(createElement('span', { className: 'tag' },
      bookingStatusLabel('calendar_saved'), ' · ', bookingStatusLabel('not_requested')));
    expect(markup).not.toContain('list_appointments');
    expect(markup).toContain('Saved on calendar');
  });
});

describe('medication empty-state markup', () => {
  it('renders caregiver-safe empty copy without tool jargon', () => {
    const copy = medicationEmptyCopy({ canManageHousehold: true });
    const markup = renderToStaticMarkup(createElement('div', null,
      createElement('p', { className: 'empty' }, copy.title),
      createElement('p', { className: 'muted' }, copy.body),
      createElement('p', { className: 'form-hint' }, copy.nextStep),
    ));
    expect(markup).toContain('No medication schedule saved.');
    expect(markup).toContain('Update family details');
    expect(markup).not.toContain('list_appointments');
    expect(markup).not.toContain('add_medication');
    expect(markup).not.toMatch(/Metformin|get_medication_schedule|not enabled/i);
  });
});
