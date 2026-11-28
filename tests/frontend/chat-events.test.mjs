import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../../apps/desktop/web/views/chat-views.js", import.meta.url), "utf8");
const declaration = source.match(/function normalizeChatEvent\(event\)\{[\s\S]*?\n\}/);
assert.ok(declaration, "chat event normalizer is present");
const normalize = runInNewContext(`${declaration[0]}\nnormalizeChatEvent`);
const expectNormalized = (input, expected) => {
  assert.equal(JSON.stringify(normalize(input)), JSON.stringify(expected));
};

expectNormalized({ kind: "tool" }, { kind: "tool", type: "tool_call" });
expectNormalized({ kind: "tool_output" }, { kind: "tool_output", type: "tool_result" });
expectNormalized({ kind: "reasoning_chunk" }, { kind: "reasoning_chunk", type: "reasoning_delta" });
expectNormalized({ content: "legacy" }, { content: "legacy", type: "unknown" });
expectNormalized(null, { type: "unknown" });

console.log("chat-events.test: legacy and malformed events normalize safely");
