import { describe, expect, it } from 'vitest';
import { formatEvidenceLine, humanizeAgentText, isInternalEvent, operationLabel, timelineEventBody } from './timelineDisplay';

describe('timelineDisplay', () => {
  it('treats agent_action and household_setup as internal', () => {
    expect(isInternalEvent({ type: 'agent_action', detail: '{}', at: '2026-10-10T00:00:00Z' })).toBe(true);
    expect(isInternalEvent({ type: 'household_setup', detail: 'setup', at: '2026-10-10T00:00:00Z' })).toBe(true);
    expect(isInternalEvent({ type: 'note', detail: 'hello', at: '2026-10-10T00:00:00Z' })).toBe(false);
  });

  it('prefers feed_summary and compact evidence over raw JSON and speech', () => {
    const detail = JSON.stringify({
      operation: 'check_in',
      specialist: 'Watch',
      status: 'completed',
      feed_summary: 'Watch check-in · 1 recorded concern(s) · escalate via notify_family if needed',
      evidence: { operation: 'check_in', concern_count: 1, missed_doses_today: 1, ok: false },
      speech: 'secret model narration that must not appear',
    });
    const body = timelineEventBody({ type: 'agent_action', detail, at: '2026-10-10T12:00:00Z' });
    expect(body.summary).toBe('Watch check-in · 1 recorded concern(s) · escalate via Family notification if needed');
    expect(body.summary).not.toContain('secret');
    expect(body.summary).not.toContain('notify_family');
    expect(body.evidence).toContain('concern count: 1');
    expect(body.evidence).toContain('missed doses today: 1');
    expect(body.evidence).toContain('ok: false');
    expect(body.evidence).not.toContain('operation');
  });

  it('never surfaces speech or list_appointments when feed_summary is missing', () => {
    const detail = JSON.stringify({
      specialist: 'Appointments',
      operation: 'list_appointments',
      status: 'completed',
      evidence: { appointment_count: 2 },
      speech: 'Do not leak this speech to the timeline',
    });
    const body = timelineEventBody({ type: 'agent_action', detail, at: '2026-10-10T12:00:00Z' });
    expect(body.summary).toBe('Appointments · Listed upcoming appointments · completed');
    expect(body.summary).not.toContain('list_appointments');
    expect(body.summary).not.toContain('leak');
    expect(body.evidence).toBe('appointment count: 2');
  });

  it('humanizes notify_family in fallback summaries', () => {
    const detail = JSON.stringify({
      specialist: 'FamilyLoop',
      operation: 'notify_family',
      status: 'completed',
      evidence: { urgency: 'alert', delivery_status: 'recorded_only' },
      speech: 'Do not leak this speech to the timeline',
    });
    const body = timelineEventBody({ type: 'agent_action', detail, at: '2026-10-10T12:00:00Z' });
    expect(body.summary).toBe('FamilyLoop · Family notification · completed');
    expect(body.summary).not.toContain('notify_family');
    expect(body.evidence).toBe('urgency: alert · delivery status: recorded_only');
  });

  it('scrubs list_appointments from agent feed_summary text', () => {
    const detail = JSON.stringify({
      operation: 'list_appointments',
      specialist: 'Appointments',
      status: 'completed',
      feed_summary: 'Appointments · list_appointments · completed',
      evidence: { operation: 'list_appointments', appointment_count: 0 },
    });
    const body = timelineEventBody({ type: 'agent_action', detail, at: '2026-10-10T12:00:00Z' });
    expect(body.summary).toBe('Appointments · Listed upcoming appointments · completed');
    expect(body.summary).not.toMatch(/list_appointments/);
    expect(JSON.stringify(body)).not.toMatch(/list_appointments/);
  });

  it('maps operation ids to human labels', () => {
    expect(operationLabel('list_appointments')).toBe('Listed upcoming appointments');
    expect(operationLabel('book_appointment')).toBe('Saved appointment record');
    expect(humanizeAgentText('via notify_family please')).toBe('via Family notification please');
  });

  it('falls back safely for invalid agent_action JSON', () => {
    const body = timelineEventBody({
      type: 'agent_action',
      detail: '{not-json and also "speech":"hidden"}',
      at: '2026-10-10T12:00:00Z',
    });
    expect(body.summary).toBe('System activity recorded');
    expect(body.evidence).toBeNull();
  });

  it('leaves ordinary notes unchanged', () => {
    expect(timelineEventBody({ type: 'note', detail: 'Physio called back', at: '2026-10-10T12:00:00Z' }))
      .toEqual({ summary: 'Physio called back', evidence: null });
  });

  it.each(['accepted', 'uncertain', 'failed', 'recorded_only'])('shows message and actual %s status without internal audit fields', status => {
    const result = timelineEventBody({ type: 'notify', at: '2026-10-12T09:00:00Z', detail: JSON.stringify({
      message: 'Please bring the blue folder', status, operation: 'internal-review-key', provider_message_id: 'provider-id',
    }) });
    expect(result.summary).toBe('Please bring the blue folder');
    expect(result.evidence).toBeTruthy();
    expect(JSON.stringify(result)).not.toContain('provider-id');
    expect(JSON.stringify(result)).not.toContain('internal-review-key');
    if (status === 'accepted') expect(result.evidence).toContain('inbox delivery is not confirmed');
    if (status === 'uncertain') expect(result.evidence).toContain('may have been sent');
  });

  it('does not infer delivery from unknown or malformed notification outcomes', () => {
    for (const detail of ['{"message":"Call tomorrow","status":"sent"}', '{broken']) {
      expect(timelineEventBody({ type: 'notify', detail, at: '2026-10-12T09:00:00Z' }).evidence)
        .toBe('Email delivery status is unavailable');
    }
    expect(timelineEventBody({ type: 'notify', detail: '[info] Legacy message', at: '2026-10-12T09:00:00Z' }))
      .toEqual({ summary: '[info] Legacy message', evidence: null });
  });

  it('skips empty evidence objects', () => {
    expect(formatEvidenceLine({})).toBeNull();
    expect(formatEvidenceLine({ operation: 'log_dose' })).toBeNull();
    expect(formatEvidenceLine(null)).toBeNull();
  });
});
