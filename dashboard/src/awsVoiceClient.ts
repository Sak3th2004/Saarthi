import { NotebookAccessError } from './notebookErrors';
import { audioFrame, decodeFrame } from './awsEventStream';
import captureUrl from './voice-capture.js?url&no-inline';

export interface VoiceCapture { stop(): void; abort(): void }
export interface VoiceCallbacks {
  started(): void; transcript(text: string, interim: boolean): void;
  ended(): void; failed(message: string): void; level(value: number): void;
}
export interface AwsVoiceClient {
  listen(language: string, callbacks: VoiceCallbacks): VoiceCapture;
  speak(text: string, signal: AbortSignal): Promise<Blob>;
}
export function parseVoiceSession(value: unknown) {
  const r = value as { url?: unknown; max_seconds?: unknown; sample_rate?: unknown; expires_in?: unknown };
  if (!r || typeof r.url !== 'string' || r.max_seconds !== 60 || r.sample_rate !== 16000 || r.expires_in !== 30) throw new Error('Invalid voice session.');
  const url = new URL(r.url);
  if (url.protocol !== 'wss:' || url.hostname !== 'transcribestreaming.us-east-1.amazonaws.com' ||
      url.port !== '8443' || url.pathname !== '/stream-transcription-websocket' || url.username || url.password || url.hash ||
      url.searchParams.get('media-encoding') !== 'pcm' || url.searchParams.get('sample-rate') !== '16000' ||
      url.searchParams.get('X-Amz-Expires') !== '30' || !url.searchParams.has('X-Amz-Signature')) throw new Error('Invalid voice destination.');
  return { url: url.href, maxSeconds: 60 };
}
export function createAwsVoiceClient(token: () => string): AwsVoiceClient {
  async function request(action: string, body: object, signal: AbortSignal) {
    const response = await fetch(`/household/voice/${action}`, {
      method: 'POST', headers: { Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body), cache: 'no-store', redirect: 'error', signal,
    });
    if ([401, 403].includes(response.status)) throw new NotebookAccessError();
    if (response.status === 429) throw new Error('Voice usage limit reached. You can still type a question.');
    if (!response.ok) throw new Error('AWS voice could not connect. Try again shortly.');
    return response;
  }
  return {
    async speak(text, signal) {
      const response = await request('speak', { text }, signal);
      if (!response.headers.get('content-type')?.startsWith('audio/mpeg')) throw new Error('Invalid audio response.');
      const blob = await response.blob();
      if (!blob.size || blob.size > 1024 * 1024) throw new Error('Invalid audio response.');
      return blob;
    },
    listen(language, callbacks) {
      let stream: MediaStream|undefined, context: AudioContext|undefined, node: AudioWorkletNode|undefined;
      let socket: WebSocket|undefined, stopTimer: ReturnType<typeof setTimeout>|undefined;
      let deadline: ReturnType<typeof setTimeout>|undefined, ended = false, stopping = false, samples = 0;
      const controller = new AbortController();
      const results = new Map<string, { text: string; partial: boolean }>();
      function releaseMicrophone() {
        node?.disconnect(); stream?.getTracks().forEach(track => track.stop());
        if (context && context.state !== 'closed') void context.close().catch(() => undefined);
        callbacks.level(0);
      }
      function finish(message?: string, silent = false) {
        if (ended) return;
        ended = true; controller.abort(); clearTimeout(deadline); clearTimeout(stopTimer); releaseMicrophone();
        if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
        if (!silent) { if (message) callbacks.failed(message); callbacks.ended(); }
      }
      function stop() {
        if (ended || stopping) return;
        stopping = true; clearTimeout(deadline); releaseMicrophone();
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(audioFrame(new Uint8Array()).buffer);
          stopTimer = setTimeout(() => finish(), 5000);
        } else finish();
      }
      deadline = setTimeout(() => finish('Microphone setup timed out. Check permission and try again.'), 45000);
      void (async () => {
        try {
          if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw new Error('This browser cannot record audio securely. Open the HTTPS site in a current browser.');
          stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
          if (ended) { releaseMicrophone(); return; }
          context = new AudioContext({ sampleRate: 16000 });
          if (context.sampleRate !== 16000 || !context.audioWorklet) throw new Error('This browser cannot provide the required audio format. Try Chrome or Edge.');
          await context.audioWorklet.addModule(captureUrl);
          if (ended) { releaseMicrophone(); return; }
          const response = await request('listen', { language }, controller.signal);
          const session = parseVoiceSession(await response.json());
          if (ended) return;
          socket = new WebSocket(session.url); socket.binaryType = 'arraybuffer';
          clearTimeout(deadline);
          deadline = setTimeout(() => finish('The voice connection timed out. Check your network and try again.'), 15000);
          socket.onopen = () => {
            if (ended) { socket?.close(); return; }
            clearTimeout(deadline);
            const source = context!.createMediaStreamSource(stream!);
            node = new AudioWorkletNode(context!, 'saarthi-capture');
            const muted = context!.createGain(); muted.gain.value = 0;
            source.connect(node); node.connect(muted); muted.connect(context!.destination);
            node.port.onmessage = event => {
              if (ended || stopping || socket?.readyState !== WebSocket.OPEN) return;
              const pcm = new Uint8Array(event.data.pcm);
              samples += pcm.byteLength / 2;
              if (samples > 16000 * session.maxSeconds) { stop(); return; }
              if (socket.bufferedAmount > 256000) { finish('The connection is too slow for live audio. Please try again.'); return; }
              socket.send(audioFrame(pcm).buffer);
              callbacks.level(Math.min(1, event.data.level * 4));
            };
            void context!.resume().then(() => { if (!ended && !stopping) callbacks.started(); }).catch(() => finish('Audio capture could not start.'));
            deadline = setTimeout(stop, session.maxSeconds * 1000);
          };
          socket.onmessage = event => {
            if (ended || !(event.data instanceof ArrayBuffer)) return;
            try {
              const frame = decodeFrame(event.data);
              if (frame.headers[':message-type'] !== 'event') throw new Error('AWS could not process the audio stream. Try again.');
              if (frame.headers[':event-type'] !== 'TranscriptEvent') return;
              const body = JSON.parse(new TextDecoder().decode(frame.payload));
              for (const result of body.Transcript?.Results ?? []) {
                if (typeof result.ResultId !== 'string' || !Array.isArray(result.Alternatives)) continue;
                const text = result.Alternatives[0]?.Transcript;
                if (typeof text === 'string') results.set(result.ResultId, { text, partial: result.IsPartial === true });
              }
              const values = [...results.values()];
              callbacks.transcript(values.map(r => r.text).join(' ').slice(0, 2000), values.some(r => r.partial));
            } catch { finish('The transcription response could not be read. Your existing words are still available.'); }
          };
          socket.onerror = () => finish('AWS live captions could not connect. Check whether your network blocks secure WebSockets on port 8443.');
          socket.onclose = () => finish(stopping ? undefined : 'The live caption connection ended. Review your words or start again.');
        } catch (error) {
          if (!ended) finish(error instanceof DOMException && error.name === 'NotAllowedError'
            ? 'Microphone permission was denied. Allow microphone access in your browser and operating-system settings.'
            : error instanceof DOMException && error.name === 'NotFoundError' ? 'No microphone was found. Connect an input device and try again.'
            : error instanceof Error ? error.message : 'Voice input could not start.');
        }
      })();
      return { stop, abort: () => finish(undefined, true) };
    },
  };
}
