import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startConversationSession, TurnBoundary } from './conversationSession';
import type { VoiceCallbacks } from './awsVoiceClient';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(10000); });
afterEach(() => vi.useRealTimers());
function harness(action?: object) {
  let callbacks!: VoiceCallbacks;
  const abort = vi.fn(), stop = vi.fn(() => callbacks.ended());
  const capture = vi.fn((value: VoiceCallbacks) => { callbacks = value; return { stop, abort }; });
  const ask = vi.fn().mockResolvedValue({ speech: 'A real saved detail.', action });
  const play = vi.fn().mockResolvedValue(undefined), phase = vi.fn(), reply = vi.fn(), ended = vi.fn();
  const session = startConversationSession({ capture, ask, play, phase, reply, ended, transcript: vi.fn(), countdown: vi.fn(), level: vi.fn() });
  return { session, get callbacks() { return callbacks; }, stop, abort, capture, ask, play, phase, reply, ended };
}
describe('bounded natural conversation', () => {
  it('waits through short pauses, sends once, then opens the next turn only after playback', async () => {
    const h = harness();
    let finishAudio!: () => void;
    h.play.mockImplementationOnce(() => new Promise<void>(resolve => { finishAudio = resolve; }));
    h.callbacks.started(); h.callbacks.level(.5); h.callbacks.transcript('Where is my folder?', false);
    await vi.advanceTimersByTimeAsync(2500);
    expect(h.ask).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(800);
    expect(h.ask).toHaveBeenCalledExactlyOnceWith('Where is my folder?', expect.any(AbortSignal));
    expect(h.stop).toHaveBeenCalledOnce(); expect(h.capture).toHaveBeenCalledOnce();
    finishAudio(); await Promise.resolve(); await Promise.resolve();
    expect(h.capture).toHaveBeenCalledTimes(2);
    h.session.stop();
  });
  it('waits for the final caption after closing the audio stream', async () => {
    const h = harness();
    h.stop.mockImplementation(() => {});
    h.callbacks.started(); h.callbacks.transcript('Where is my', true);
    await vi.advanceTimersByTimeAsync(3300);
    expect(h.stop).toHaveBeenCalledOnce(); expect(h.ask).not.toHaveBeenCalled();
    h.callbacks.transcript('Where is my folder?', false); h.callbacks.ended(); h.callbacks.ended();
    await Promise.resolve(); await Promise.resolve();
    expect(h.ask).toHaveBeenCalledOnce();
    h.session.stop();
  });
  it('never submits unconfirmed partial words or silent audio', async () => {
    const h = harness();
    h.callbacks.started(); h.callbacks.transcript('book an', true);
    await vi.advanceTimersByTimeAsync(3300);
    expect(h.ask).not.toHaveBeenCalled(); expect(h.ended).toHaveBeenCalledWith(expect.stringContaining('final words'));
    const silent = harness();
    silent.callbacks.started();
    await vi.advanceTimersByTimeAsync(25100);
    expect(silent.ask).not.toHaveBeenCalled(); expect(silent.abort).toHaveBeenCalledOnce();
  });
  it('pauses at a draft without executing any external action or restarting capture', async () => {
    const h = harness({ type: 'family_message', message: 'A draft only' });
    h.callbacks.started(); h.callbacks.transcript('Tell my family about that.', false);
    await vi.advanceTimersByTimeAsync(3300);
    expect(h.reply).toHaveBeenCalledOnce();
    expect(h.capture).toHaveBeenCalledOnce();
    expect(h.ended).toHaveBeenCalledWith(expect.stringContaining('Review it'));
  });
  it('ignores late replies and callbacks after ending', async () => {
    const h = harness();
    let resolve!: (value: object) => void;
    h.ask.mockImplementation(() => new Promise(done => { resolve = done; }));
    h.callbacks.started(); h.callbacks.transcript('A question', false);
    await vi.advanceTimersByTimeAsync(3300);
    const signal = h.ask.mock.calls[0][1];
    h.session.stop(); resolve({ speech: 'Late result' }); h.callbacks.ended();
    await Promise.resolve(); await Promise.resolve();
    expect(signal.aborted).toBe(true);
    expect(h.reply).not.toHaveBeenCalled(); expect(h.play).not.toHaveBeenCalled();
    expect(h.ended).toHaveBeenCalledOnce();
  });
  it('aborts playback when the session ends and never resumes the microphone', async () => {
    const h = harness();
    h.play.mockImplementation(() => new Promise(() => {}));
    h.callbacks.started(); h.callbacks.transcript('A question', false);
    await vi.advanceTimersByTimeAsync(3300);
    h.session.stop();
    expect(h.play.mock.calls[0][1].aborted).toBe(true);
    expect(h.capture).toHaveBeenCalledOnce();
  });
  it('ends on a capture failure without retrying paid connections', () => {
    const h = harness();
    h.callbacks.failed('Permission denied'); h.callbacks.ended();
    expect(h.ended).toHaveBeenCalledExactlyOnceWith('Permission denied');
    expect(h.capture).toHaveBeenCalledOnce();
  });
  it('bounds session time and turn count', async () => {
    const h = harness();
    for (let i = 0; i < 6; i++) {
      h.callbacks.started(); h.callbacks.transcript(`Question ${i}`, false);
      await vi.advanceTimersByTimeAsync(3300);
    }
    expect(h.ask).toHaveBeenCalledTimes(6); expect(h.capture).toHaveBeenCalledTimes(6);
    expect(h.ended).toHaveBeenCalledWith(expect.stringContaining('Six turns'));
    const idle = harness();
    await vi.advanceTimersByTimeAsync(180000);
    expect(idle.ended).toHaveBeenCalledWith(expect.stringContaining('three minutes'));
  });
  it('does not count time waiting for permission as listening silence', async () => {
    const h = harness();
    await vi.advanceTimersByTimeAsync(30000);
    h.callbacks.started();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.ended).not.toHaveBeenCalled();
    h.session.stop();
  });
});
describe('turn boundary', () => {
  it('extends a pause when more words or audible speech arrive', () => {
    const boundary = new TurnBoundary(0);
    boundary.transcript('Hello', 100); boundary.level(.3, 100);
    expect(boundary.remaining(3000)).toBeGreaterThan(0);
    boundary.level(.4, 3100);
    expect(boundary.remaining(4000)).toBeGreaterThan(2000);
    boundary.transcript('Hello Saarthi', 6000);
    expect(boundary.remaining(7000)).toBe(800);
    expect(boundary.remaining(8000)).toBe(0);
  });
});
