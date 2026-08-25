"use client";

import { useCallback, useMemo, useState } from "react";
import Map, { Layer, Marker, Source } from "react-map-gl/maplibre";
import type { Map as MaplibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type {
  DataFreshness,
  Gate,
  LayerId,
  LngLat,
  Resource,
  Zone,
} from "@/types";
import LayerToggleChip from "@/components/LayerToggleChip";
import LiveCachedBadge from "@/components/LiveCachedBadge";
import MapMarker from "@/components/map/MapMarker";

const BASEMAP_STYLE =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const LAYER_LABELS: { id: LayerId; label: string }[] = [
  { id: "temperature", label: "Temperature" },
  { id: "wbgt", label: "WBGT bands" },
  { id: "shade", label: "Shade" },
  { id: "queues", label: "Queues" },
];

interface MapCanvasProps {
  zones: Zone[];
  gates: Gate[];
  resources: Resource[];
  layers: Record<LayerId, boolean>;
  onToggleLayer: (id: LayerId) => void;
  freshness: DataFreshness;
  scenarioName: string;
  onResourceMove: (id: string, coordinates: LngLat) => void;
}

/** Reads token colours from CSS variables so map paint stays on the palette.
 *
 * Read once, lazily, on first render rather than in an effect. This component is
 * imported with `ssr: false`, so the document exists by the time it mounts and
 * there is nothing to wait for. Setting state from an effect meant one extra
 * render with `colors` null on every mount, which Next 16's
 * `react-hooks/set-state-in-effect` rule flags and which was already costing the
 * map a paint it did not need.
 */
const TOKEN_NAMES = {
  low: "--wbgt-low",
  moderate: "--wbgt-moderate",
  high: "--wbgt-high",
  extreme: "--wbgt-extreme",
  border: "--base-border",
  shade: "--base-secondary",
  heat1: "--heat-1",
  heat2: "--heat-2",
  heat3: "--heat-3",
  heat4: "--heat-4",
  heat5: "--heat-5",
  heat6: "--heat-6",
  heat7: "--heat-7",
} as const;

function readTokenColors(): Record<string, string> | null {
  if (typeof document === "undefined") return null;
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(
    Object.entries(TOKEN_NAMES).map(([key, variable]) => [
      key,
      style.getPropertyValue(variable).trim(),
    ]),
  );
}

function useTokenColors() {
  const [colors] = useState<Record<string, string> | null>(readTokenColors);
  return colors;
}

function zonesToGeoJson(zones: Zone[]) {
  return {
    type: "FeatureCollection" as const,
    features: zones.map((z) => ({
      type: "Feature" as const,
      properties: {
        id: z.id,
        band: z.wbgtBand,
        temperatureC: z.temperatureC,
        shadeCoverage: z.shadeCoverage,
      },
      geometry: {
        type: "Polygon" as const,
        coordinates: [z.polygon],
      },
    })),
  };
}

/** Adds a diagonal hatch pattern image built from the shade token colour. */
function addHatchImage(map: MaplibreMap, shadeColor: string) {
  if (map.hasImage("shade-hatch")) return;
  const size = 8;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.strokeStyle = shadeColor;
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(-2, size + 2);
  ctx.lineTo(size + 2, -2);
  ctx.stroke();
  const data = ctx.getImageData(0, 0, size, size);
  map.addImage("shade-hatch", {
    width: size,
    height: size,
    data: new Uint8Array(data.data.buffer),
  });
}

export default function MapCanvas({
  zones,
  gates,
  resources,
  layers,
  onToggleLayer,
  freshness,
  scenarioName,
  onResourceMove,
}: MapCanvasProps) {
  const colors = useTokenColors();
  const [draggingId, setDraggingId] = useState<string | null>(null);

  const geojson = useMemo(() => zonesToGeoJson(zones), [zones]);
  const maxQueue = Math.max(1, ...gates.map((g) => g.queueLength));

  const center = useMemo(() => {
    const all = zones.flatMap((z) => z.polygon);
    const lng = all.reduce((s, p) => s + p[0], 0) / Math.max(1, all.length);
    const lat = all.reduce((s, p) => s + p[1], 0) / Math.max(1, all.length);
    return { longitude: lng || -112.074, latitude: lat || 33.448 };
  }, [zones]);

  const handleLoad = useCallback(
    (e: { target: MaplibreMap }) => {
      if (colors) addHatchImage(e.target, colors.shade);
    },
    [colors],
  );

  return (
    <div className="relative h-full w-full overflow-hidden bg-base-bg">
      {colors ? (
        <Map
          initialViewState={{ ...center, zoom: 15.4 }}
          mapStyle={BASEMAP_STYLE}
          onLoad={handleLoad}
          attributionControl={false}
        >
          <Source id="zones" type="geojson" data={geojson}>
            {layers.wbgt ? (
              <Layer
                id="zones-wbgt"
                type="fill"
                paint={{
                  "fill-color": [
                    "match",
                    ["get", "band"],
                    "low",
                    colors.low,
                    "moderate",
                    colors.moderate,
                    "high",
                    colors.high,
                    "extreme",
                    colors.extreme,
                    colors.border,
                  ],
                  "fill-opacity": 0.72,
                }}
              />
            ) : null}
            {layers.temperature ? (
              <Layer
                id="zones-temperature"
                type="fill"
                paint={{
                  "fill-color": [
                    "interpolate",
                    ["linear"],
                    ["get", "temperatureC"],
                    30,
                    colors.heat1,
                    33,
                    colors.heat2,
                    36,
                    colors.heat3,
                    39,
                    colors.heat4,
                    42,
                    colors.heat5,
                    45,
                    colors.heat6,
                    48,
                    colors.heat7,
                  ],
                  "fill-opacity": 0.45,
                }}
              />
            ) : null}
            {layers.shade ? (
              <Layer
                id="zones-shade"
                type="fill"
                paint={{
                  "fill-pattern": "shade-hatch",
                  "fill-opacity": ["get", "shadeCoverage"],
                }}
              />
            ) : null}
            <Layer
              id="zones-outline"
              type="line"
              paint={{ "line-color": colors.border, "line-width": 1.5 }}
            />
          </Source>

          {gates.map((gate) => (
            <Marker
              key={gate.id}
              longitude={gate.coordinates[0]}
              latitude={gate.coordinates[1]}
              anchor="bottom"
            >
              <MapMarker
                kind="gate"
                label={gate.name}
                queueFraction={layers.queues ? gate.queueLength / maxQueue : 0}
                queueLength={layers.queues ? gate.queueLength : undefined}
              />
            </Marker>
          ))}

          {gates
            .filter((g) => g.staffCount > 0)
            .map((gate) => (
              <Marker
                key={`${gate.id}-staff`}
                longitude={gate.coordinates[0] + 0.0004}
                latitude={gate.coordinates[1] - 0.0002}
                anchor="bottom"
              >
                <MapMarker kind="staff" label={`x${gate.staffCount}`} />
              </Marker>
            ))}

          {resources.map((r) => (
            <Marker
              key={r.id}
              longitude={r.coordinates[0]}
              latitude={r.coordinates[1]}
              anchor="bottom"
              draggable={r.movable}
              onDragStart={() => setDraggingId(r.id)}
              onDragEnd={(e) => {
                setDraggingId(null);
                onResourceMove(r.id, [e.lngLat.lng, e.lngLat.lat]);
              }}
            >
              <MapMarker
                kind={r.type}
                label={r.name}
                movable={r.movable}
                dragging={draggingId === r.id}
              />
            </Marker>
          ))}
        </Map>
      ) : null}

      <div className="absolute left-4 top-4 flex gap-2">
        {LAYER_LABELS.map(({ id, label }) => (
          <LayerToggleChip
            key={id}
            label={label}
            on={layers[id]}
            onToggle={() => onToggleLayer(id)}
          />
        ))}
      </div>

      <div className="absolute right-4 top-4 flex items-center gap-3">
        <span className="rounded-pill border border-base-border bg-base-surface/90 px-3 py-1 text-caption text-base-tertiary">
          {scenarioName}
        </span>
        <LiveCachedBadge freshness={freshness} />
      </div>
    </div>
  );
}
