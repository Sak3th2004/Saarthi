/** Resolves only when playback ends; abort always releases the object URL/audio. */
export function playVoiceBlob(blob: Blob, signal: AbortSignal, started: () => void = () => {}): Promise<void> {
  if (signal.aborted) return Promise.reject(new DOMException('Cancelled', 'AbortError'));
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob), audio = new Audio(url);
    let settled = false;
    function finish(error?: Error) {
      if (settled) return;
      settled = true; signal.removeEventListener('abort', abort);
      audio.onended = null; audio.onerror = null; audio.pause(); audio.removeAttribute('src');
      URL.revokeObjectURL(url);
      if (error) reject(error); else resolve();
    }
    const abort = () => finish(new DOMException('Cancelled', 'AbortError'));
    signal.addEventListener('abort', abort, { once: true });
    audio.onended = () => finish();
    audio.onerror = () => finish(new Error('Audio playback failed.'));
    audio.play().then(() => { if (!settled) started(); }).catch(() => finish(new Error('Playback blocked. Use the manual play button.')));
  });
}
