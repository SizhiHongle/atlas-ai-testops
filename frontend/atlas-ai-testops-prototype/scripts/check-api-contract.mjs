import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contractPath = resolve(projectRoot, "../../contracts/openapi.json");
const generatedPath = resolve(projectRoot, "lib/api/schema.d.ts");
const cliPath = resolve(projectRoot, "node_modules/openapi-typescript/bin/cli.js");
const temporaryDirectory = await mkdtemp(resolve(tmpdir(), "atlas-openapi-"));
const candidatePath = resolve(temporaryDirectory, "schema.d.ts");

try {
  const result = spawnSync(
    process.execPath,
    [cliPath, contractPath, "-o", candidatePath],
    { cwd: projectRoot, stdio: "inherit" }
  );
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }

  const [current, candidate] = await Promise.all([
    readFile(generatedPath, "utf8"),
    readFile(candidatePath, "utf8")
  ]);
  if (current !== candidate) {
    throw new Error("前端 API 类型已过期，请运行 npm run generate:api");
  }
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
