import type { VoiceCallbacks, VoiceCapture } from './awsVoiceClient';

export type ConversationPhase = 'opening' | 'listening' | 'finishing' | 'thinking' | 'preparing' | 'speaking' | 'ended';
export interface ConversationAnswer { speech: string; action?: unknown }
export interface ConversationSessionOptions<T extends ConversationAnswer> {
  capture(callbacks: VoiceCallbacks): VoiceCapture;
  ask(text: string, signal: AbortSignal): Promise<T>;
  play(text: string, signal: AbortSignal, started: () => void): Promise<void>;
  phase(value: ConversationPhase): void;
  transcript(text: string, partial: boolean): void;
  level(value: number): void;
  countdown(seconds: number|null): void;
  reply(answer: T): void;
  ended(message: string): void;
  now?: () => number;
}

/** Energy assists end-of-turn detection; recognized words are required for a request.
 * This is an endpointing heuristic, not a speech/person classifier. */
export class TurnBoundary {
  private floor = .012;
  private lastSound = 0;
  private changed = 0;
  private text = '';
  constructor(private started: number) { this.lastSound = started; this.changed = started; }
  level(value: number, now: number) {
    if (!Number.isFinite(value)) return;
    const threshold = Math.max(.035, this.floor * 2.5);
    if (value > threshold) this.lastSound = now;
    else this.floor = Math.max(.004, Math.min(.04, this.floor * .96 + Math.max(0, value) * .04));
  }
  transcript(text: string, now: number) {
    if (text !== this.text) { this.text = text; this.changed = now; }
  }
  remaining(now: number): number|null {
    if (!this.text.trim()) return null;
    return Math.max(0, Math.max(this.lastSound + 3200, this.changed + 1800, this.started + 1800) - now);
  }
}

/** One bounded session, sequential capture -> read/draft -> playback.
 * No microphone is open during playback. stop() invalidates every late callback. */
export function startConversationSession<T extends ConversationAnswer>(options: ConversationSessionOptions<T>) {
  const now = options.now ?? Date.now;
  const controller = new AbortController();
  let capture: VoiceCapture|undefined, interval: ReturnType<typeof setInterval>|undefined;
  let requestTimer: ReturnType<typeof setTimeout>|undefined, turns = 0, generation = 0, done = false;
  const sessionTimer = setTimeout(() => stop('Conversation ended after three minutes. You can start again.'), 180000);
  function stop(message = 'Conversation ended. Microphone is off.') {
    if (done) return;
    done = true; generation++; controller.abort(); clearTimeout(sessionTimer); clearTimeout(requestTimer);
    clearInterval(interval); capture?.abort(); capture = undefined;
    options.level(0); options.countdown(null); options.phase('ended'); options.ended(message);
  }
  function listen() {
    if (done) return;
    const current = ++generation;
    let opened = now(), boundary = new TurnBoundary(opened);
    let text = '', partial = true, finishing = false, ended = false, failure = '';
    const valid = () => !done && generation === current;
    options.phase('opening'); options.transcript('', false); options.countdown(null);
    const callbacks: VoiceCallbacks = {
      started() {
        if (!valid()) return;
        opened = now(); boundary = new TurnBoundary(opened);
        options.phase('listening');
        interval = setInterval(() => {
          if (!valid() || finishing) return;
          if (!text.trim() && now() - opened >= 25000) { stop('No words heard. Microphone is off; start again whenever you are ready.'); return; }
          const remaining = boundary.remaining(now());
          options.countdown(remaining !== null && remaining <= 1800 ? Math.max(1, Math.ceil(remaining / 1000)) : null);
          if (remaining === 0) {
            finishing = true; clearInterval(interval); options.countdown(null);
            options.phase('finishing'); capture?.stop();
          }
        }, 100);
      },
      transcript(value, isPartial) {
        if (!valid()) return;
        text = value.trim(); partial = isPartial;
        boundary.transcript(text, now()); options.transcript(text, partial);
      },
      level(value) { if (valid()) { boundary.level(value, now()); options.level(value); } },
      failed(message) { if (valid()) { failure = message; stop(message); } },
      ended() {
        if (!valid() || ended) return;
        ended = true; clearInterval(interval); capture = undefined; options.level(0);
        if (failure || !text || partial) {
          stop(failure || 'The final words could not be confirmed. Review the transcript and use Ask Saarthi.');
          return;
        }
        void answer(text, current);
      },
    };
    try {
      const handle = options.capture(callbacks);
      if (!valid() || ended) handle.abort(); else capture = handle;
    } catch { stop('Microphone could not start. You can still use manual mode.'); }
  }
  async function answer(text: string, current: number) {
    const valid = () => !done && generation === current;
    options.phase('thinking'); options.countdown(null);
    requestTimer = setTimeout(() => stop('The answer is taking too long. Conversation paused; try again manually.'), 45000);
    try {
      const reply = await options.ask(text, controller.signal);
      if (!valid()) return;
      clearTimeout(requestTimer); options.reply(reply); options.phase('preparing');
      requestTimer = setTimeout(() => stop('Audio playback timed out. You can read the answer on screen.'), 60000);
      await options.play(reply.speech, controller.signal, () => { if (valid()) options.phase('speaking'); });
      if (!valid()) return;
      clearTimeout(requestTimer); turns++;
      if (reply.action) { stop('A draft is ready. Review it on screen before saving or sending.'); return; }
      if (turns >= 6) { stop('Six turns complete. Start another conversation whenever you are ready.'); return; }
      listen();
    } catch {
      if (valid()) stop('Conversation paused because the answer or audio could not complete. Your transcript and saved records are available in manual mode.');
    }
  }
  listen();
  return { stop };
}
