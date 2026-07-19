import "server-only";

import { z } from "zod";

const serverEnvironmentSchema = z.object({
  ATLAS_API_ORIGIN: z.url().refine(
    (value) => {
      const url = new URL(value);
      return (
        (url.protocol === "http:" || url.protocol === "https:") &&
        url.pathname === "/" &&
        !url.search &&
        !url.hash
      );
    },
    { message: "ATLAS_API_ORIGIN 必须是没有路径的 HTTP(S) Origin。" }
  )
});

export type ServerEnvironment = z.infer<typeof serverEnvironmentSchema>;

export function getServerEnvironment(): ServerEnvironment {
  return serverEnvironmentSchema.parse({
    ATLAS_API_ORIGIN: process.env.ATLAS_API_ORIGIN
  });
}
