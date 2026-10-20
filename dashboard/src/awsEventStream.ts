/** AWS EventStream framing for the documented Transcribe WebSocket protocol. */
const encoder = new TextEncoder(), decoder = new TextDecoder('utf-8', { fatal: true });
const table = Uint32Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
export function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (const b of bytes) c = table[(c ^ b) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
export function audioFrame(audio: Uint8Array): Uint8Array {
  const headers: number[] = [];
  for (const [name, value] of Object.entries({
    ':content-type': 'application/octet-stream', ':event-type': 'AudioEvent', ':message-type': 'event',
  })) {
    const n = encoder.encode(name), v = encoder.encode(value);
    headers.push(n.length, ...n, 7, v.length >> 8, v.length & 255, ...v);
  }
  const bytes = new Uint8Array(16 + headers.length + audio.length), view = new DataView(bytes.buffer);
  view.setUint32(0, bytes.length); view.setUint32(4, headers.length);
  view.setUint32(8, crc32(bytes.subarray(0, 8)));
  bytes.set(headers, 12); bytes.set(audio, 12 + headers.length);
  view.setUint32(bytes.length - 4, crc32(bytes.subarray(0, bytes.length - 4)));
  return bytes;
}
export function decodeFrame(buffer: ArrayBuffer): { headers: Record<string, string>; payload: Uint8Array } {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  if (bytes.length < 16 || bytes.length > 1024 * 1024 || view.getUint32(0) !== bytes.length ||
      view.getUint32(8) !== crc32(bytes.subarray(0, 8)) ||
      view.getUint32(bytes.length - 4) !== crc32(bytes.subarray(0, bytes.length - 4))) throw new Error('Invalid voice frame.');
  const end = 12 + view.getUint32(4), headers: Record<string, string> = {};
  if (end > bytes.length - 4) throw new Error('Invalid voice headers.');
  let offset = 12;
  while (offset < end) {
    const size = bytes[offset++];
    if (offset + size + 3 > end) throw new Error('Invalid voice header.');
    const name = decoder.decode(bytes.subarray(offset, offset + size)); offset += size;
    if (bytes[offset++] !== 7) throw new Error('Unsupported voice header.');
    const length = view.getUint16(offset); offset += 2;
    if (offset + length > end) throw new Error('Invalid voice header length.');
    headers[name] = decoder.decode(bytes.subarray(offset, offset + length)); offset += length;
  }
  return { headers, payload: bytes.slice(end, -4) };
}
