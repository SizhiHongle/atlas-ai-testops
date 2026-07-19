import type { Metadata } from "next";

import { TaskPage } from "@/features/task/ui/task-page";

export const metadata: Metadata = {
  title: "任务"
};

export default async function TasksPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  return <TaskPage projectId={projectId} />;
}
