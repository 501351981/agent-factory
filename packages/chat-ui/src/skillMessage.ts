/**
 * Serialize selected skill chips into the outgoing user message.
 * Chip UI → text API: inject an explicit skill_load instruction so the model
 * must call the client tool (timeline will show skill_load).
 */
export function buildSkillPrefixedMessage(
  serialized: string,
  skillNames: string[],
): string {
  const names: string[] = []
  for (const raw of skillNames) {
    const name = String(raw || '').trim()
    if (name && !names.includes(name)) names.push(name)
  }
  if (!names.length) return serialized
  const loads = names.map((n) => `skill_load(name="${n}")`).join('，然后 ')
  const prefix = `请先调用 ${loads}，再严格按加载到的 skill 流程处理下面的用户请求。`
  return serialized ? `${prefix}\n\n${serialized}` : prefix
}
