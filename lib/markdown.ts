/**
 * A small Markdown reader for model written briefs.
 *
 * It returns tokens rather than HTML for one reason: a brief cites its sources
 * as [3], and those markers have to become controls the reader can press to
 * jump to article 3. That is impossible once the text is a string of HTML, and
 * it is the whole point of the brief, since the citations are how a reader
 * checks a model.
 *
 * Only the constructs a brief actually uses are supported. Anything else is
 * left as the literal text it was written as, which is the safe failure.
 */

export type InlineToken =
  | { kind: "text"; value: string }
  | { kind: "strong"; value: string }
  | { kind: "emphasis"; value: string }
  | { kind: "code"; value: string }
  | { kind: "link"; value: string; href: string }
  /** One bracket, which may hold several numbers, as in [5, 6, 8]. */
  | { kind: "citation"; numbers: number[] };

export type Block =
  | { kind: "heading"; level: 1 | 2 | 3; content: InlineToken[] }
  | { kind: "paragraph"; content: InlineToken[] }
  | { kind: "list"; ordered: boolean; items: InlineToken[][] }
  | { kind: "quote"; content: InlineToken[] }
  | { kind: "rule" };

/**
 * Alternatives are ordered so the longest wins: a link is matched before a
 * citation, because both open with a bracket.
 */
const INLINE =
  /(\*\*|__)([\s\S]+?)\1|`([^`\n]+)`|\[([^\]\n]+)\]\((\S+?)\)|\[(\d+(?:\s*,\s*\d+)*)\]|(\*|_)(?!\s)([^*_\n]+?)\7/g;

function pushText(tokens: InlineToken[], value: string): void {
  if (!value) return;
  const last = tokens[tokens.length - 1];
  if (last && last.kind === "text") last.value += value;
  else tokens.push({ kind: "text", value });
}

export function parseInline(source: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let index = 0;

  INLINE.lastIndex = 0;
  for (let match = INLINE.exec(source); match !== null; match = INLINE.exec(source)) {
    pushText(tokens, source.slice(index, match.index));
    index = match.index + match[0].length;

    if (match[2] !== undefined) {
      tokens.push({ kind: "strong", value: match[2] });
    } else if (match[3] !== undefined) {
      tokens.push({ kind: "code", value: match[3] });
    } else if (match[4] !== undefined && match[5] !== undefined) {
      tokens.push({ kind: "link", value: match[4], href: match[5] });
    } else if (match[6] !== undefined) {
      tokens.push({
        kind: "citation",
        numbers: match[6].split(",").map((part) => Number.parseInt(part.trim(), 10)),
      });
    } else if (match[8] !== undefined) {
      tokens.push({ kind: "emphasis", value: match[8] });
    }
  }

  pushText(tokens, source.slice(index));
  return tokens;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}\d+[.)]\s+(.*)$/;
const RULE = /^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let quote: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ kind: "paragraph", content: parseInline(paragraph.join(" ").trim()) });
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    blocks.push({
      kind: "list",
      ordered: list.ordered,
      items: list.items.map((item) => parseInline(item)),
    });
    list = null;
  };
  const flushQuote = () => {
    if (quote.length === 0) return;
    blocks.push({ kind: "quote", content: parseInline(quote.join(" ").trim()) });
    quote = [];
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  for (const line of lines) {
    if (!line.trim()) {
      flushAll();
      continue;
    }

    if (RULE.test(line)) {
      flushAll();
      blocks.push({ kind: "rule" });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushAll();
      const level = Math.min(3, heading[1].length) as 1 | 2 | 3;
      blocks.push({ kind: "heading", level, content: parseInline(heading[2].trim()) });
      continue;
    }

    const quoted = QUOTE.exec(line);
    if (quoted) {
      flushParagraph();
      flushList();
      quote.push(quoted[1]);
      continue;
    }

    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flushParagraph();
      flushQuote();
      const ordered = Boolean(numbered);
      const text = item[1].trim();
      if (list && list.ordered === ordered) list.items.push(text);
      else {
        flushList();
        list = { ordered, items: [text] };
      }
      continue;
    }

    // A line under a list item continues it rather than starting a paragraph.
    if (list) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    if (quote.length > 0) {
      quote[quote.length - 1] += ` ${line.trim()}`;
      continue;
    }
    paragraph.push(line.trim());
  }

  flushAll();
  return blocks;
}
