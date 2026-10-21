import { afterEach, describe, expect, it, vi } from 'vitest';
import { playVoiceBlob } from '../src/playback';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('conversation playback cleanup', () => {
  function setup(blocked = false) {
    let audio: any;
    class FakeAudio {
      onended?: () => void; onerror?: () => void;
      pause = vi.fn(); removeAttribute = vi.fn();
      play = vi.fn(() => blocked ? Promise.reject(new Error('blocked')) : Promise.resolve());
      constructor() { audio = this; }
    }
    vi.stubGlobal('Audio', FakeAudio);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    return { get audio() { return audio; }, revoke };
  }
  it('reports actual playback start and resolves only at its end', async () => {
    const h = setup(), started = vi.fn(), done = vi.fn();
    const promise = playVoiceBlob(new Blob(['mp3']), new AbortController().signal, started).then(done);
    await Promise.resolve();
    expect(started).toHaveBeenCalledOnce(); expect(done).not.toHaveBeenCalled();
    h.audio.onended(); await promise;
    expect(h.revoke).toHaveBeenCalledWith('blob:test');
    expect(h.audio.pause).toHaveBeenCalled();
  });
  it('aborts playing audio and revokes its URL', async () => {
    const h = setup(), controller = new AbortController();
    const promise = playVoiceBlob(new Blob(['mp3']), controller.signal);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
    expect(h.audio.pause).toHaveBeenCalledOnce(); expect(h.revoke).toHaveBeenCalledOnce();
  });
  it('cleans up if the browser refuses automatic playback', async () => {
    const h = setup(true);
    await expect(playVoiceBlob(new Blob(['mp3']), new AbortController().signal)).rejects.toThrow('Playback blocked');
    expect(h.revoke).toHaveBeenCalledOnce();
  });
});
