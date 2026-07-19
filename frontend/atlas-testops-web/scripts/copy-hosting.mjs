import { copyFile, mkdir, rm } from "node:fs/promises";

await rm("dist/server/.dev.vars", { force: true });
await mkdir("dist/.openai", { recursive: true });
await copyFile(".openai/hosting.json", "dist/.openai/hosting.json");
