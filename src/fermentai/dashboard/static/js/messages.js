// Helpers for agent messages of not-yet-frozen shape (pipeline dicts, Finding records, DB rows).

const TEXT_KEYS = ["content", "text", "message", "summary", "statement", "critique", "hypothesis_statement", "body"];

/** Best human-readable text of a message (string, pipeline dict, or DB row with .content). */
export function messageText(m, depth = 0) {
  if (m === null || m === undefined) return "";
  if (typeof m === "string") return m;
  if (typeof m !== "object" || depth > 3) return String(m);
  for (const k of TEXT_KEYS) {
    const v = m[k];
    if (typeof v === "string" && v.trim()) return v;
    if (v && typeof v === "object") {
      const t = messageText(v, depth + 1);
      if (t) return t;
    }
  }
  return "";
}

/** Sender id of a message in any of the known shapes. */
export function messageSender(m) {
  if (!m || typeof m !== "object") return null;
  return m.sender ?? m.from ?? m.agent_id ?? m.agent ?? m.author ?? (m.content && typeof m.content === "object" ? messageSender(m.content) : null);
}
export function messageRecipient(m) {
  if (!m || typeof m !== "object") return null;
  return m.recipient ?? m.to ?? (m.content && typeof m.content === "object" ? messageRecipient(m.content) : null);
}
export function messageKind(m) {
  if (!m || typeof m !== "object") return null;
  return m.kind ?? m.type ?? m.message_type ?? (m.content && typeof m.content === "object" ? messageKind(m.content) : null);
}
/** The innermost dict that carries structured fields (prediction, uncertainty, ...). */
export function messageBody(m) {
  if (!m || typeof m !== "object") return {};
  if (m.content && typeof m.content === "object") return messageBody(m.content);
  return m;
}
