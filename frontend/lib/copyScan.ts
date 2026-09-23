/**
 * READ THE SITE'S COPY, NOT ITS SOURCE — the one scanner behind every copy guard.
 *
 * Two guards hold claims in copy to a single setting: forecastClaim.test.mts (how far ahead
 * the forecast runs) and updateCadence.test.mts (how often the data is updated). Both need
 * the same thing — every piece of reader-facing text on the site, and nothing else — and a
 * second copy of this scanner would be the exact drift those guards exist to prevent. So it
 * lives here, and each guard brings only its own rules.
 *
 * What counts as copy: in .ts/.tsx/.mts/.cts files, string literals, template text and JSX
 * text, parsed with the TypeScript compiler. Comments never become nodes, so a comment may say
 * anything. Templates and JSX are REASSEMBLED, so a split literal cannot slip through: a
 * literal expression is inlined (`next {7} days` reads as "next 7 days"), anything else
 * stands as "…" (which is what lets `Free ${LABEL} surf forecast` pass). Markdown under
 * content/ is prose throughout and is read line by line.
 *
 * Test files are never copy. They must contain the phrases they forbid, and each guard shows
 * that this exclusion — not a file extension — is what keeps them out.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import ts from 'typescript';

/** The directories of the frontend that hold copy. */
export const SCANNED_DIRS = ['app', 'components', 'lib', 'content'];

export type CopyText = { line: number; text: string };

const ELIDED = '…';   // what a non-literal expression contributes

function literalText(e: ts.Expression): string {
  if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e) || ts.isNumericLiteral(e)) {
    return e.text;
  }
  if (ts.isParenthesizedExpression(e)) return literalText(e.expression);
  return ELIDED;
}

/** Reader-facing strings in one TS/TSX source, with the line each starts on. */
export function copyStrings(fileName: string, source: string): CopyText[] {
  const kind = fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, kind);
  const out: CopyText[] = [];
  const at = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;

  const visit = (n: ts.Node): void => {
    if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) return;   // module specifiers
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      out.push({ line: at(n), text: n.text });
    } else if (ts.isTemplateExpression(n)) {
      const parts = [n.head.text];
      for (const span of n.templateSpans) parts.push(literalText(span.expression), span.literal.text);
      out.push({ line: at(n), text: parts.join('') });
    } else if (ts.isJsxElement(n) || ts.isJsxFragment(n)) {
      const parts = n.children.map((c) => {
        if (ts.isJsxText(c)) return c.text;
        if (ts.isJsxExpression(c)) return c.expression ? literalText(c.expression) : '';
        return ELIDED;   // a nested element is checked on its own when the walk reaches it
      });
      out.push({ line: at(n), text: parts.join('').replace(/\s+/g, ' ') });
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return out;
}

/** The copy in one file: markdown line by line, TS/TSX through copyStrings. */
export function copyOf(rel: string, source: string): CopyText[] {
  if (rel.endsWith('.md')) return source.split('\n').map((text, i) => ({ line: i + 1, text }));
  return copyStrings(rel, source);
}

export function isTestFile(rel: string): boolean {
  return /\.test\.[cm]?[jt]sx?$/.test(rel);
}

// .mts and .cts ARE walked, and that is what makes the test-file exclusion load-bearing. A
// first draft walked only .ts/.tsx, which skipped every *.test.mts before isTestFile ever
// saw it — so the self-reference guard was a file-extension accident, and a copy-bearing
// .mts module would have been skipped too. Mutation testing found it by deleting the
// exclusion and watching the suite stay green.
export function walk(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== 'node_modules' && name !== '.next') walk(p, acc);
    } else if (/\.(?:[cm]?ts|tsx|md)$/.test(name) && !/\.d\.[cm]?ts$/.test(name)) {
      acc.push(p);
    }
  }
  return acc;
}

/** Every file the walk reaches, test files included — relative to `frontend`. */
export function walkedFiles(frontend: string): string[] {
  return SCANNED_DIRS.flatMap((d) => walk(join(frontend, d))).map((abs) => relative(frontend, abs));
}

/** The files whose copy is checked — relative to `frontend`, test files excluded. */
export function scannedFiles(frontend: string): string[] {
  return walkedFiles(frontend).filter((rel) => !isTestFile(rel));
}

/** How many times `name` is used as an identifier in a file, imports aside. */
export function references(frontend: string, rel: string, name: string): number {
  const sf = ts.createSourceFile(rel, readFileSync(join(frontend, rel), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let n = 0;
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node)) return;
    if (ts.isIdentifier(node) && node.text === name) n += 1;
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return n;
}

/** The TS/TSX files, among those scanned, that declare a variable named `name`. */
export function definitionsOf(frontend: string, name: string): string[] {
  const out: string[] = [];
  for (const rel of scannedFiles(frontend).filter((f) => /\.tsx?$/.test(f))) {
    const sf = ts.createSourceFile(rel, readFileSync(join(frontend, rel), 'utf8'),
      ts.ScriptTarget.Latest, true);
    const visit = (n: ts.Node): void => {
      if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.name.text === name) {
        out.push(rel);
      }
      ts.forEachChild(n, visit);
    };
    visit(sf);
  }
  return out;
}
