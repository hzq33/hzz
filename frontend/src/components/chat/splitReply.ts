export function splitReplySegments(content: string): string[] {
  if (!content.trim()) return [];
  const parts: string[] = [];
  let buf = '';
  let inFence = false;
  for (const line of content.split('\n')) {
    if (line.trim().startsWith('```')) inFence = !inFence;
    if (!inFence && buf && line.trim() === '') {
      parts.push(buf);
      buf = '';
    } else {
      buf = buf ? `${buf}\n${line}` : line;
    }
  }
  if (buf.trim()) parts.push(buf);
  return parts.length ? parts : [content];
}
