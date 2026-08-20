import type { Metadata } from "next";
import PlanWorkspace from "@/components/PlanWorkspace";
import { mockPlanWorkspaceData } from "@/lib/mockData";

export const metadata: Metadata = {
  title: "Plan Workspace | ThermCue",
};

export default function PlanPage() {
  // Mock data is injected here and nowhere else. Swap this import for a real
  // API source and the workspace renders unchanged.
  return <PlanWorkspace data={mockPlanWorkspaceData} />;
}
