/** Browser speech is optional. No microphone starts without an explicit user action. */
export type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void)|null;
  onerror: ((event: { error: string }) => void)|null;
  onend: (() => void)|null;
  start(): void; stop(): void; abort(): void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition;
};
export function recognitionFactory(scope: Window): (() => Recognition)|null {
  const Constructor = (scope as SpeechWindow).SpeechRecognition ?? (scope as SpeechWindow).webkitSpeechRecognition;
  return Constructor ? () => new Constructor() : null;
}
export function microphoneError(code: string) {
  if (code === 'not-allowed' || code === 'service-not-allowed') return 'Microphone access was not allowed. You can type your question below.';
  if (code === 'no-speech') return 'No speech was heard. Try again or type your question.';
  return 'Voice input is unavailable right now. You can still type your question.';
}
