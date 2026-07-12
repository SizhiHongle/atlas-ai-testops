import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "登录 · Atlas AI 测试平台",
  description: "连接企业身份并进入 Atlas AI 测试空间"
};

export default function LoginLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
