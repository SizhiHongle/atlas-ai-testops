import type { Metadata } from "next";

import { LoginPage } from "@/features/auth/ui/login-page";

export const metadata: Metadata = {
  title: "登录"
};

export default function LoginRoute() {
  return <LoginPage />;
}
