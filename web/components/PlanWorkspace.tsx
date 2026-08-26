"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AgentFeedEntry,
  LayerId,
  LngLat,
  PlanWorkspaceData,
  Resource,
} from "@/types";
import RightRail from "@/components/RightRail";
import TimeSlider from "@/components/TimeSlider";
import { ToastProvider, useToast } from "@/components/Toast";

const MapCanvas = dynamic(() => import("@/components/map/MapCanvas"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-base-bg text-caption text-base-muted">
      Loading map
    </div>
  ),
});

interface PlanWorkspaceProps {
  data: PlanWorkspaceData;
}

export default function PlanWorkspace({ data }: PlanWorkspaceProps) {
  return (
    <ToastProvider>
      <PlanWorkspaceInner data={data} />
    </ToastProvider>
  );
}

function PlanWorkspaceInner({ data }: PlanWorkspaceProps) {
  const { scenario } = data;
  const toast = useToast();

  const [currentHour, setCurrentHour] = useState(
    scenario.timeWindow.startHour,
  );
  const [playing, setPlaying] = useState(false);
  const [layers, setLayers] = useState<Record<LayerId, boolean>>({
    temperature: false,
    wbgt: true,
    shade: false,
    queues: true,
  });
  const [expandedChangeId, setExpandedChangeId] = useState<string | null>(
    data.planChanges[0]?.id ?? null,
  );
  const [resources, setResources] = useState<Resource[]>(scenario.resources);
  const [agentFeed, setAgentFeed] = useState<AgentFeedEntry[]>(data.agentFeed);
  const [replanActiveId, setReplanActiveId] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const timeouts = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const pending = timeouts.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  useEffect(() => {
    if (!playing) return;
    const interval = setInterval(() => {
      setCurrentHour((h) => {
        if (h >= scenario.timeWindow.endHour) {
          setPlaying(false);
          return h;
        }
        return h + 1;
      });
    }, 900);
    return () => clearInterval(interval);
  }, [playing, scenario.timeWindow.endHour]);

  const zonesAtHour = useMemo(() => {
    return scenario.zones.map((zone) => {
      const state = data.hourlyZoneStates.find(
        (s) => s.zoneId === zone.id && s.hour === currentHour,
      );
      return state
        ? {
            ...zone,
            wbgtBand: state.wbgtBand,
            temperatureC: state.temperatureC,
            shadeCoverage: state.shadeCoverage,
          }
        : zone;
    });
  }, [scenario.zones, data.hourlyZoneStates, currentHour]);

  const gatesAtHour = useMemo(() => {
    return scenario.gates.map((gate) => {
      const state = data.queueStates.find(
        (q) => q.gateId === gate.id && q.hour === currentHour,
      );
      return state
        ? {
            ...gate,
            // Older engines did not publish queueLength yet. Their
            // personMinutes are still enough to recover the time-average for
            // a one-hour row, so staggered frontend/backend deploys stay safe.
            queueLength: Math.round(state.queueLength ?? state.personMinutes / 60),
            waitTimeMinutes: state.waitTimeMinutes,
          }
        : gate;
    });
  }, [scenario.gates, data.queueStates, currentHour]);

  const handleToggleLayer = useCallback((id: LayerId) => {
    setLayers((prev) => ({ ...prev, [id]: !prev[id] }));
  }, []);

  const handleResourceMove = useCallback(
    (id: string, coordinates: LngLat) => {
      setResources((prev) =>
        prev.map((r) => (r.id === id ? { ...r, coordinates } : r)),
      );
      const moved = resources.find((r) => r.id === id);
      if (moved) {
        toast("success", `${moved.name} repositioned. Rerun the plan to apply.`);
      }
    },
    [resources, toast],
  );

  const handleSimulateForecast = useCallback(() => {
    if (simulating) return;
    setSimulating(true);
    const now = new Date();
    const stamp = (offsetMs: number) =>
      new Date(now.getTime() + offsetMs).toISOString();

    const monitorEntry: AgentFeedEntry = {
      id: `sim-monitor-${now.getTime()}`,
      timestamp: stamp(0),
      type: "monitor",
      text: "Forecast update received from FortyGuard. Comparing against the active plan window.",
      toolTrace: [
        {
          tool: "fortyguard.env_params",
          input: "venue polygon, 15:00 to 21:00",
          output: "wet bulb +0.8 C at 17:00, Lawn and West Queue",
        },
      ],
    };

    const replanEntry: AgentFeedEntry = {
      id: `sim-replan-${now.getTime()}`,
      timestamp: stamp(800),
      type: "replan",
      text: "Band shift detected: West Queue moves High to Extreme at 17:00. Re-running the queue model.",
      toolTrace: [
        {
          tool: "simpy.queue_model",
          input: "updated bands, current gate config",
          output: "Gate D wait rises to 24 min under Extreme",
        },
        {
          tool: "optimiser.pareto",
          input: "candidate actions, exposure weightings",
          output: "3 candidates; best trades +2 min wait for -18% exposure",
        },
      ],
    };

    const directiveEntry: AgentFeedEntry = {
      id: `sim-directive-${now.getTime()}`,
      timestamp: stamp(2400),
      type: "directive",
      text: "Directive: open Gate D a second lane from 16:30 and move one water point to West Queue. Confirms within tolerance by 17:15.",
      toolTrace: [
        {
          tool: "plan.apply",
          input: "gate D lanes 1 to 2, water point W2 to West Queue",
          output: "plan version 12 issued to operations",
        },
      ],
    };

    setAgentFeed((prev) => [monitorEntry, ...prev]);
    timeouts.current.push(
      setTimeout(() => {
        setAgentFeed((prev) => [replanEntry, ...prev]);
        setReplanActiveId(replanEntry.id);
      }, 800),
      setTimeout(() => {
        setReplanActiveId(null);
        setAgentFeed((prev) => [directiveEntry, ...prev]);
        setSimulating(false);
        toast("agent", "Agent issued a new directive. Plan updated to version 12.");
      }, 2400),
    );
  }, [simulating, toast]);

  return (
    /* h-full, not h-screen: the workspace fills whatever height its parent
     * gives it rather than assuming it owns the viewport. The page now docks a
     * provenance strip beneath it, and with h-screen the workspace covered the
     * full viewport and the strip rendered underneath the map. */
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-[3]">
          <MapCanvas
            zones={zonesAtHour}
            gates={gatesAtHour}
            resources={resources}
            layers={layers}
            onToggleLayer={handleToggleLayer}
            freshness={scenario.dataFreshness}
            scenarioName={scenario.venue}
            onResourceMove={handleResourceMove}
          />
        </div>
        <div className="w-[40%] min-w-[480px] max-w-[576px]">
          <RightRail
            kpis={data.kpis}
            paretoPoints={data.paretoPoints}
            planChanges={data.planChanges}
            expandedChangeId={expandedChangeId}
            onToggleChange={(id) =>
              setExpandedChangeId((cur) => (cur === id ? null : id))
            }
            agentFeed={agentFeed}
            replanActiveId={replanActiveId}
            onSimulateForecast={handleSimulateForecast}
            simulating={simulating}
            validationPoints={data.validationPoints}
            validationSummary={data.validationSummary}
            observedValidation={data.observedValidation}
            zones={scenario.zones}
            timezone={scenario.timezone}
          />
        </div>
      </div>

      <TimeSlider
        startHour={scenario.timeWindow.startHour}
        endHour={scenario.timeWindow.endHour}
        value={currentHour}
        onChange={(h) => {
          setPlaying(false);
          setCurrentHour(h);
        }}
        playing={playing}
        onTogglePlay={() => setPlaying((p) => !p)}
        wbgtHourly={data.wbgtHourly}
      />
    </div>
  );
}
