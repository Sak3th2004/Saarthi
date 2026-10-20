/** Browser speech is optional. No microphone starts without an explicit user action. */
export type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => void)|null;
  onstart?: (() => void)|null;
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
  if (code === 'not-allowed') return 'Microphone permission was denied. Allow microphone access in this site’s browser settings, then try again. You can also type your question.';
  if (code === 'service-not-allowed') return 'This browser has blocked its speech recognition service. Microphone permission alone may not enable it. Try Chrome or Edge, or type your question.';
  if (code === 'network') return 'Your browser could not reach its speech recognition service. Check the connection or try another browser. This is separate from the Saarthi server.';
  if (code === 'audio-capture') return 'No microphone could be opened. Check your selected input device and operating-system microphone permission.';
  if (code === 'language-not-supported') return 'This browser does not support the selected recognition language. Choose another language in Voice settings.';
  if (code === 'no-speech') return 'No speech was heard. Try again or type your question.';
  if (code === 'aborted') return 'Listening stopped. Your transcript is still editable.';
  return 'The browser could not start speech recognition. Check microphone permissions or type your question.';
}

export function preferredVoice(voices: SpeechSynthesisVoice[], language: string): SpeechSynthesisVoice | undefined {
  const sameLanguage = voices.filter(v => v.lang.replace('_', '-').toLowerCase() === language.toLowerCase());
  const english = voices.filter(v => v.lang.toLowerCase().startsWith('en'));
  const candidates = sameLanguage.length ? sameLanguage : english;
  // These names are hints only. The browser has no standard gender field; the
  // user can explicitly choose any voice their device provides.
  return candidates.find(v => /neerja|kajal|female|jenny|aria|samantha|natural/i.test(v.name))
    ?? candidates.find(v => v.default) ?? candidates[0];
}
