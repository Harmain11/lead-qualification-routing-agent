const ALLOWED_TAGS = new Set([
  "a", "b", "strong", "i", "em", "u", "s", "p", "br", "div", "span", "ul", "ol", "li",
  "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "hr", "table", "thead", "tbody",
  "tr", "th", "td", "img", "small", "sub", "sup",
]);
const DROP_WITH_CONTENT = new Set(["script", "style", "iframe", "object", "embed", "template", "noscript", "form", "svg", "math"]);
const ALLOWED_ATTRS: Record<string, Set<string>> = {
  a: new Set(["href", "title"]),
  img: new Set(["src", "alt", "width", "height"]),
  td: new Set(["colspan", "rowspan"]),
  th: new Set(["colspan", "rowspan"]),
};
const SAFE_LINK = /^(?:https?:|mailto:|tel:|#)/i;
const SAFE_IMAGE = /^(?:https:|data:image\/(?:png|jpe?g|gif|webp);base64,)/i;

/**
 * Allow-list sanitiser for editor HTML (saved drafts, quote bodies). Keeps simple
 * formatting and safe links; drops scripts, event handlers, styles and unknown tags.
 */
export function sanitizeHtml(html: string): string {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const clean = (node: Node): Node | null => {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent ?? "");
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const element = node as Element;
    const tag = element.tagName.toLowerCase();
    if (DROP_WITH_CONTENT.has(tag)) return null;
    const children = [...element.childNodes].map(clean).filter((child): child is Node => child !== null);
    if (!ALLOWED_TAGS.has(tag)) {
      const fragment = document.createDocumentFragment();
      children.forEach((child) => fragment.appendChild(child));
      return fragment;
    }
    const out = document.createElement(tag);
    const allowed = ALLOWED_ATTRS[tag];
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (!allowed?.has(name)) continue;
      const value = attribute.value.trim();
      if (name === "href" && !SAFE_LINK.test(value)) continue;
      if (name === "src" && !SAFE_IMAGE.test(value)) continue;
      out.setAttribute(name, value);
    }
    if (tag === "a") {
      out.setAttribute("rel", "noopener noreferrer");
      out.setAttribute("target", "_blank");
    }
    children.forEach((child) => out.appendChild(child));
    return out;
  };
  const container = document.createElement("div");
  [...doc.body.childNodes].map(clean).forEach((child) => { if (child) container.appendChild(child); });
  return container.innerHTML;
}
