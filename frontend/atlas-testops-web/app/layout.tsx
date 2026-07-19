import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@/shared/styles/globals.css";

import { AppProviders } from "./providers";

export const metadata: Metadata = {
  title: {
    default: "Atlas AI TestOps",
    template: "%s | Atlas AI TestOps"
  },
  description: "AI 原生测试运营平台"
};

export default function RootLayout({
  children
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
