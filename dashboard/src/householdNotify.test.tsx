import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { FamilyNotify } from './FamilyNotify';
import {
  createHouseholdNotifyClient,
  deliveryStatusLabel,
  householdNotifyErrorMessage,
  parseDigestReview,
  parseNotifyConfirm,
  parseNotifyReview,
  type NotifyReview,
} from './householdNotifyClient';

const review = (): NotifyReview => ({
  review_token: 'opaque-review',
  person: { id: 'elder-1', name: 'Ravi', role: 'elder' },
  message: 'Dinner went well',
  urgency: 'info',
  recipient_emails: ['caregiver@example.test'],
  delivery_status: 'pending',
  notice: 'Email sends only after explicit confirmation.',
});

const reviewPayload = () => ({
  review_token: 'opaque-review',
  person: { id: 'elder-1', name: 'Ravi', role: 'elder' },
  message: 'Dinner went well',
  urgency: 'info',
  recipient_emails: ['caregiver@example.test'],
  delivery_status: 'pending',
  notice: 'Email sends only after explicit confirmation.',
  expires_in_seconds: 600,
});

describe('household notify response verification', () => {
  it('accepts pending reviews with saved recipient emails only', () => {
    expect(parseNotifyReview(reviewPayload(), 'elder-1', 'Dinner went well').recipient_emails).toEqual(['caregiver@example.test']);
    expect(() => parseNotifyReview({ ...reviewPayload(), recipient_emails: [] }, 'elder-1', 'Dinner went well')).toThrow();
    expect(() => parseNotifyReview({ ...reviewPayload(), message: 'Different' }, 'elder-1', 'Dinner went well')).toThrow();
    expect(() => parseNotifyReview({ ...reviewPayload(), urgency: 'urgent' }, 'elder-1', 'Dinner went well', 'info')).toThrow();
    expect(() => parseNotifyReview({ ...reviewPayload(), delivery_status: 'sent' }, 'elder-1', 'Dinner went well')).toThrow();
  });
  it('accepts live review person shape with id+name and no role', () => {
    // mcp-server family_notifications.review returns person: {id, name} only
    const live = {
      review_token: 'opaque-review',
      person: { id: 'elder-1', name: 'Ravi' },
      message: 'Dinner went well',
      urgency: 'info',
      recipient_emails: ['caregiver@example.test'],
      delivery_status: 'pending',
      notice: 'Email sends only after explicit confirmation. Recipients are saved household family emails only — not sample contacts or global env overrides.',
      expires_in_seconds: 600,
    };
    const parsed = parseNotifyReview(live, 'elder-1', 'Dinner went well');
    expect(parsed.person).toEqual({ id: 'elder-1', name: 'Ravi' });
    expect(parsed.person).not.toHaveProperty('role');
    expect(parsed.delivery_status).toBe('pending');
    expect(() => parseNotifyReview({ ...live, person: { id: 'elder-1', name: 'Ravi', role: 'doctor' } }, 'elder-1', 'Dinner went well')).toThrow();
  });
  it('requires honest confirm delivery_status and never invents sent destinations', () => {
    const pending = review();
    expect(parseNotifyConfirm({
      person: pending.person, delivered_to: [], submitted_to: ['email:caregiver@example.test'], candidate_channels: ['email:caregiver@example.test'],
      delivery_status: 'accepted', urgency: 'info', message: pending.message, speech: 'Email accepted for sending.',
    }, pending).delivery_status).toBe('accepted');
    expect(parseNotifyConfirm({
      person: pending.person, delivered_to: [], submitted_to: [], candidate_channels: [],
      delivery_status: 'recorded_only', urgency: 'info', message: pending.message, speech: 'Recorded only.',
    }, pending).delivery_status).toBe('recorded_only');
    expect(parseNotifyConfirm({
      person: pending.person, delivered_to: [], submitted_to: [], candidate_channels: [],
      delivery_status: 'failed', urgency: 'info', message: pending.message, speech: 'Failed.',
    }, pending).delivery_status).toBe('failed');
    expect(parseNotifyConfirm({
      person: pending.person, delivered_to: [], submitted_to: [], candidate_channels: [],
      delivery_status: 'uncertain', urgency: 'info', message: pending.message, speech: 'Email result is unknown.',
    }, pending).delivery_status).toBe('uncertain');
    expect(() => parseNotifyConfirm({
      person: pending.person, delivered_to: [], candidate_channels: [],
      delivery_status: 'sent', urgency: 'info', message: pending.message, speech: 'Claimed.',
    }, pending)).toThrow();
    expect(() => parseNotifyConfirm({
      person: pending.person, delivered_to: ['email:x'], candidate_channels: [],
      delivery_status: 'recorded_only', urgency: 'info', message: pending.message, speech: 'Claimed.',
    }, pending)).toThrow();
  });
  it('labels delivery statuses without claiming SMS', () => {
    expect(deliveryStatusLabel('pending')).toMatch(/nothing sent/i);
    expect(deliveryStatusLabel('accepted')).toMatch(/inbox delivery is not confirmed/i);
    expect(deliveryStatusLabel('uncertain')).toMatch(/may have been sent/i);
    expect(deliveryStatusLabel('failed')).toMatch(/not accepted/i);
    expect(deliveryStatusLabel('recorded_only')).toMatch(/not sent/i);
    expect(deliveryStatusLabel('accepted')).not.toMatch(/SMS/i);
  });
  it('accepts server-assembled weekly digests without a caller-known message', () => {
    const digestBody = [
      'Saarthi weekly household digest for Ravi',
      'Window: last 7 days (saved records only).',
      '',
      'This digest is assembled from household notebook records.',
      'It is not medical advice and does not confirm how someone is doing.',
    ].join('\n');
    const parsed = parseDigestReview({
      review_token: 'opaque-digest',
      person: { id: 'elder-1', name: 'Ravi' },
      message: digestBody,
      urgency: 'info',
      recipient_emails: ['caregiver@example.test'],
      delivery_status: 'pending',
      notice: 'Confirm to email this weekly digest.',
      kind: 'digest',
    }, 'elder-1');
    expect(parsed.kind).toBe('digest');
    expect(parsed.message).toContain('weekly household digest');
    expect(() => parseDigestReview({
      review_token: 'opaque-digest',
      person: { id: 'elder-1', name: 'Ravi' },
      message: 'Not a digest',
      urgency: 'info',
      recipient_emails: ['caregiver@example.test'],
      delivery_status: 'pending',
      notice: 'Confirm',
      kind: 'digest',
    }, 'elder-1')).toThrow();
  });
});

describe('household notify transport', () => {
  it('rejects a changed urgency instead of approving a different request', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...reviewPayload(), urgency: 'urgent' }));
    await expect(createHouseholdNotifyClient(() => 'token', fetcher).review('elder-1', 'Dinner went well', 'info'))
      .rejects.toThrow('could not be verified');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([
    { delivery_status: 'sent', delivered_to: ['email:caregiver@example.test'] },
    { delivery_status: 'accepted', submitted_to: ['email:stranger@example.test'] },
    { delivery_status: 'accepted', submitted_to: [] },
    { delivery_status: 'failed', submitted_to: ['email:caregiver@example.test'] },
    { delivery_status: 'accepted', message: 'A different message' },
  ])('treats malformed confirmation as an uncertain write, without automatic retries: %j', async replacement => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      person: review().person, delivered_to: [], submitted_to: ['email:caregiver@example.test'],
      candidate_channels: ['email:caregiver@example.test'],
      urgency: 'info', message: review().message, speech: 'Accepted.', ...replacement,
    }));
    await expect(createHouseholdNotifyClient(() => 'token', fetcher).confirm(review()))
      .rejects.toThrow('may already have been sent');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('preserves uncertainty for a lost confirmation response', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('network down'));
    await expect(createHouseholdNotifyClient(() => 'token', fetcher).confirm(review()))
      .rejects.toThrow('Retry the same review');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('uses Bearer /household/notify/* without cookies, redirects or invented emails', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(reviewPayload()))
      .mockResolvedValueOnce(Response.json({
        person: { id: 'elder-1', name: 'Ravi', role: 'elder' },
        delivered_to: [],
        submitted_to: ['email:caregiver@example.test'],
        candidate_channels: ['email:caregiver@example.test'],
        delivery_status: 'accepted',
        urgency: 'info',
        message: 'Dinner went well',
        speech: 'Email accepted for sending to 1 household family address(es). SMS is not enabled yet.',
      }));
    const client = createHouseholdNotifyClient(() => 'session-token', fetcher);
    const pending = await client.review('elder-1', 'Dinner went well', 'info');
    const confirmed = await client.confirm(pending);
    expect(fetcher).toHaveBeenNthCalledWith(1, '/household/notify/review', expect.objectContaining({
      method: 'POST', credentials: 'omit', redirect: 'error',
      headers: { Authorization: 'Bearer session-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ person: 'elder-1', message: 'Dinner went well', urgency: 'info' }),
    }));
    expect(fetcher).toHaveBeenNthCalledWith(2, '/household/notify/confirm', expect.objectContaining({
      body: JSON.stringify({ review_token: 'opaque-review', confirmed: true }),
    }));
    expect(confirmed.delivery_status).toBe('accepted');
    expect(confirmed.delivered_to).toEqual([]);
    expect(confirmed.submitted_to).toEqual(['email:caregiver@example.test']);
  });
  it('reviews weekly digests through /household/notify/digest/review', async () => {
    const digestBody = [
      'Saarthi weekly household digest for Ravi',
      'It is not medical advice and does not confirm how someone is doing.',
    ].join('\n');
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      review_token: 'opaque-digest',
      person: { id: 'elder-1', name: 'Ravi' },
      message: digestBody,
      urgency: 'info',
      recipient_emails: ['caregiver@example.test'],
      delivery_status: 'pending',
      notice: 'Confirm to email this weekly digest.',
      kind: 'digest',
    }));
    const pending = await createHouseholdNotifyClient(() => 'session-token', fetcher).reviewDigest('elder-1', 7);
    expect(fetcher).toHaveBeenCalledWith('/household/notify/digest/review', expect.objectContaining({
      body: JSON.stringify({ person: 'elder-1', days: 7 }),
    }));
    expect(pending.kind).toBe('digest');
    expect(pending.message).toContain('weekly household digest');
  });
  it.each([[401, 'Sign out'], [403, 'Sign out'], [400, 'Check the message'], [503, 'family emails']])(
    'maps HTTP %i without exposing provider text',
    async (status, expected) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: 'SECRET SES TEXT' }, { status: status as number }));
      await expect(createHouseholdNotifyClient(() => 'token', fetcher).review('elder-1', 'Hello')).rejects.toThrow(expected as string);
      expect(householdNotifyErrorMessage(new Error('secret'))).not.toContain('secret');
    },
  );
});

describe('family notify presentation', () => {
  it('shows review-first copy and never claims SMS delivery', () => {
    const client = { review: vi.fn(), reviewDigest: vi.fn(), confirm: vi.fn() };
    const html = renderToStaticMarkup(createElement(FamilyNotify, {
      person: { id: 'elder-1', name: 'Ravi', role: 'elder' },
      client,
    }));
    expect(html).toContain('Review family message');
    expect(html).toContain('Review weekly digest');
    expect(html).toContain('SMS is not available');
    expect(html).not.toContain('Confirm and send email');
    expect(html).not.toContain('SMS sent');
    expect(html).not.toContain('family@example.com');
  });
});
