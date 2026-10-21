export interface VoiceCapture { stop(): void; abort(): void }
export interface VoiceCallbacks {
  started(): void;
  transcript(text: string, interim: boolean): void;
  ended(): void;
  failed(message: string): void;
  level(value: number): void;
}
