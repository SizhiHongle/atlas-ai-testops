import { redirect } from "next/navigation";

export default async function ProjectPage({
  params
}: Readonly<{ params: Promise<{ projectId: string }> }>) {
  const { projectId } = await params;
  redirect(`/projects/${projectId}/space`);
}
