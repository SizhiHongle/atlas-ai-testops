import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Atlas AI 测试平台原型",
  description: "AI 原生测试平台整套交互原型"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
