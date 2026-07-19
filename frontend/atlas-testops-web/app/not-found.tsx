import Link from "next/link";

import { ErrorState } from "@/shared/ui/feedback/error-state";

export default function NotFound() {
  return (
    <>
      <ErrorState title="页面不存在" detail="当前地址不属于 Atlas 工作空间。" />
      <div style={{ display: "flex", justifyContent: "center", marginTop: -80 }}>
        <Link href="/">返回工作空间</Link>
      </div>
    </>
  );
}
