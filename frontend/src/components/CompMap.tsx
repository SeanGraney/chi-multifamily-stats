import { memo, useMemo } from "react";
import { divIcon, latLngBounds } from "leaflet";
import type { DivIcon, LatLngBoundsExpression, LatLngTuple, Map as LeafletMap } from "leaflet";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import type { components } from "../api/schema";

type DerivedComp = components["schemas"]["DerivedComp"];
type DeriveRequest = components["schemas"]["DeriveRequest"];
type Subject = DeriveRequest["subject"];

/**
 * F6-S1 — the map half of Results.
 *
 * D5/D13, and it is the whole story here: **this component decides nothing
 * about membership.** A pin's `data-pin-state` is `comp.state` verbatim — the
 * label `pipeline/derive.py` produced from its single `included` mask. It is
 * never `weight > 0`, never `distance_mi <= radius`, never `!censored`, and
 * never any other predicate reconstructed from the fields sitting on the comp.
 *
 * That temptation is real and specific: `distance_mi`, `censored` and
 * `removal_class` are all on every `DerivedComp`, so "which pins do I paint
 * rust?" looks like one comparison away. Two implementations of one rule is
 * exactly the drift the architecture exists to remove, and on a map the drift
 * is invisible until a user has already acted on it (F6 epic: *"map and list
 * never disagree"*).
 *
 * The map and the list are two renderers over ONE client-side source: the
 * single `derived` object `useDerive` sets atomically per response, read in
 * one render pass by `Results`. Neither panel fetches, caches or re-partitions
 * anything of its own.
 *
 * SCOPE (PM ruling at dispatch): pins, their state, their placement, the
 * subject marker, and the unplaceable disclosure. Deliberately NOT built here
 * — pin tooltips and hover sync (F6-S2/S3, cut), the pulsing ring for censored
 * comps, the legend, clustering (which would break "exactly one pin per
 * comp"), and any zoom/pan/fit behaviour beyond the initial framing below.
 */

/** The three states a pin can be in — `DerivedComp["state"]`, no wider. */
type PinState = DerivedComp["state"];

const PIN_COLOUR: Record<PinState, string> = {
  // Design tokens, tailwind.config.ts. Colour is presentation; the STATE is
  // the contract, which is why every test keys on `data-pin-state`.
  included: "#52d48a", // green
  excluded: "#555555", // grey
  filtered: "#c45c2a", // rust — filtered comps keep their pin (F7-S1)
};

const SUBJECT_COLOUR = "#f5a623"; // amber

const PIN_PX = 12;
const SUBJECT_PX = 18;
const MAP_HEIGHT_PX = 420;

/**
 * Whether a coordinate is one we can honestly draw.
 *
 * `(0, 0)` is not a location in a Chicago pull — it is the Gulf of Guinea, and
 * it is how a missing coordinate currently reaches the view: `shape.py` does
 * `float(record.get("latitude") or 0.0)`, so "absent" is destroyed before a
 * `Spell` exists (QUEUE.md row 9d). Plotting it would put a comp 5,000 miles
 * into the Atlantic at a place no evidence supports.
 *
 * `null`/`undefined`/`NaN` are handled here too, unasserted today on purpose:
 * when row 9d lands and types `lat`/`lng` as optional, absence arrives as
 * `null` instead and this predicate already answers the same way. The claim is
 * about "no honest coordinate", not about the sentinel that encodes it.
 *
 * NORTH_STAR: state the absence, never fill it in. This is the only judgement
 * this component makes, and it is a judgement about a *missing field*, not
 * about membership.
 *
 * Not exported: D23 scopes the Vitest leg to `useDerive`'s timing and nothing
 * else, so an exported seam with no importer would be dead surface. The (0, 0)
 * branch is asserted end-to-end by F6-S1's AC9; the `null`/`NaN` branches are
 * deliberately unasserted until row 9d makes them reachable.
 */
function hasHonestCoordinate(lat: unknown, lng: unknown): boolean {
  if (typeof lat !== "number" || typeof lng !== "number") return false;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  return !(lat === 0 && lng === 0);
}

/**
 * A pin as a `DivIcon` built from a real DOM node rather than an HTML string.
 *
 * Leaflet accepts either (`DivIcon.createIcon` branches on `instanceof
 * Element`), and the element form is the one that cannot be made to inject:
 * `comp.key` is a normalized address+unit and goes through `setAttribute`, so
 * a quote in an address is data, not markup.
 */
function markerIcon(attrs: Record<string, string>, colour: string, sizePx: number): DivIcon {
  const dot = document.createElement("span");
  for (const [name, value] of Object.entries(attrs)) dot.setAttribute(name, value);
  dot.style.display = "block";
  dot.style.width = `${sizePx}px`;
  dot.style.height = `${sizePx}px`;
  dot.style.borderRadius = "50%";
  dot.style.background = colour;
  dot.style.border = "1px solid #0b0c0b";
  dot.style.boxSizing = "border-box";
  return divIcon({
    html: dot,
    className: "",
    iconSize: [sizePx, sizePx],
    iconAnchor: [sizePx / 2, sizePx / 2],
  });
}

interface CompPinProps {
  compKey: string;
  state: PinState;
  lat: number;
  lng: number;
  label: string;
}

/**
 * One comp, one pin — including excluded and filtered comps, which keep theirs.
 *
 * The map never hides evidence (F6 epic). A comp that loses its pin when it
 * leaves the analysis is a map that disagrees with the list in the quietest
 * way available, and a pin that vanishes cannot be clicked back in.
 *
 * `memo` matters at this size: a pull is ~570 comps, and `Results` re-renders
 * on every keystroke in a weight box. The comp objects are identical across
 * renders that did not bring a new payload, so all ~570 markers skip.
 */
const CompPin = memo(function CompPin({ compKey, state, lat, lng, label }: CompPinProps) {
  const icon = useMemo(
    () =>
      markerIcon(
        { "data-testid": "map-pin", "data-comp-key": compKey, "data-pin-state": state, title: label },
        PIN_COLOUR[state],
        PIN_PX,
      ),
    [compKey, state, label],
  );
  const position = useMemo<LatLngTuple>(() => [lat, lng], [lat, lng]);
  return <Marker position={position} icon={icon} />;
});

/**
 * The subject, which is never evidence for itself.
 *
 * It carries no `data-comp-key` and is not a `map-pin`, so nothing that counts
 * comps can count it — the arithmetic and the meaning fail together or not at
 * all.
 */
function SubjectMarker({ subject }: { subject: Subject }) {
  const icon = useMemo(
    () =>
      markerIcon(
        { "data-testid": "map-subject", title: `Subject — ${subject.address}` },
        SUBJECT_COLOUR,
        SUBJECT_PX,
      ),
    [subject.address],
  );
  const position = useMemo<LatLngTuple>(() => [subject.lat, subject.lng], [subject.lat, subject.lng]);
  return <Marker position={position} icon={icon} />;
}

interface CompMapProps {
  /**
   * `derived.comps` whole, not a partition of it. The map draws every comp the
   * server returned and paints each one with the label the server gave it.
   */
  comps: DerivedComp[];
  subject: Subject;
}

export default function CompMap({ comps, subject }: CompMapProps) {
  const placeable = useMemo(
    () => comps.filter((comp) => hasHonestCoordinate(comp.lat, comp.lng)),
    [comps],
  );
  const unplaceable = useMemo(
    () => comps.filter((comp) => !hasHonestCoordinate(comp.lat, comp.lng)),
    [comps],
  );

  /**
   * The initial frame. Viewport geometry, not a statistic — this decides what
   * is on screen, never what a comp means.
   *
   * `MapContainer` reads `bounds` once, at construction (see its source: the
   * ref callback runs one `fitBounds` and never again). That is the behaviour
   * we want and not a limitation: re-framing on every curation change would
   * yank the map out from under a user who had panned somewhere deliberately,
   * and fit/zoom behaviour is out of this story's scope either way.
   *
   * Unplaceable comps are excluded, which is the point — one comp at (0, 0)
   * would otherwise zoom the whole pull down to a smudge over the Atlantic.
   */
  const bounds = useMemo<LatLngBoundsExpression>(() => {
    const points: LatLngTuple[] = placeable.map((comp) => [comp.lat, comp.lng]);
    points.push([subject.lat, subject.lng]);
    return latLngBounds(points).pad(0.08);
    // Framing follows the first payload; see the note above on why it is not
    // recomputed per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * `data-testid="map"` goes on Leaflet's own container, set through the map
   * instance, rather than on a wrapper div. `MapContainer` forwards only
   * `id`/`className`/`style`, and a wrapper would be present (and "visible")
   * even if Leaflet never initialised — a testid that can be satisfied without
   * a map is a testid that can report a green map on a page that has none.
   */
  const tagContainer = (map: LeafletMap | null) => {
    map?.getContainer().setAttribute("data-testid", "map");
  };

  return (
    <div className="mt-4">
      <MapContainer
        bounds={bounds}
        scrollWheelZoom
        style={{ height: MAP_HEIGHT_PX, width: "100%", background: "#0b0c0b" }}
        ref={tagContainer}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        <SubjectMarker subject={subject} />
        {placeable.map((comp) => (
          <CompPin
            key={comp.key}
            compKey={comp.key}
            state={comp.state}
            lat={comp.lat}
            lng={comp.lng}
            label={`${comp.address}${comp.unit ? ` #${comp.unit}` : ""}`}
          />
        ))}
      </MapContainer>
      <UnplaceableDisclosure comps={unplaceable} />
    </div>
  );
}

/**
 * The comps that could not be placed, named out loud.
 *
 * The alternative — drop them from the map and say nothing — is this story's
 * own invariant failing in its quietest form: the map would show fewer comps
 * than the list, without ever admitting it. A gap in one field is not grounds
 * to disappear evidence the user paid for, so the comp keeps its list row
 * throughout and loses only the one thing it has no honest answer for.
 */
function UnplaceableDisclosure({ comps }: { comps: DerivedComp[] }) {
  if (comps.length === 0) return null;
  return (
    <div className="mt-2 border border-rust px-2 py-1 text-xs text-rust">
      <p>
        {comps.length} comp{comps.length === 1 ? "" : "s"} could not be placed on the map — no
        coordinate was reported. {comps.length === 1 ? "It stays" : "They stay"} in the list and in
        every calculation.
      </p>
      <ul>
        {comps.map((comp) => (
          <li key={comp.key} data-testid="map-unplaceable" data-comp-key={comp.key}>
            {comp.address}
            {comp.unit ? ` #${comp.unit}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
