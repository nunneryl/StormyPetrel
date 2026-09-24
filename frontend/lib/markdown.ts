/**
 * Markdown → HTML for the blog, in one place.
 *
 * TABLES. remark on its own is CommonMark, and CommonMark has no tables: a GitHub-flavoured
 * table came out as one <p> of pipes and dashes, and the browser ran its rows together into
 * a single line (every table in the methodology post, on the live site). remark-gfm adds the
 * table syntax. It is the renderer's job, so every post gets it, not post by post.
 *
 * READABLE TABLES. Every table is wrapped in a box that scrolls sideways on its own, so a wide
 * table never squashes its columns or widens the page on a phone. The box can take keyboard
 * focus and is labelled with the table's headers, so it can be scrolled without a mouse. A
 * column whose every body cell is a number (units allowed: "8s", "0.85", "16s+", "2.5 m/s")
 * is right-aligned, and the site's tabular figures then line the digits up. It uses GFM's own
 * column alignment, so an alignment the author wrote (`:---` / `---:`) always wins.
 *
 * SANITIZING stays on (remark-html's default, GitHub's schema). The schema below is that one
 * plus exactly the scroll box's attributes, nothing else.
 */
import { remark } from 'remark';
import remarkGfm from 'remark-gfm';
import remarkHtml from 'remark-html';
import { defaultSchema, type Schema } from 'hast-util-sanitize';
import type { Nodes, Parent, PhrasingContent, Root, Table, TableCell } from 'mdast';

export const TABLE_SCROLL_CLASS = 'table-scroll';

/** A number, optionally with a comparison sign in front and a short unit or "+" after. */
const NUMERIC_CELL =
  /^[<>≤≥≈~±+\-−]?\s*\d+(?:[.,]\d+)?\s*(?:%|°|s|ms|min|h|hr|hrs|ft|m|km|kt|kts|mph|m\/s)?\+?$/;

function cellText(node: TableCell | PhrasingContent): string {
  if ('value' in node && typeof node.value === 'string') return node.value;
  if ('children' in node) return (node.children as PhrasingContent[]).map(cellText).join('');
  return '';
}

export function isNumericCell(text: string): boolean {
  return NUMERIC_CELL.test(text.trim());
}

/** Right-align each column whose body cells are all numbers, unless the author aligned it. */
export function alignNumericColumns(table: Table): void {
  const [head, ...body] = table.children;
  if (!head || body.length === 0) return;
  const align = table.align ?? [];
  for (let i = 0; i < head.children.length; i++) {
    if (align[i]) continue;
    if (body.every((row) => row.children[i] && isNumericCell(cellText(row.children[i])))) {
      align[i] = 'right';
    }
  }
  table.align = align;
}

function scrollBox(table: Table): Nodes {
  const headers = table.children[0]?.children.map(cellText).filter(Boolean) ?? [];
  // An unknown node with children becomes the element named by data.hName (mdast-util-to-hast).
  return {
    type: 'tableScroll',
    data: {
      hName: 'div',
      hProperties: {
        className: [TABLE_SCROLL_CLASS],
        role: 'region',
        tabIndex: 0,
        ariaLabel: headers.length ? `Table: ${headers.join(', ')}` : 'Table',
      },
    },
    children: [table],
  } as unknown as Nodes;
}

/** remark plugin: align numeric columns and put every table in its scroll box. */
export function remarkReadableTables() {
  return (tree: Root) => {
    const walk = (node: Parent) => {
      node.children = node.children.map((child) => {
        if (child.type === 'table') {
          alignNumericColumns(child);
          return scrollBox(child);
        }
        if ('children' in child) walk(child as Parent);
        return child;
      }) as Parent['children'];
    };
    walk(tree);
  };
}

/** GitHub's schema, plus the scroll box's own attributes and nothing more. */
export const SANITIZE_SCHEMA: Schema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    div: [
      ...(defaultSchema.attributes?.div ?? []),
      ['className', TABLE_SCROLL_CLASS],
      ['role', 'region'],
      ['tabIndex', 0],
      'ariaLabel',
    ],
  },
};

export async function renderMarkdown(markdown: string): Promise<string> {
  const file = await remark()
    // singleTilde off: "~6 km … ~10 h" in prose must never become strikethrough.
    .use(remarkGfm, { singleTilde: false })
    .use(remarkReadableTables)
    .use(remarkHtml, { sanitize: SANITIZE_SCHEMA })
    .process(markdown);
  return String(file);
}
