import { Fragment, createElement, type ReactNode } from "react";

const INLINE_MARKDOWN =
  /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\([^)]+\))/g;

function safeLinkTarget(target: string): string | null {
  const trimmed = target.trim();
  if (/^(https?:\/\/|mailto:|\/(?!\/)|\.{1,2}\/|#)/i.test(trimmed)) {
    return trimmed;
  }
  return null;
}

function renderInline(source: string, keyPrefix: string): ReactNode[] {
  return source.split(INLINE_MARKDOWN).map((token, index) => {
    const key = `${keyPrefix}-${index}`;
    if (token.startsWith("**") || token.startsWith("__")) {
      return <strong key={key}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith("`")) {
      return <code key={key}>{token.slice(1, -1)}</code>;
    }
    if (
      (token.startsWith("*") && token.endsWith("*")) ||
      (token.startsWith("_") && token.endsWith("_"))
    ) {
      return <em key={key}>{token.slice(1, -1)}</em>;
    }

    const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const href = safeLinkTarget(link[2]);
      return href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{link[1]}</a>
        : <Fragment key={key}>{link[1]}</Fragment>;
    }
    return <Fragment key={key}>{token}</Fragment>;
  });
}

function isListItem(line: string): boolean {
  return /^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)/.test(line);
}

function isBlockStart(line: string): boolean {
  return /^\s{0,3}(?:#{1,6}\s|```|>|[-*+]\s+|\d+[.)]\s+)/.test(line)
    || /^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line);
}

export function AssistantMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.trimStart().startsWith("```")) {
      index += 1;
      const codeLines: string[] = [];
      while (index < lines.length && !lines[index].trimStart().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre key={`block-${blocks.length}`}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      blocks.push(
        createElement(
          `h${heading[1].length}`,
          { key: `block-${blocks.length}` },
          ...renderInline(heading[2], `heading-${blocks.length}`),
        ),
      );
      index += 1;
      continue;
    }

    if (/^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      blocks.push(<hr key={`block-${blocks.length}`} />);
      index += 1;
      continue;
    }

    if (/^\s{0,3}>/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^\s{0,3}>/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ""));
        index += 1;
      }
      blocks.push(
        <blockquote key={`block-${blocks.length}`}>
          {renderInline(quoteLines.join(" "), `quote-${blocks.length}`)}
        </blockquote>,
      );
      continue;
    }

    if (isListItem(line)) {
      const ordered = /^\s{0,3}\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (index < lines.length && isListItem(lines[index])) {
        items.push(lines[index].replace(/^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)/, ""));
        index += 1;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        createElement(
          List,
          { key: `block-${blocks.length}` },
          ...items.map((item, itemIndex) => (
            <li key={`item-${itemIndex}`}>
              {renderInline(item, `list-${blocks.length}-${itemIndex}`)}
            </li>
          )),
        ),
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={`block-${blocks.length}`}>
        {renderInline(paragraphLines.join(" "), `paragraph-${blocks.length}`)}
      </p>,
    );
  }

  return <div className="corral-workspace-markdown">{blocks}</div>;
}
