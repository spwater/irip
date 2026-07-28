/**
 * Data Ocean — Theme Literal Scanner (dependency-free)
 *
 * Flags legacy and off-theme color literals in migrated TSX files so the
 * Data Ocean / Polar Mist palette stays authoritative.
 *
 * Design rules enforced:
 *   - Near-black backgrounds (#030812, #061321, …) must not become large-area
 *     application backgrounds; they are only allowed inside src/theme and
 *     src/styles where the token system deliberately owns them.
 *   - Legacy Ant Design blue (#1677ff) and grays (#f0f2f5, #f0f0f0) must not
 *     leak into migrated components.
 *   - Hard-coded pure-white container backgrounds (#fff / #ffffff used as
 *     background) are flagged because Data Ocean surfaces are translucent.
 *
 * Exemptions:
 *   - Files under src/theme/ or src/styles/ — the token system lives here.
 *   - Test fixtures (*.test.*, *.spec.*, __tests__/*, test/*).
 *   - Scientific data embedded in user content or chart input payloads —
 *     detected via contextual heuristics (chart option objects, data arrays,
 *     inline JSON payloads) so legitimate data colors are not flagged.
 *
 * CLI:  node scripts/check-theme-literals.mjs <dir>
 * API:  scanFiles(files) → Finding[]
 */

import { readFile } from 'node:fs/promises';
import { relative, sep } from 'node:path';

/**
 * @typedef {Object} Finding
 * @property {string} file    Absolute or relative file path.
 * @property {number} line    1-based line number.
 * @property {string} literal The matched color literal (lowercased).
 * @property {string} reason  Human-readable reason for flagging.
 */

/** Literal blocklist — exact hex matches (lowercase). */
const BLOCKLIST_HEX = new Set([
  '#030812', // near-black background (legacy)
  '#061321', // near-black background (legacy)
  '#1677ff', // legacy Ant Design primary blue
  '#f0f2f5', // legacy Ant Design layout background gray
  '#f0f0f0', // legacy Ant Design bordered background gray
]);

/** Legacy Ant Design rgba backgrounds that should not appear in migrated TSX. */
const BLOCKLIST_RGBA = [
  /rgba?\(\s*240\s*,\s*242\s*,\s*245\s*/i, // #f0f2f5 as rgba
  /rgba?\(\s*240\s*,\s*240\s*,\s*240\s*/i, // #f0f0f0 as rgba
];

/**
 * Near-black threshold: if all RGB channels are <= this value the hex is
 * considered a near-black background literal.
 */
const NEAR_BLACK_THRESHOLD = 24;

/** Hex literal regex — matches #rgb, #rrggbb, #rrggbbaa, #rrrrggggbbbb. */
const HEX_RE = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/g;

/** rgba/rgb literal regex. */
const RGBA_RE = /rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*(?:,\s*[\d.]+\s*)?\)/gi;

/**
 * Context patterns that indicate a color literal is part of scientific data,
 * a chart input payload, or embedded user content — not a theme decision.
 * When the line (trimmed) matches one of these, the literal is exempted.
 */
const DATA_CONTEXT_PATTERNS = [
  /itemStyle\s*:/i,
  /lineStyle\s*:/i,
  /areaStyle\s*:/i,
  /color\s*:\s*\[/i, // echarts color array
  /data\s*:\s*\[/i, // chart data array
  /series\s*:/i,
  /visualMap/i,
  /formatter\s*:/i,
  /axisLine/i,
  /splitLine/i,
  /backgroundColor\s*:\s*['"]transparent['"]/i,
];

/** Lines that look like embedded JSON / scientific payload values. */
const PAYLOAD_LINE_PATTERNS = [
  /"color"\s*:/i, // JSON payload color field
  /"background"\s*:\s*['"]#/i,
  /payload\s*:/i,
  /content\s*:\s*`/i, // template literal content (user content)
];

/**
 * Check whether a hex string is a near-black background.
 * @param {string} hex - hex literal like "#030812"
 * @returns {boolean}
 */
function isNearBlack(hex) {
  const h = hex.replace('#', '');
  let r, g, b;
  if (h.length === 3) {
    r = parseInt(h[0] + h[0], 16);
    g = parseInt(h[1] + h[1], 16);
    b = parseInt(h[2] + h[2], 16);
  } else if (h.length === 6 || h.length === 8) {
    r = parseInt(h.slice(0, 2), 16);
    g = parseInt(h.slice(2, 4), 16);
    b = parseInt(h.slice(4, 6), 16);
  } else {
    return false;
  }
  return r <= NEAR_BLACK_THRESHOLD && g <= NEAR_BLACK_THRESHOLD && b <= NEAR_BLACK_THRESHOLD;
}

/**
 * Check whether a hex string represents pure white (#fff or #ffffff).
 * @param {string} hex
 * @returns {boolean}
 */
function isPureWhite(hex) {
  const h = hex.replace('#', '');
  if (h.length === 3) return h === 'fff';
  if (h.length === 6 || h.length === 8) return h.slice(0, 6) === 'ffffff';
  return false;
}

/**
 * Determine whether the literal on a given line is in a data/chart context
 * and should be exempted.
 * @param {string} line - the full source line
 * @returns {boolean}
 */
function isDataContext(line) {
  return (
    DATA_CONTEXT_PATTERNS.some((re) => re.test(line)) ||
    PAYLOAD_LINE_PATTERNS.some((re) => re.test(line))
  );
}

/**
 * Determine whether a hex literal is used as a background (heuristic: the
 * line contains "background" or "bg" property).  Pure-white is only flagged
 * when used as a background, not as a text color or border.
 * @param {string} line
 * @returns {boolean}
 */
function isBackgroundContext(line) {
  return /background|bgColor|backgroundColor|bg-color/i.test(line);
}

/**
 * Determine whether a file path should be skipped entirely.
 * Allowed directories: src/theme, src/styles.  Test fixtures are skipped.
 * @param {string} filePath
 * @returns {boolean}
 */
function shouldSkipFile(filePath) {
  const normalized = filePath.split(sep).join('/');
  // Allowed theme/style directories
  if (/\/src\/theme\//.test(normalized) || /\/src\/styles\//.test(normalized)) return true;
  // Non-tsx/ts files
  if (!/\.(tsx?|jsx?)$/i.test(normalized)) return true;
  // Test fixtures
  if (/\.(test|spec)\./i.test(normalized)) return true;
  if (/__tests__\//i.test(normalized) || /\/test\//i.test(normalized)) return true;
  return false;
}

/**
 * Scan a single file for off-theme color literals.
 * @param {string} filePath - absolute path to the file
 * @returns {Promise<Finding[]>}
 */
async function scanFile(filePath) {
  if (shouldSkipFile(filePath)) return [];

  let content;
  try {
    content = await readFile(filePath, 'utf-8');
  } catch {
    return [];
  }

  const lines = content.split('\n');
  /** @type {Finding[]} */
  const findings = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineNum = i + 1;
    const inDataContext = isDataContext(line);

    // --- Hex literals ---
    const hexMatches = line.match(HEX_RE) ?? [];
    for (const raw of hexMatches) {
      const literal = raw.toLowerCase();

      // Exact blocklist match
      if (BLOCKLIST_HEX.has(literal)) {
        if (inDataContext) continue;
        findings.push({
          file: filePath,
          line: lineNum,
          literal,
          reason: 'legacy or near-black off-theme literal',
        });
        continue;
      }

      // Near-black heuristic
      if (isNearBlack(literal)) {
        if (inDataContext) continue;
        findings.push({
          file: filePath,
          line: lineNum,
          literal,
          reason: 'near-black background literal',
        });
        continue;
      }

      // Pure-white as background (translucent surfaces should be used instead)
      if (isPureWhite(literal) && isBackgroundContext(line)) {
        if (inDataContext) continue;
        findings.push({
          file: filePath,
          line: lineNum,
          literal,
          reason: 'hard-coded white container background; use ocean-panel surface',
        });
      }
    }

    // --- rgba/rgb literals ---
    const rgbaMatches = line.match(RGBA_RE) ?? [];
    for (const raw of rgbaMatches) {
      if (inDataContext) continue;
      for (const re of BLOCKLIST_RGBA) {
        if (re.test(raw)) {
          findings.push({
            file: filePath,
            line: lineNum,
            literal: raw.toLowerCase(),
            reason: 'legacy Ant Design gray rgba literal',
          });
          break;
        }
      }
    }
  }

  return findings;
}

/**
 * Scan multiple files and return all findings.
 * @param {string[]} files - array of absolute file paths
 * @returns {Promise<Finding[]>}
 */
export async function scanFiles(files) {
  /** @type {Finding[]} */
  const all = [];
  for (const f of files) {
    const found = await scanFile(f);
    all.push(...found);
  }
  return all;
}

/**
 * Recursively collect TSX/TS files under a directory, excluding skipped paths.
 * @param {string} dir
 * @returns {Promise<string[]>}
 */
async function collectFiles(dir) {
  const { readdir } = await import('node:fs/promises');
  const { stat } = await import('node:fs/promises');
  /** @type {string[]} */
  const results = [];
  let entries;
  try {
    entries = await readdir(dir);
  } catch {
    return results;
  }
  for (const entry of entries) {
    const full = `${dir}${sep}${entry}`;
    let s;
    try {
      s = await stat(full);
    } catch {
      continue;
    }
    if (s.isDirectory()) {
      const sub = await collectFiles(full);
      results.push(...sub);
    } else if (s.isFile() && /\.(tsx?|jsx?)$/i.test(entry)) {
      if (!shouldSkipFile(full)) results.push(full);
    }
  }
  return results;
}

/**
 * CLI entry point.
 * @param {string[]} argv
 * @returns {Promise<number>} exit code
 */
export async function main(argv) {
  const dir = argv[2];
  if (!dir) {
    console.error('Usage: node scripts/check-theme-literals.mjs <dir>');
    return 2;
  }

  const { resolve } = await import('node:path');
  const root = resolve(dir);

  const files = await collectFiles(root);
  const findings = await scanFiles(files);

  if (findings.length === 0) {
    console.log(`✓ No off-theme color literals found in ${files.length} files under ${root}`);
    return 0;
  }

  console.error(`✗ Found ${findings.length} off-theme color literal(s):\n`);
  for (const f of findings) {
    const rel = relative(process.cwd(), f.file).split(sep).join('/');
    console.error(`  ${rel}:${f.line}  ${f.literal}  — ${f.reason}`);
  }
  return 1;
}

// Run CLI when invoked directly (not imported as a module)
const isMain = process.argv[1] && process.argv[1].endsWith('check-theme-literals.mjs');
if (isMain) {
  main(process.argv)
    .then((code) => process.exit(code))
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
