import { afterEach, describe, expect, it, vi } from 'vitest';
import { audioFrame, crc32, decodeFrame } from './awsEventStream';
import { createAwsVoiceClient, parseVoiceSession } from './awsVoiceClient';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('AWS audio transport', () => {
  it('matches the standard CRC32 vector and protects binary frames against corruption', () => {
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
    const pcm = new Uint8Array([0, 128, 255, 127, 1, 0]);
    const frame = audioFrame(pcm);
    const decoded = decodeFrame(frame.buffer as ArrayBuffer);
    expect(decoded.headers[':event-type']).toBe('AudioEvent');
    expect(decoded.payload).toEqual(pcm);
    frame[frame.length - 5] ^= 1;
    expect(() => decodeFrame(frame.buffer as ArrayBuffer)).toThrow();
    expect(decodeFrame(audioFrame(new Uint8Array()).buffer as ArrayBuffer).payload.length).toBe(0);
  });
  it('rejects untrusted stream destinations and excessive recording duration', () => {
    const data = { url: 'wss://transcribestreaming.us-east-1.amazonaws.com:8443/stream-transcription-websocket?media-encoding=pcm&sample-rate=16000&X-Amz-Expires=30&X-Amz-Signature=test', expires_in: 30, max_seconds: 60, sample_rate: 16000 };
    expect(parseVoiceSession(data).maxSeconds).toBe(60);
    expect(() => parseVoiceSession({ ...data, url: data.url.replace('amazonaws.com', 'attacker.example') })).toThrow();
    expect(() => parseVoiceSession({ ...data, max_seconds: 600 })).toThrow();
  });
  it('sends playback only to the authenticated backend and rejects non-audio responses', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('audio', { headers: { 'content-type': 'audio/mpeg' } }));
    vi.stubGlobal('fetch', fetch);
    const client = createAwsVoiceClient(() => 'private-token');
    await client.speak('A saved detail.', new AbortController().signal);
    expect(fetch).toHaveBeenCalledWith('/household/voice/speak', expect.objectContaining({
      headers: { Authorization: 'Bearer private-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'A saved detail.' }), redirect: 'error',
    }));
    fetch.mockResolvedValue(new Response('<html>failure</html>'));
    await expect(client.speak('detail', new AbortController().signal)).rejects.toThrow('Invalid audio');
  });
  it('releases a microphone granted after cancellation without starting a cloud session', async () => {
    let resolve!: (value: unknown) => void;
    const stop = vi.fn(), fetch = vi.fn();
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => new Promise(done => { resolve = done; }) } });
    vi.stubGlobal('window', { AudioContext: class {} });
    vi.stubGlobal('fetch', fetch);
    const callbacks = { started: vi.fn(), transcript: vi.fn(), ended: vi.fn(), failed: vi.fn(), level: vi.fn() };
    const capture = createAwsVoiceClient(() => 'token').listen('en-IN', callbacks);
    capture.abort();
    resolve({ getTracks: () => [{ stop }] });
    await Promise.resolve(); await Promise.resolve();
    expect(stop).toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(callbacks.started).not.toHaveBeenCalled();
  });
  it('times out an unanswered permission prompt without a paid stream', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => new Promise(() => {}) } });
    vi.stubGlobal('window', { AudioContext: class {} });
    const callbacks = { started: vi.fn(), transcript: vi.fn(), ended: vi.fn(), failed: vi.fn(), level: vi.fn() };
    createAwsVoiceClient(() => 'token').listen('en-IN', callbacks);
    await vi.advanceTimersByTimeAsync(45000);
    expect(callbacks.failed).toHaveBeenCalledWith(expect.stringContaining('timed out'));
    expect(callbacks.ended).toHaveBeenCalledOnce();
  });
  it('streams captured PCM, replaces partial captions, and releases the microphone on stop', async () => {
    const trackStop = vi.fn(), close = vi.fn().mockResolvedValue(undefined);
    const connectNode = vi.fn(), disconnect = vi.fn();
    let socket: any, worklet: any;
    class Socket {
      static OPEN = 1; static CLOSING = 2;
      readyState = 0; bufferedAmount = 0;
      onopen?: Function; onmessage?: Function; onclose?: Function;
      send = vi.fn(); close = vi.fn(() => { this.readyState = 3; });
      constructor() { socket = this; }
    }
    class Context {
      sampleRate = 16000; state = 'running'; destination = {};
      audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
      createMediaStreamSource() { return { connect: connectNode }; }
      createGain() { return { gain: { value: 1 }, connect: connectNode }; }
      resume = vi.fn().mockResolvedValue(undefined); close = close;
    }
    class Worklet {
      port = { onmessage: undefined as Function|undefined }; connect = connectNode; disconnect = disconnect;
      constructor() { worklet = this; }
    }
    vi.stubGlobal('window', { AudioContext: Context });
    vi.stubGlobal('AudioContext', Context); vi.stubGlobal('AudioWorkletNode', Worklet); vi.stubGlobal('WebSocket', Socket);
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: trackStop }] }) } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      url: 'wss://transcribestreaming.us-east-1.amazonaws.com:8443/stream-transcription-websocket?media-encoding=pcm&sample-rate=16000&X-Amz-Expires=30&X-Amz-Signature=test',
      expires_in: 30, max_seconds: 60, sample_rate: 16000,
    }))));
    const callbacks = { started: vi.fn(), transcript: vi.fn(), ended: vi.fn(), failed: vi.fn(), level: vi.fn() };
    const capture = createAwsVoiceClient(() => 'token').listen('en-IN', callbacks);
    await vi.waitFor(() => expect(socket).toBeDefined());
    socket.readyState = Socket.OPEN; socket.onopen();
    await Promise.resolve();
    expect(callbacks.started).toHaveBeenCalledOnce();
    const pcm = new Uint8Array([0, 128, 255, 127]);
    worklet.port.onmessage({ data: { pcm: pcm.buffer, level: .1 } });
    expect(decodeFrame(socket.send.mock.calls[0][0]).payload).toEqual(pcm);
    function transcript(text: string, partial: boolean) {
      const enc = new TextEncoder();
      const headers: number[] = [];
      for (const [key, val] of Object.entries({ ':message-type': 'event', ':event-type': 'TranscriptEvent' })) {
        const k = enc.encode(key), v = enc.encode(val);
        headers.push(k.length, ...k, 7, 0, v.length, ...v);
      }
      const payload = enc.encode(JSON.stringify({ Transcript: { Results: [{ ResultId: 'one', IsPartial: partial, Alternatives: [{ Transcript: text }] }] } }));
      const bytes = new Uint8Array(16 + headers.length + payload.length), view = new DataView(bytes.buffer);
      view.setUint32(0, bytes.length); view.setUint32(4, headers.length); view.setUint32(8, crc32(bytes.subarray(0, 8)));
      bytes.set(headers, 12); bytes.set(payload, 12 + headers.length);
      view.setUint32(bytes.length - 4, crc32(bytes.subarray(0, -4)));
      socket.onmessage({ data: bytes.buffer });
    }
    transcript('Where is', true); transcript('Where is the notebook?', false);
    expect(callbacks.transcript).toHaveBeenLastCalledWith('Where is the notebook?', false);
    capture.stop();
    expect(trackStop).toHaveBeenCalled(); expect(close).toHaveBeenCalled();
    expect(decodeFrame(socket.send.mock.calls.at(-1)[0]).payload.length).toBe(0);
    socket.onclose();
    expect(callbacks.ended).toHaveBeenCalledOnce(); expect(callbacks.failed).not.toHaveBeenCalled();
  });
});
