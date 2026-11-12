#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a grower-level interactive field map as a single lightweight HTML file.

This is the runtime entrypoint for the ``grower-web-map`` subskill. It reads the
field polygons the data pipeline already wrote under
``growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson`` and emits
one self-contained HTML document per grower (all farms merged into one map).

The output embeds vector boundaries only. Satellite imagery is streamed by the
browser from Esri World Imagery; nothing raster is copied into the HTML.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
sys.path.insert(0, str(_RUNTIME_PATHS.runtime_scripts))
sys.path.insert(0, str(_RUNTIME_PATHS.runtime_scripts / "lib"))

from paths import (  # noqa: E402
    GROWERS_ROOT,
    farm_boundary_path,
    farm_dir,
    farm_tables_dir,
    grower_dir,
    grower_map_path,
)

FARM_PALETTE = ("#2563eb", "#16a34a", "#db2777", "#d97706", "#7c3aed", "#0891b2")
COORD_DECIMALS = 6
CONUS_CENTER = (39.5, -98.35)
ESRI_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
OSM_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
ESRI_ATTRIBUTION = (
    "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, "
    "and the GIS User Community"
)


def _clean(value: Any) -> str:
    """JSON/CSV scalar -> display string, collapsing nulls and NaN."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _display_name(meta_path: Path, fallback: str) -> str:
    meta = _read_json(meta_path)
    return _clean(meta.get("display_name")) or fallback


def _farm_slugs(grower_slug: str) -> list[str]:
    farms_root = grower_dir(grower_slug) / "farms"
    if not farms_root.is_dir():
        return []
    return sorted(
        path.name
        for path in farms_root.iterdir()
        if path.is_dir() and (path / "boundary" / "field_boundaries.geojson").is_file()
    )


def _rotation_by_field(grower_slug: str, farm_slug: str) -> dict[str, dict[str, str]]:
    """Read the pipeline's crop-rotation table when this farm has one."""
    tables = farm_tables_dir(grower_slug, farm_slug)
    candidates = sorted(tables.glob("*_crop_rotation.csv")) if tables.is_dir() else []
    if not candidates:
        return {}
    try:
        table = pd.read_csv(candidates[-1])
    except (OSError, ValueError):
        return {}
    if "field_id" not in table.columns:
        return {}

    lookup: dict[str, dict[str, str]] = {}
    for row in table.to_dict("records"):
        start = _clean(row.get("history_start_year"))
        end = _clean(row.get("history_end_year"))
        lookup[_clean(row.get("field_id"))] = {
            "sequence": _clean(row.get("rotation_sequence")),
            "years": f"{start}\u2013{end}" if start and end else "",
            "next": _clean(row.get("predicted_next_crop")),
            "confidence": _clean(row.get("rotation_confidence")),
        }
    return lookup


def _round_coords(node: Any, ndigits: int = COORD_DECIMALS) -> Any:
    """Recursively round GeoJSON coordinates so the payload stays small."""
    if isinstance(node, (list, tuple)):
        return [_round_coords(item, ndigits) for item in node]
    if isinstance(node, float):
        return round(node, ndigits)
    return node


def _acres(row: Any, fields: gpd.GeoDataFrame, index: Any) -> float:
    value = row.get("area_acres") if hasattr(row, "get") else None
    try:
        acres = float(value)
        if math.isfinite(acres) and acres > 0:
            return round(acres, 1)
    except (TypeError, ValueError):
        pass
    try:
        geometry = fields.geometry.loc[index]
        projected = gpd.GeoSeries([geometry], crs=fields.crs).to_crs(5070)
        return round(float(projected.area.iloc[0]) / 4046.8564224, 1)
    except Exception:  # noqa: BLE001 - area is a convenience, not a contract
        return 0.0


def _field_label(row: Any) -> str:
    return (
        _clean(row.get("name"))
        or _clean(row.get("field_name"))
        or _clean(row.get("field_id"))
    )


def _build_feature(
    row: Any,
    index: Any,
    fields: gpd.GeoDataFrame,
    grower: dict[str, str],
    farm: dict[str, str],
    color: str,
    rotation: dict[str, str] | None,
) -> dict[str, Any] | None:
    geometry = fields.geometry.loc[index]
    if geometry is None or geometry.is_empty:
        return None
    rotation = rotation or {}
    return {
        "type": "Feature",
        "geometry": {
            "type": geometry.geom_type,
            "coordinates": _round_coords(geometry.__geo_interface__["coordinates"]),
        },
        "properties": {
            "field": _field_label(row),
            "field_id": _clean(row.get("field_id")),
            "grower": grower["name"],
            "grower_slug": grower["slug"],
            "farm": farm["name"],
            "farm_slug": farm["slug"],
            "acres": _acres(row, fields, index),
            "county": _clean(row.get("county")),
            "state": _clean(row.get("state")),
            "country": _clean(row.get("country")),
            "source": _clean(row.get("source")),
            "rotation": rotation.get("sequence", ""),
            "rotation_years": rotation.get("years", ""),
            "next_crop": rotation.get("next", ""),
            "rotation_confidence": rotation.get("confidence", ""),
            "color": color,
        },
    }


def build_payload(
    grower_slug: str,
    *,
    farms: Iterable[str] | None = None,
    include_rotation: bool = True,
) -> dict[str, Any] | None:
    slug = grower_slug.strip()
    if not slug:
        raise ValueError("grower slug must not be empty")
    grower = {
        "slug": slug,
        "name": _display_name(grower_dir(slug) / "grower.json", slug),
    }
    farm_slugs = list(farms) if farms else _farm_slugs(slug)
    if not farm_slugs:
        return None

    feature_collection: list[dict[str, Any]] = []
    farm_summaries: list[dict[str, Any]] = []
    for order, farm_slug in enumerate(farm_slugs):
        boundary = farm_boundary_path(slug, farm_slug)
        if not boundary.is_file():
            print(f"  skip  {farm_slug}: no field boundaries", file=sys.stderr)
            continue
        fields = gpd.read_file(boundary)
        if fields.empty:
            print(f"  skip  {farm_slug}: boundary file has no fields", file=sys.stderr)
            continue
        if fields.crs is None:
            fields = fields.set_crs("EPSG:4326", allow_override=True)
        elif not fields.crs.is_geographic:
            fields = fields.to_crs("EPSG:4326")

        farm = {
            "slug": farm_slug,
            "name": _display_name(farm_dir(slug, farm_slug) / "farm.json", farm_slug),
        }
        color = FARM_PALETTE[order % len(FARM_PALETTE)]
        rotation_lookup = _rotation_by_field(slug, farm_slug) if include_rotation else {}

        count = 0
        acres = 0.0
        for index, row in fields.iterrows():
            feature = _build_feature(
                row,
                index,
                fields,
                grower,
                farm,
                color,
                rotation_lookup.get(_clean(row.get("field_id"))),
            )
            if feature is None:
                continue
            feature_collection.append(feature)
            count += 1
            acres += float(feature["properties"]["acres"] or 0)
        farm_summaries.append(
            {
                "slug": farm_slug,
                "name": farm["name"],
                "field_count": count,
                "acres": round(acres, 1),
                "color": color,
            }
        )
        print(f"  farm  {farm_slug}: {count} field(s), {acres:.0f} ac")

    if not feature_collection:
        return None

    return {
        "grower": grower,
        "farms": farm_summaries,
        "field_count": len(feature_collection),
        "acres": round(sum(float(f["properties"]["acres"] or 0) for f in feature_collection), 1),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "type": "FeatureCollection",
        "features": feature_collection,
    }


def render_html(payload: dict[str, Any], *, title: str | None = None) -> str:
    grower_name = payload["grower"]["name"]
    page_title = title or f"{grower_name} \u2014 Field Map"
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # A data string containing "</script>" would close the embedding tag early.
    payload_json = payload_json.replace("</", "<\\/")

    return (
        _HTML_TEMPLATE.replace("__TITLE__", _escape_html(page_title))
        .replace("__GROWER_NAME__", _escape_html(grower_name))
        .replace("__FIELD_COUNT__", str(payload["field_count"]))
        .replace("__ACRES__", f'{payload["acres"]:,.0f}')
        .replace("__FARM_COUNT__", str(len(payload["farms"])))
        .replace("__GENERATED_AT__", _escape_html(payload["generated_at"]))
        .replace("__ESRI_TILES__", ESRI_IMAGERY)
        .replace("__OSM_TILES__", OSM_TILES)
        .replace("__ESRI_ATTRIBUTION__", ESRI_ATTRIBUTION)
        .replace("__CENTER__", json.dumps(CONUS_CENTER))
        .replace("__PAYLOAD__", payload_json)
    )


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --accent:#166534; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; color:var(--ink); }
  #map { position:absolute; inset:0; background:#e8eef4; }
  #panel {
    position:absolute; z-index:1000; top:12px; left:12px; width:300px; max-height:calc(100% - 24px);
    display:flex; flex-direction:column; background:rgba(255,255,255,.96); border:1px solid var(--line);
    border-radius:10px; box-shadow:0 8px 24px rgba(15,23,42,.18); overflow:hidden;
  }
  #panel header { padding:12px 14px 10px; border-bottom:1px solid var(--line); }
  #panel h1 { font-size:15px; margin:0 0 2px; letter-spacing:.01em; }
  #panel .stats { font-size:11.5px; color:var(--muted); }
  #panel .actions { display:flex; gap:8px; padding:10px 14px; border-bottom:1px solid var(--line); }
  #panel button {
    font:inherit; font-size:12px; cursor:pointer; border-radius:7px; padding:6px 10px;
    border:1px solid #cbd5e1; background:#f8fafc; color:var(--ink);
  }
  #panel button:hover { background:#eef2f7; }
  #panel button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  #fields { overflow:auto; padding:8px 8px 12px; }
  .farm-group { margin:4px 0 8px; }
  .farm-head { display:flex; align-items:center; gap:7px; padding:4px 6px; font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
  .swatch { width:9px; height:9px; border-radius:2px; flex:none; }
  .field-item {
    display:flex; justify-content:space-between; gap:8px; width:100%; text-align:left;
    background:none; border:0; border-radius:6px; padding:6px 8px; font-size:12.5px; color:var(--ink);
  }
  .field-item:hover, .field-item:focus { background:#ecfdf5; outline:none; }
  .field-item .acres { color:var(--muted); font-size:11.5px; white-space:nowrap; }
  .leaflet-popup-content { margin:12px 14px; font-size:12.5px; line-height:1.5; }
  .popup h3 { margin:0 0 6px; font-size:14.5px; }
  .popup table { border-collapse:collapse; }
  .popup th { text-align:left; font-weight:600; color:var(--muted); padding:1px 10px 1px 0; vertical-align:top; }
  .popup td { padding:1px 0; }
  .popup .rot { margin-top:8px; padding-top:8px; border-top:1px solid var(--line); font-size:12px; }
  .popup .rot b { color:var(--muted); font-weight:600; }
  @media (max-width:640px) {
    #panel { width:min(88vw,300px); }
    #fields { max-height:40vh; }
  }
</style>
</head>
<body>
<div id="map" role="application" aria-label="Grower field map"></div>
<aside id="panel">
  <header>
    <h1>__GROWER_NAME__</h1>
    <div class="stats">__FIELD_COUNT__ fields &middot; __ACRES__ ac &middot; __FARM_COUNT__ farm(s)<br />__GENERATED_AT__</div>
  </header>
  <div class="actions">
    <button type="button" id="fit-all" class="primary">Fit all fields</button>
    <button type="button" id="toggle-fields" aria-expanded="true">Hide list</button>
  </div>
  <nav id="fields" aria-label="Fields"></nav>
</aside>
<script>
var payload = __PAYLOAD__;
var layersByField = {};
var selected = null;

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function popupHtml(p) {
  var rows = [
    ["Grower", p.grower],
    ["Farm", p.farm],
    ["Field", p.field],
    ["Area", p.acres ? p.acres.toLocaleString() + " ac" : ""],
    ["Location", [p.county, p.state].filter(Boolean).join(", ")],
    ["Source", p.source]
  ];
  var html = '<div class="popup"><h3>' + esc(p.field) + "</h3><table>";
  rows.forEach(function (row) {
    if (!row[1]) { return; }
    html += "<tr><th>" + esc(row[0]) + "</th><td>" + esc(row[1]) + "</td></tr>";
  });
  html += "</table>";
  if (p.rotation) {
    html += '<div class="rot"><b>Rotation ' + esc(p.rotation_years) + "</b><br />" + esc(p.rotation);
    if (p.next_crop) { html += "<br /><b>Next:</b> " + esc(p.next_crop); }
    if (p.rotation_confidence) { html += " (" + esc(p.rotation_confidence) + " confidence)"; }
    html += "</div>";
  }
  return html + "</div>";
}

var map = L.map("map", { zoomControl: true, scrollWheelZoom: true })
  .setView(__CENTER__, 4);

var imagery = L.tileLayer("__ESRI_TILES__", {
  maxZoom: 19,
  attribution: "__ESRI_ATTRIBUTION__"
});
var streets = L.tileLayer("__OSM_TILES__", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
});
imagery.addTo(map);

L.control.layers({ "Satellite (Esri World Imagery)": imagery, "Streets (OpenStreetMap)": streets }, null, { collapsed: false }).addTo(map);
L.control.scale({ imperial: true, metric: true }).addTo(map);

function styleFeature(feature) {
  return {
    color: feature.properties.color,
    weight: 2,
    opacity: 0.95,
    fillColor: feature.properties.color,
    fillOpacity: 0.22
  };
}

function highlight(layer, on) {
  layer.setStyle({ weight: on ? 3.5 : 2, fillOpacity: on ? 0.42 : 0.22 });
}

var fieldLayer = L.geoJSON(payload, {
  style: styleFeature,
  onEachFeature: function (feature, layer) {
    var p = feature.properties;
    layersByField[p.field_id] = layer;
    layer.bindPopup(popupHtml(p), { maxWidth: 340 });
    layer.bindTooltip(esc(p.field), { sticky: true, direction: "top" });
    layer.on({
      mouseover: function () { highlight(layer, true); },
      mouseout: function () { if (selected !== layer) { highlight(layer, false); } },
      click: function () { selected = layer; },
      popupclose: function () {
        if (selected === layer) { selected = null; highlight(layer, false); }
      }
    });
  }
}).addTo(map);

function fitAll() {
  var bounds = fieldLayer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 });
  }
}
fitAll();

function buildList() {
  var nav = document.getElementById("fields");
  var groups = {};
  payload.features.forEach(function (feature) {
    var p = feature.properties;
    if (!groups[p.farm_slug]) {
      groups[p.farm_slug] = { name: p.farm, color: p.color, fields: [] };
    }
    groups[p.farm_slug].fields.push(p);
  });
  var html = "";
  Object.keys(groups).forEach(function (slug) {
    var group = groups[slug];
    html += '<div class="farm-group"><div class="farm-head"><span class="swatch" style="background:' +
      esc(group.color) + '"></span>' + esc(group.name) + "</div>";
    group.fields
      .sort(function (a, b) { return String(a.field).localeCompare(String(b.field)); })
      .forEach(function (p) {
        html += '<button type="button" class="field-item" data-field-id="' + esc(p.field_id) + '"><span>' +
          esc(p.field) + '</span><span class="acres">' +
          (p.acres ? p.acres.toLocaleString() + " ac" : "") + "</span></button>";
      });
    html += "</div>";
  });
  nav.innerHTML = html;
  Array.prototype.forEach.call(nav.querySelectorAll(".field-item"), function (button) {
    button.addEventListener("click", function () {
      var layer = layersByField[button.getAttribute("data-field-id")];
      if (!layer) { return; }
      selected = layer;
      map.fitBounds(layer.getBounds(), { padding: [60, 60], maxZoom: 16 });
      layer.openPopup();
    });
  });
}
buildList();

document.getElementById("fit-all").addEventListener("click", fitAll);
document.getElementById("toggle-fields").addEventListener("click", function () {
  var nav = document.getElementById("fields");
  var collapsed = nav.style.display === "none";
  nav.style.display = collapsed ? "" : "none";
  this.textContent = collapsed ? "Hide list" : "Show list";
  this.setAttribute("aria-expanded", collapsed ? "true" : "false");
});
</script>
</body>
</html>
"""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grower-slug",
        action="append",
        default=None,
        help="Grower to render; repeat for several. Default: every grower with farm boundaries.",
    )
    parser.add_argument("--title", default=None, help="Override the page title.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path. Only valid with a single --grower-slug.",
    )
    parser.add_argument(
        "--no-rotation",
        action="store_true",
        help="Omit crop-rotation summaries from popups.",
    )
    return parser.parse_args(argv)


def discover_growers() -> list[str]:
    if not GROWERS_ROOT.is_dir():
        return []
    return sorted(
        path.name
        for path in GROWERS_ROOT.iterdir()
        if path.is_dir() and (path / "farms").is_dir()
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    grower_slugs = args.grower_slug or discover_growers()
    if not grower_slugs:
        print("No growers found under the runtime data root.", file=sys.stderr)
        return 1
    if args.output and len(grower_slugs) != 1:
        print("--output requires exactly one --grower-slug.", file=sys.stderr)
        return 2

    failures = 0
    for slug in grower_slugs:
        print(f"grower {slug}")
        payload = build_payload(
            slug, include_rotation=not args.no_rotation
        )
        if payload is None:
            print(f"  skip  {slug}: no field boundaries on any farm", file=sys.stderr)
            failures += 1
            continue
        target = Path(args.output).expanduser() if args.output else grower_map_path(slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_html(payload, title=args.title), encoding="utf-8")
        size_kb = target.stat().st_size / 1024
        print(
            f"  wrote {target}  ({payload['field_count']} fields, "
            f"{payload['acres']:.0f} ac, {size_kb:.0f} KB)"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
