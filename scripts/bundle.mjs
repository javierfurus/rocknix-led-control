import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const rootDir = process.cwd();
const manifestPath = path.join(rootDir, "plugin.json");

if (!existsSync(manifestPath)) {
  throw new Error(`Missing plugin manifest at ${manifestPath}`);
}

if (!existsSync(path.join(rootDir, "dist", "index.js"))) {
  throw new Error('Missing dist/index.js. Run "npm run build" first.');
}

/** @type {{ name?: unknown }} */
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const pluginName =
  typeof manifest.name === "string" && manifest.name.trim()
    ? manifest.name.trim()
    : "decky-plugin";
const safePluginName = pluginName.replace(/[^\w.-]+/g, "_");
const bundleRoot = path.join(rootDir, "bundle");
const stageDir = path.join(bundleRoot, safePluginName);
const zipPath = path.join(bundleRoot, `${safePluginName}.zip`);

rmSync(stageDir, { recursive: true, force: true });
rmSync(zipPath, { force: true });
mkdirSync(stageDir, { recursive: true });

for (const entry of [
  "dist",
  "main.py",
  "backend.py",
  "plugin.json",
  "package.json",
  "README.md",
]) {
  const sourcePath = path.join(rootDir, entry);
  if (existsSync(sourcePath)) {
    cpSync(sourcePath, path.join(stageDir, safePluginName, entry), {
      recursive: true,
    });
  }
}

const zipCheck = spawnSync("zip", ["-v"], { stdio: "ignore" });
if (zipCheck.error || zipCheck.status !== 0) {
  throw new Error(
    'The "zip" command is required to create the plugin archive.',
  );
}

const zipResult = spawnSync("zip", ["-r", "-q", zipPath, "."], {
  cwd: stageDir,
  stdio: "inherit",
});

if (zipResult.error || zipResult.status !== 0) {
  throw new Error(
    `zip failed with status ${zipResult.status ?? "unknown"}${
      zipResult.error ? ` (${zipResult.error.message})` : ""
    }`,
  );
}

console.log(`Created ${path.relative(rootDir, zipPath)}`);
console.log(`Prepared ${path.relative(rootDir, stageDir)}`);
