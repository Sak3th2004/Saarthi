import { describe, expect, it, vi } from 'vitest';
import { createMcpAsk } from '../src/mcp';

describe('MCP read adapter', () => {
  const controller = () => new AbortController();
  it('calls only the fixed tool and validates structured data before returning it', async () => {
    const client = { callTool: vi.fn().mockResolvedValue({ structuredContent: { speech: 'Saved detail' } }) };
    const parse = vi.fn(value => value), request = controller();
    const ask = createMcpAsk(client, { tool: 'approved_read', argumentsFor: question => ({ question, person: 'authorized-person' }), parse });
    expect(await ask('New words', request.signal)).toEqual({ speech: 'Saved detail' });
    expect(client.callTool).toHaveBeenCalledExactlyOnceWith({ name: 'approved_read', arguments: { question: 'New words', person: 'authorized-person' } }, { signal: request.signal, timeout: 45000 });
    expect(parse).toHaveBeenCalledExactlyOnceWith({ speech: 'Saved detail' }, 'New words');
  });
  it('rejects a late response after cancellation even if transport ignores abort', async () => {
    const request = controller(), parse = vi.fn();
    const client = { callTool: vi.fn(async () => { request.abort(); return { structuredContent: { speech: 'Late' } }; }) };
    await expect(createMcpAsk(client, { tool: 'approved_read', argumentsFor: () => ({}), parse })('Question', request.signal)).rejects.toThrow('Cancelled');
    expect(parse).not.toHaveBeenCalled();
  });
  it.each([{ isError: true, structuredContent: {} }, { content: [{ type: 'text', text: 'unvalidated prose' }] }])('rejects unstructured or failed tools', async result => {
    const parse = vi.fn();
    await expect(createMcpAsk({ callTool: vi.fn().mockResolvedValue(result) }, { tool: 'approved_read', argumentsFor: () => ({}), parse })('Question', controller().signal)).rejects.toThrow('structured answer');
    expect(parse).not.toHaveBeenCalled();
  });
});
