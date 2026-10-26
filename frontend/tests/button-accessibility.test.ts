import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const css = await readFile(new URL("../src/v3/polish.css", import.meta.url), "utf8");

function contrastRatio(hexForeground: string, hexBackground: string) {
  const luminance = (hex: string) => {
    const channels = hex.match(/[0-9a-f]{2}/gi)?.map((channel) => Number.parseInt(channel, 16) / 255) ?? [];
    assert.equal(channels.length, 3, `Expected a six-digit hex color: ${hex}`);
    const linear = channels.map((channel) =>
      channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    );
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };
  const foreground = luminance(hexForeground);
  const background = luminance(hexBackground);
  return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
}

test("filled primary actions keep white text at accessible contrast in every theme state", () => {
  const colors = {
    normal: css.match(/--primary-action-background:\s*(#[\da-f]{6})/i)?.[1],
    hover: css.match(/--primary-action-hover:\s*(#[\da-f]{6})/i)?.[1],
    pressed: css.match(/--primary-action-pressed:\s*(#[\da-f]{6})/i)?.[1],
  };
  assert.deepEqual(colors, {
    normal: "#1e6b52",
    hover: "#195c46",
    pressed: "#144b39",
  });

  for (const [state, background] of Object.entries(colors)) {
    assert.ok(background, `Missing primary-action color for ${state}`);
    assert.ok(
      contrastRatio("#ffffff", background) >= 4.5,
      `${state} primary action must meet 4.5:1 text contrast`,
    );
  }
});

test("primary actions include legacy Slack routing and preserve readable disabled/loading/focus states", () => {
  const actionGroup = /\.v-app\s+:is\([\s\S]*?\.primary-button,[\s\S]*?\.slack-routing-actions\s*>\s*button,[\s\S]*?\.v-btn\[data-kind="filled"\]/;
  assert.match(css, actionGroup);
  assert.match(css, /:hover:not\(:disabled\):not\(\[aria-disabled="true"\]\)/);
  assert.match(css, /:active:not\(:disabled\):not\(\[aria-disabled="true"\]\)/);
  assert.match(css, /:focus-visible\s*\{[^}]*outline:\s*3px solid var\(--focus\)/);
  assert.match(css, /:is\(:disabled,\s*\[aria-disabled="true"\],\s*\[aria-busy="true"\],\s*\[data-loading="true"\],\s*\.is-loading\)\s*\{[^}]*color:\s*var\(--primary-action-foreground\)\s*!important;[^}]*opacity:\s*1\s*!important;/);
});

test("Agent scope uses keyboard-operable toggle buttons instead of incomplete radio semantics", async () => {
  const source = await readFile(new URL("../src/components/CorralAgentDock.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/v3/polish.css", import.meta.url), "utf8");
  assert.match(source, /className="v-agent-scope"\s+role="group"\s+aria-label="Conversation scope"/);
  assert.equal((source.match(/aria-pressed=\{(?:Boolean\(targetLead\)\s*\|\|\s*showLeadPicker|!targetLead\s*&&\s*!showLeadPicker)\}/g) ?? []).length, 2);
  assert.doesNotMatch(source, /role="radiogroup"|role="radio"/);
  assert.match(styles, /\.v-app \.corral-agent-window \.v-agent-scope button\[aria-pressed="true"\]/);
});
