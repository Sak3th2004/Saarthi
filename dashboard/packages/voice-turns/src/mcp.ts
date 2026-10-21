/** Structural subset of the official MCP client. Authentication and connection
 * lifecycle belong to the host application, never to microphone capture. */
export interface McpReader {
  callTool(request: { name: string; arguments: Record<string, unknown> },
    options: { signal: AbortSignal; timeout: number }): Promise<{ isError?: boolean; structuredContent?: Record<string, unknown> }>;
}

/** Bind ONE developer-approved read/draft tool. Tool annotations alone are not
 * an authorization boundary. Do not bind sending, booking or purchasing tools.
 * parse must verify business schema, actor/person scope and evidence ownership. */
export function createMcpAsk<T>(client: McpReader, options: {
  tool: string;
  argumentsFor: (question: string) => Record<string, unknown>;
  parse: (value: unknown, question: string) => T;
}) {
  if (!options.tool.trim()) throw new Error('Choose one approved read/draft tool.');
  return async (question: string, signal: AbortSignal): Promise<T> => {
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    const result = await client.callTool({ name: options.tool, arguments: options.argumentsFor(question) }, { signal, timeout: 45000 });
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (result.isError || !result.structuredContent) throw new Error('The read tool did not return a structured answer.');
    return options.parse(result.structuredContent, question);
  };
}
