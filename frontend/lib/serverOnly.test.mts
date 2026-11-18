/**
 * THE SUPABASE SERVICE KEY STAYS ON THE SERVER.
 *
 * lib/supabase.ts builds both clients with SUPABASE_SERVICE_KEY, which bypasses row-level
 * security. Next ships a module to the browser when a 'use client' file imports it, directly or
 * through other modules, and inlines an env var into browser code only when its name starts
 * NEXT_PUBLIC_. So this holds:
 *   1. No 'use client' file under app/, components/ or lib/ reaches lib/supabase.ts through its
 *      imports (static or dynamic, '@/', './' or '../'; `import type` ships nothing and is skipped).
 *      Also lib/coastline.ts, which holds every spot's coastline (0.9 MB).
 *   2. lib/supabase.ts reads the key as SUPABASE_SERVICE_KEY, and nothing anywhere names a
 *      NEXT_PUBLIC_ variable holding a service key or secret.
 *   3. The import walker finds a chain written the ways the site writes imports (checked on a
 *      made-up set of files), so a pass above means something.
 *
 * Checked 2026-11-17 by hand and in a production build (no key, client or table name in the
 * browser bundles); this keeps it so.
 *
 *     node --experimental-strip-types frontend/lib/serverOnly.test.mts
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const SERVER_ONLY = ['lib/supabase.ts', 'lib/coastline.ts'];

type Files = { read(rel: string): string | null };

const IMPORT = /(?:^|\n)\s*(import|export)\s+(type\s+)?[^;'"]*?from\s+['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)|(?:^|\n)\s*import\s+['"]([^'"]+)['"]/g;

/** Repo-relative module paths a file imports at run time (type-only imports skipped). */
function importsOf(rel: string, src: string, files: Files): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(IMPORT)) {
    if (m[2]) continue;                                   // import type / export type
    const spec = m[3] ?? m[4] ?? m[5];
    if (!spec) continue;
    let base: string;
    if (spec.startsWith('@/')) base = spec.slice(2);
    else if (spec.startsWith('.')) base = normalize(join(dirname(rel), spec));
    else continue;                                        // a package
    const hit = [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]
      .map((p) => p.replace(/\.(ts|tsx)\.(ts|tsx)$/, '.$1'))
      .find((p) => files.read(p) !== null);
    if (hit) out.push(hit);
  }
  return out;
}

/** The chain from *start* to any of *targets* through run-time imports, or null. */
function chain(start: string, targets: string[], files: Files): string[] | null {
  const seen = new Set<string>();
  const stack: string[][] = [[start]];
  while (stack.length) {
    const path = stack.pop()!;
    const at = path[path.length - 1];
    if (targets.includes(at)) return path;
    if (seen.has(at)) continue;
    seen.add(at);
    const src = files.read(at);
    if (src === null) continue;
    for (const d of importsOf(at, src, files)) stack.push([...path, d]);
  }
  return null;
}

const disk: Files = {
  read(rel) {
    const p = join(FRONTEND, rel);
    return existsSync(p) && statSync(p).isFile() ? readFileSync(p, 'utf8') : null;
  },
};
function walk(dir: string): string[] {
  return readdirSync(join(FRONTEND, dir)).flatMap((n) => {
    const rel = join(dir, n);
    return statSync(join(FRONTEND, rel)).isDirectory() ? walk(rel) : [rel];
  });
}

// 3 first: the walker on made-up files, the ways the site writes imports.
const fake = (m: Record<string, string>): Files => ({ read: (r) => m[r] ?? null });
const FIXTURE = fake({
  'components/A.tsx': "'use client';\nimport { x } from '@/lib/q';\n",
  'lib/q.ts': "import { supabase } from './supabase';\nexport const x = 1;\n",
  'lib/supabase.ts': 'export const supabase = 1;\n',
  'components/B.tsx': "'use client';\nimport type { T } from '@/lib/q';\n",
  'components/C.tsx': "'use client';\nconst m = await import('../lib/q');\n",
  'components/D.tsx': "'use client';\nimport { y } from '@/lib/other';\n",
  'lib/other.ts': 'export const y = 2;\n',
});
check('the walker follows @/ then ./ to lib/supabase.ts',
  JSON.stringify(chain('components/A.tsx', SERVER_ONLY, FIXTURE)) ===
    JSON.stringify(['components/A.tsx', 'lib/q.ts', 'lib/supabase.ts']));
check('an import type ships nothing', chain('components/B.tsx', SERVER_ONLY, FIXTURE) === null);
check("a dynamic import('../lib/q') is followed", chain('components/C.tsx', SERVER_ONLY, FIXTURE) !== null);
check('a module that never reaches it passes', chain('components/D.tsx', SERVER_ONLY, FIXTURE) === null);

// 1. Every 'use client' file.
const clients = ['app', 'components', 'lib'].flatMap(walk)
  .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.m?ts$/.test(p))
  .filter((p) => /^\s*['"]use client['"]/.test(disk.read(p) ?? ''));
check('there are client modules to check (more than 20)', clients.length > 20, String(clients.length));
for (const c of clients) {
  const hit = chain(c, SERVER_ONLY, disk);
  if (hit) check(`${c} does not reach a server-only module`, false, hit.join(' -> '));
}
check(`none of the ${clients.length} client modules reaches lib/supabase.ts or lib/coastline.ts`,
  clients.every((c) => chain(c, SERVER_ONLY, disk) === null));

// 2. The key's name.
const supa = disk.read('lib/supabase.ts') ?? '';
check('lib/supabase.ts reads the key as SUPABASE_SERVICE_KEY',
  supa.includes('process.env.SUPABASE_SERVICE_KEY') && !/NEXT_PUBLIC_[A-Z_]*(SERVICE|SECRET)/.test(supa));
const everywhere = ['app', 'components', 'lib'].flatMap(walk).filter((p) => /\.(ts|tsx|mts)$/.test(p))
  .filter((p) => p !== 'lib/serverOnly.test.mts');
const leaks = everywhere.filter((p) => /NEXT_PUBLIC_[A-Z_]*(SERVICE|SECRET|SUPABASE_KEY)/.test(disk.read(p) ?? ''));
check('no NEXT_PUBLIC_ variable names a service key or secret', leaks.length === 0, leaks.join(', '));
check('.env.local.example keeps the key unprefixed',
  /^SUPABASE_SERVICE_KEY=/m.test(disk.read('.env.local.example') ?? '') &&
  !/NEXT_PUBLIC_SUPABASE_SERVICE/.test(disk.read('.env.local.example') ?? ''));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nserverOnly: ALL PASS');
