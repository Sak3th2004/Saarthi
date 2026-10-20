/* AudioWorklet: 100ms mono PCM16 frames, transferred without recording to disk. */
class SaarthiCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Int16Array(1600);
    this.offset = 0;
    this.energy = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const raw of input) {
      const value = Math.max(-1, Math.min(1, raw));
      this.samples[this.offset++] = Math.round(value * (value < 0 ? 32768 : 32767));
      this.energy += value * value;
      if (this.offset === 1600) {
        const pcm = new ArrayBuffer(3200), view = new DataView(pcm);
        for (let i = 0; i < 1600; i++) view.setInt16(i * 2, this.samples[i], true);
        this.port.postMessage({ pcm, level: Math.sqrt(this.energy / 1600) }, [pcm]);
        this.offset = 0; this.energy = 0;
      }
    }
    return true;
  }
}
registerProcessor('saarthi-capture', SaarthiCapture);
