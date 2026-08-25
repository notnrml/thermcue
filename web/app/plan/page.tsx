import type { Metadata } from "next";
import PlanWorkspace from "@/components/PlanWorkspace";
import ProvenanceStrip from "@/components/ProvenanceStrip";
import { fetchPlan } from "@/lib/engine";
import { mockPlanWorkspaceData } from "@/lib/mockData";

export const metadata: Metadata = {
  title: "Plan Workspace | ThermCue",
};

/* Rendered per request. The payload depends on a live forecast and a live
 * optimiser search, so a statically generated page would bake one response into
 * the deployment and serve it for the whole event. */
export const dynamic = "force-dynamic";

export default async function PlanPage() {
  /* The engine is the source of truth. The bundled scenario is the fallback and
   * is labelled as such by the provenance strip, never passed off as measured
   * data: a judge opening the link mid-deploy should see the product working
   * with an honest label rather than a stack trace. */
  const { data, meta, source, error } = await fetchPlan(mockPlanWorkspaceData);

  return (
    <div className="flex h-screen flex-col">
      <div className="min-h-0 flex-1">
        <PlanWorkspace data={data} />
      </div>
      <ProvenanceStrip meta={meta} source={source} error={error} />
    </div>
  );
}
