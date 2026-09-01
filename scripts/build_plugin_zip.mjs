#!/usr/bin/env node
// build_plugin_zip.mjs — one command: dist/deepseek-megapack.zip + dist/index.yml
//
// CommunityScripts shape: zip entries sit at the ZIP ROOT (no wrapper folder),
// matching what build_site.sh-style packaging produces, so Stash's plugin
// source index can consume dist/index.yml directly.
//
// Node BUILTINS ONLY (node:fs / node:path / node:zlib / node:crypto /
// node:child_process) — deliberately no npm dependencies.
//
// Executable-bit note: shelling out to PowerShell Compress-Archive would DROP
// install.sh's executable bit. This hand-rolled zip writer instead stamps unix
// mode bits into each central-directory entry's external attrs (install.sh =>
// 0755), so the bit survives extraction on Linux/macOS; Windows extractors
// ignore those bits, which is exactly right for install.ps1 users.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateRawSync } from "node:zlib";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pluginDir = join(repoRoot, "plugin");
const backendPkg = join(repoRoot, "backend", "empornium_megapack");
const distDir = join(repoRoot, "dist");
const zipPath = join(distDir, "deepseek-megapack.zip");
const indexPath = join(distDir, "index.yml");

// Explicit deny list — a future plugin/ addition matching any of these can
// never leak into the zip. `tests` covers plugin/tests/* wholesale (the old
// mock_scenes.html dev fixture lives in tests/, not in the shipped tree).
const DENIED_DIRS = new Set(["tests", "__pycache__", ".git"]);
const isDenied = (relPosix) => {
  const segs = relPosix.split("/");
  const base = segs[segs.length - 1];
  return (
    segs.some((s) => DENIED_DIRS.has(s)) ||
    segs.some((s) => s.startsWith(".venv")) ||
    base.endsWith(".pyc") ||
    base.startsWith("config.local")
  );
};

// Walk srcDir, returning { rel (posix), abs } for every non-denied file.
const collectFiles = (srcDir, out = []) => {
  for (const ent of readdirSync(srcDir, { withFileTypes: true })) {
    const abs = join(srcDir, ent.name);
    const rel = relative(pluginDir, abs).split(sep).join("/");
    if (isDenied(rel)) continue;
    if (ent.isDirectory()) collectFiles(abs, out);
    else if (ent.isFile()) out.push({ rel, abs });
  }
  return out;
};

// --- minimal ZIP writer (stored + deflate, unix mode bits) -----------------
let crcTable;
const crc32 = (buf) => {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let c = 0 ^ -1;
  for (let i = 0; i < buf.length; i++) c = (c >>> 8) ^ crcTable[(c ^ buf[i]) & 0xff];
  return (c ^ -1) >>> 0;
};
const dosTime = (d) => ({
  time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
  date: (Math.max(d.getFullYear(), 1980) - 1980) << 9 | ((d.getMonth() + 1) << 5) | d.getDate(),
});

const makeZip = (entries) => {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const e of entries) {
    const { time, date } = dosTime(e.mtime);
    const name = Buffer.from(e.name, "utf8");
    const crc = crc32(e.data);
    const stored = e.data.length === 0;
    const data = stored ? e.data : deflateRawSync(e.data);
    const method = stored ? 0 : 8;
    const mode = e.mode & 0xffff;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(e.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    locals.push(local, name, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4); // version made by: unix
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(e.data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(mode << 16, 38); // external attrs: unix mode
    central.writeUInt32LE(offset, 42);
    centrals.push(central, name);
    offset += local.length + name.length + data.length;
  }
  const cdStart = offset;
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(cdStart, 16);
  return Buffer.concat([...locals, cd, eocd]);
};

// --- metadata sources -------------------------------------------------------
const ymlField = (text, key) => {
  const m = text.match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
  if (!m) throw new Error(`plugin/deepseek-megapack.yml has no ${key}: field`);
  return m[1].trim().replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1");
};
const gitShortSha = () =>
  execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: repoRoot })
    .toString()
    .trim();

// --- build ------------------------------------------------------------------
mkdirSync(distDir, { recursive: true });
const stage = mkdtempSync(join(repoRoot, "dist", ".stage-"));
try {
  const included = collectFiles(pluginDir);
  const excluded = [];
  for (const ent of readdirSync(pluginDir, { withFileTypes: true })) {
    const rel = ent.name;
    if (isDenied(rel)) excluded.push(rel.startsWith("config.local") ? "<config.local* masked>" : rel);
  }
  for (const { rel, abs } of included) {
    const dest = join(stage, rel);
    mkdirSync(dirname(dest), { recursive: true });
    cpSync(abs, dest);
  }
  // Vendored tier-4 package: backend/empornium_megapack -> stage/empornium_megapack
  const pkgFiles = [];
  for (const ent of readdirSync(backendPkg, { withFileTypes: true })) {
    if (ent.isDirectory() && ent.name === "__pycache__") continue;
    if (ent.isFile()) pkgFiles.push(ent.name);
  }
  mkdirSync(join(stage, "empornium_megapack"), { recursive: true });
  for (const f of pkgFiles) cpSync(join(backendPkg, f), join(stage, "empornium_megapack", f));

  // Installers from repo root + generated 3-line INSTALL.txt (README itself is
  // written by a parallel task and is intentionally NOT copied into the zip).
  cpSync(join(repoRoot, "install.ps1"), join(stage, "install.ps1"));
  cpSync(join(repoRoot, "install.sh"), join(stage, "install.sh"));
  writeFileSync(
    join(stage, "INSTALL.txt"),
    [
      "DeepSeek Megapack - quick start",
      "1. Run install.ps1 (Windows) or install.sh (Linux/macOS) from this folder; it creates a .venv beside the plugin files and installs the Python dependencies.",
      "2. Full instructions: see README.md at the root of the deepseek-megapack repository.",
      "",
    ].join("\n"),
  );

  // Fail fast if the acceptance set ever loses a member.
  const required = [
    "deepseek-megapack.yml", "main.js", "style.css", "task.py", "requirements.txt",
    "assets/review.html", "assets/review.js", "empornium_megapack/main.py",
    "install.ps1", "install.sh", "INSTALL.txt",
  ];
  const missing = required.filter((f) => !existsSync(join(stage, f)));
  if (missing.length) throw new Error(`stage missing required files: ${missing.join(", ")}`);

  const walkStage = (abs, name) =>
    statSync(abs).isDirectory()
      ? readdirSync(abs, { withFileTypes: true }).flatMap((c) =>
          walkStage(join(abs, c.name), `${name}/${c.name}`))
      : [{ name, abs }];
  const zipEntries = readdirSync(stage, { withFileTypes: true })
    .flatMap((ent) => walkStage(join(stage, ent.name), ent.name))
    .map(({ name, abs }) => {
      const st = statSync(abs);
      return {
        name,
        data: readFileSync(abs),
        mtime: st.mtime,
        mode: name === "install.sh" ? 0o755 : 0o644,
      };
    })
    .sort((a, b) => (a.name < b.name ? -1 : 1));

  const zip = makeZip(zipEntries);
  writeFileSync(zipPath, zip);
  const sha256 = createHash("sha256").update(zip).digest("hex");

  const yml = readFileSync(join(pluginDir, "deepseek-megapack.yml"), "utf8");
  const version = ymlField(yml, "version");
  const name = ymlField(yml, "name");
  const shortSha = gitShortSha();
  writeFileSync(indexPath, [
    `id: deepseek-megapack`,
    `name: ${name}`,
    `version: "${version}-${shortSha}"`,
    `date: ${new Date().toISOString()}`,
    `path: deepseek-megapack.zip`,
    `sha256: ${sha256}`,
    "",
  ].join("\n"));

  console.log(`zip: ${zipPath} (${zip.length} bytes, ${zipEntries.length} entries)`);
  console.log(`sha256: ${sha256}`);
  console.log(`index: ${indexPath} (version ${version}-${shortSha})`);
  console.log(`excluded by deny-list: ${excluded.length ? excluded.join(", ") : "(none)"}`);
} finally {
  rmSync(stage, { recursive: true, force: true });
  console.log(`stage cleaned: ${stage}`);
}
