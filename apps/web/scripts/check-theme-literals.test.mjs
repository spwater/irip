/**
 * Dependency-free test suite for the theme-literal scanner.
 *
 * Run:  node apps/web/scripts/check-theme-literals.test.mjs
 *
 * Uses only Node.js built-ins (assert, fs/promises, os, path).
 */

import assert from 'node:assert/strict';
import { mkdtemp, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { scanFiles } from './check-theme-literals.mjs';

let passed = 0;
let failed = 0;

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed++;
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
  }
}

// ── Test 1: plan example — Bad.tsx with #030812 and #1677ff ──
await test('flags near-black background and legacy Ant Design blue', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(join(root, 'Bad.tsx'), "export const bad = { background: '#030812', color: '#1677ff' };\n");
  const findings = await scanFiles([join(root, 'Bad.tsx')]);
  assert.deepEqual(findings.map((item) => item.literal).sort(), ['#030812', '#1677ff']);
});

// ── Test 2: does not flag theme-allowed directories ──
await test('skips files under src/theme and src/styles', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await mkdir(join(root, 'src', 'theme'), { recursive: true });
  await mkdir(join(root, 'src', 'styles'), { recursive: true });
  await mkdir(join(root, 'src', 'pages'), { recursive: true });
  await writeFile(
    join(root, 'src', 'theme', 'tokens.ts'),
    "export const bg = '#030812';\nexport const blue = '#1677ff';\n",
  );
  await writeFile(
    join(root, 'src', 'styles', 'ocean.css'),
    '.ocean-backdrop { background: #030812; color: #1677ff; }\n',
  );
  await writeFile(
    join(root, 'src', 'pages', 'BadPage.tsx'),
    "export const bad = { background: '#1677ff' };\n",
  );
  const files = [
    join(root, 'src', 'theme', 'tokens.ts'),
    join(root, 'src', 'styles', 'ocean.css'),
    join(root, 'src', 'pages', 'BadPage.tsx'),
  ];
  const findings = await scanFiles(files);
  // Only BadPage should be flagged; theme/styles are exempt
  assert.equal(findings.length, 1, `expected 1 finding, got ${findings.length}`);
  assert.equal(findings[0].literal, '#1677ff');
});

// ── Test 3: flags legacy gray literals ──
await test('flags #f0f2f5 and #f0f0f0 legacy grays', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Card.tsx'),
    "const styles = { bg: '#f0f2f5', border: '#f0f0f0' };\n",
  );
  const findings = await scanFiles([join(root, 'Card.tsx')]);
  assert.deepEqual(findings.map((f) => f.literal).sort(), ['#f0f0f0', '#f0f2f5']);
});

// ── Test 4: does not flag chart/data context colors ──
await test('exempts echarts itemStyle and data array colors', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Chart.tsx'),
    [
      "const option = {",
      "  series: [{ type: 'line', itemStyle: { color: '#1677ff' } },",
      "  data: [{ value: 1, itemStyle: { color: '#030812' } }],",
      "  color: ['#1677ff', '#f0f2f5'],",
      "  areaStyle: { color: '#f0f0f0' },",
      "};",
    ].join('\n'),
  );
  const findings = await scanFiles([join(root, 'Chart.tsx')]);
  assert.equal(findings.length, 0, `expected 0 findings in data context, got ${findings.length}`);
});

// ── Test 5: flags near-black hex variants ──
await test('flags near-black hex variants like #061321', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(join(root, 'Dark.tsx'), "const bg = { background: '#061321' };\n");
  const findings = await scanFiles([join(root, 'Dark.tsx')]);
  assert.equal(findings.length, 1);
  assert.equal(findings[0].literal, '#061321');
});

// ── Test 6: flags hard-coded white container backgrounds ──
await test('flags hard-coded #fff and #ffffff as background', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Panel.tsx'),
    "const panel = { background: '#ffffff' };\nconst card = { backgroundColor: '#fff' };\n",
  );
  const findings = await scanFiles([join(root, 'Panel.tsx')]);
  assert.equal(findings.length, 2, `expected 2 white-bg findings, got ${findings.length}`);
  const literals = findings.map((f) => f.literal).sort();
  assert.equal(literals[0], '#fff');
  assert.equal(literals[1], '#ffffff');
});

// ── Test 7: does not flag white as text color ──
await test('does not flag #fff when used as text color (non-background)', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(join(root, 'Text.tsx'), "const label = { color: '#fff' };\n");
  const findings = await scanFiles([join(root, 'Text.tsx')]);
  assert.equal(findings.length, 0, 'white as text color should not be flagged');
});

// ── Test 8: skips test/spec files ──
await test('skips .test.tsx and .spec.ts files', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(join(root, 'Comp.test.tsx'), "const bad = '#1677ff';\n");
  await writeFile(join(root, 'Comp.spec.ts'), "const bad = '#030812';\n");
  const findings = await scanFiles([join(root, 'Comp.test.tsx'), join(root, 'Comp.spec.ts')]);
  assert.equal(findings.length, 0, 'test/spec files should be skipped');
});

// ── Test 9: flags rgba legacy gray ──
await test('flags rgba(240, 242, 245) legacy gray', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Layout.tsx'),
    "const layout = { background: 'rgba(240, 242, 245, 0.85)' };\n",
  );
  const findings = await scanFiles([join(root, 'Layout.tsx')]);
  assert.equal(findings.length, 1);
  assert.ok(findings[0].literal.includes('240'));
});

// ── Test 10: does not flag approved Ocean palette colors ──
await test('does not flag approved Polar Mist palette colors', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Ocean.tsx'),
    [
      "const ocean = {",
      "  bg: '#A9D2DF',",
      "  text: '#102F44',",
      "  action: '#1686AE',",
      "  accent: '#39B9C2',",
      "  status: '#14765E',",
      "  border: 'rgba(24, 102, 133, 0.16)',",
      "  surface: 'rgba(240, 250, 251, 0.72)',",
      "};",
    ].join('\n'),
  );
  const findings = await scanFiles([join(root, 'Ocean.tsx')]);
  assert.equal(findings.length, 0, 'approved Ocean palette should not be flagged');
});

// ── Test 11: finding objects include file and line ──
await test('finding objects include file, line, literal, and reason', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(join(root, 'Bad.tsx'), "const x = '#1677ff';\n");
  const findings = await scanFiles([join(root, 'Bad.tsx')]);
  assert.equal(findings.length, 1);
  const f = findings[0];
  assert.ok(typeof f.file === 'string' && f.file.length > 0, 'file must be a non-empty string');
  assert.equal(f.line, 1);
  assert.equal(f.literal, '#1677ff');
  assert.ok(typeof f.reason === 'string' && f.reason.length > 0, 'reason must be a non-empty string');
});

// ── Test 12: does not flag embedded JSON payload color field ──
await test('exempts JSON payload "color" fields', async () => {
  const root = await mkdtemp(join(tmpdir(), 'irip-theme-'));
  await writeFile(
    join(root, 'Payload.tsx'),
    'const payload = `{"color": "#1677ff", "background": "#030812"}`;\n',
  );
  const findings = await scanFiles([join(root, 'Payload.tsx')]);
  assert.equal(findings.length, 0, 'embedded payload content should be exempted');
});

// ── Summary ──
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
