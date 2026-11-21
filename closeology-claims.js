/*!
 * closeology-claims.js — tiny client for the Closeology public claims API (v1).
 * No dependencies. Works in any modern browser (and Node 18+ for the data calls).
 *
 *   <script src="https://jaydeepdive.github.io/closeology/api/closeology-claims.js"></script>
 *   CloseologyClaims.getClaim("BC", "1123871").then(console.log)
 *
 * Map overlays (pass in the map library you already use):
 *   CloseologyClaims.addToMapLibre(map, { maplibregl, pmtiles })   // vector tiles (fastest)
 *   CloseologyClaims.addToLeaflet(map, L)                           // GeoJSON tiles
 *   CloseologyClaims.addToOpenLayers(map, ol)                       // GeoJSON tiles
 *   CloseologyClaims.addToGoogleMaps(map)                           // GeoJSON tiles (google.maps.Data)
 *
 * Docs: https://jaydeepdive.github.io/closeology/api/
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.CloseologyClaims = factory(root);
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  var DEFAULT_BASE = "https://jaydeepdive.github.io/closeology/";
  var TILE_DEG = 0.5;
  var VECTOR_MINZOOM = 9;        // polygons in the vector tiles start here (coverage dots below)
  var GEOJSON_MINZOOM = 9;       // GeoJSON tiles are heavier; draw polygons from here
  var MAX_TILES = 36;            // refuse to pull more GeoJSON tiles than this per view
  var COLORS = { fill: "#d4380d", line: "#a8071a" };
  var KEYMAP = { c: "id", j: "jurisdiction", o: "holder", n: "name", i: "issued", e: "expiry", a: "area_ha" };

  var S = { base: DEFAULT_BASE, jur: null, tiles: null, cov: null, shard: {}, tile: {} };

  function setBase(url) {
    S.base = /\/$/.test(url) ? url : url + "/";
    S.jur = S.tiles = S.cov = null; S.shard = {}; S.tile = {};
  }
  function apiUrl(path) { return S.base + "api/v1/" + path; }
  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
      return r.json();
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /** 32-bit FNV-1a over UTF-8 bytes — the lookup shard hash (same as the server). */
  function fnv1a32(str) {
    var bytes = new TextEncoder().encode(String(str)), h = 0x811c9dc5;
    for (var i = 0; i < bytes.length; i++) { h ^= bytes[i]; h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }

  function getManifest() { return getJSON(apiUrl("index.json")); }

  /** {list:[...], byCode:{ON:{...}}} — counts, bbox, source, field coverage, shard counts. */
  function getJurisdictions() {
    if (!S.jur) {
      S.jur = getJSON(apiUrl("jurisdictions.json")).then(function (d) {
        var by = {};
        d.jurisdictions.forEach(function (j) { by[j.code] = j; });
        return { generated: d.generated, list: d.jurisdictions, byCode: by };
      });
    }
    return S.jur;
  }

  function recordUrl(j, id) {
    return j && j.record_url_template ? j.record_url_template.replace("{id}", encodeURIComponent(id)) : "";
  }

  /** Full record for one claim, or null if it isn't an active claim today. */
  function getClaim(jurisdiction, id) {
    var jur = String(jurisdiction).toUpperCase(), cid = String(id).trim();
    return getJurisdictions().then(function (J) {
      var j = J.byCode[jur];
      if (!j) throw new Error("Unknown jurisdiction: " + jur);
      var url = apiUrl("claim/" + jur.toLowerCase() + "/" + (fnv1a32(cid) % j.lookup_shards) + ".json");
      if (!S.shard[url]) S.shard[url] = getJSON(url);
      return S.shard[url].then(function (d) {
        var r = d[cid];
        if (!r) return null;
        return {
          id: cid, jurisdiction: jur, holder: r.holder || "", name: r.name || "",
          issued: r.issued || "", expiry: r.expiry || "",
          area_ha: r.area_ha == null ? null : r.area_ha, lon: r.lon, lat: r.lat, bbox: r.bbox || null,
          tile: Math.floor(r.lon / TILE_DEG) + "_" + Math.floor(r.lat / TILE_DEG),
          record_url: recordUrl(j, cid)
        };
      });
    });
  }

  function tileIndex() {
    if (!S.tiles) S.tiles = getJSON(S.base + "claimtiles/index.json").then(function (d) { return new Set(d.tiles); });
    return S.tiles;
  }

  /** GeoJSON tile keys ("ix_iy") that exist and cover [west, south, east, north]. */
  function tileKeysForBBox(bbox) {
    var x0 = Math.floor(bbox[0] / TILE_DEG), y0 = Math.floor(bbox[1] / TILE_DEG);
    var x1 = Math.floor(bbox[2] / TILE_DEG), y1 = Math.floor(bbox[3] / TILE_DEG);
    return tileIndex().then(function (set) {
      var keys = [];
      for (var x = x0; x <= x1; x++) for (var y = y0; y <= y1; y++) if (set.has(x + "_" + y)) keys.push(x + "_" + y);
      return keys;
    });
  }

  function toApiProps(p, J) {
    var out = {};
    for (var k in KEYMAP) if (Object.prototype.hasOwnProperty.call(KEYMAP, k)) out[KEYMAP[k]] = p[k] == null ? "" : p[k];
    out.area_ha = p.a == null || p.a === "" ? null : p.a;
    out.record_url = recordUrl(J.byCode[out.jurisdiction], out.id);
    return out;
  }

  /**
   * Every claim in the GeoJSON tiles covering bbox, as a FeatureCollection with API
   * field names. Claims crossing tile edges are returned once. Rejects with
   * err.tooLarge when the box needs more than opts.maxTiles tiles (zoom in).
   */
  function getClaimsInBBox(bbox, opts) {
    opts = opts || {};
    var max = opts.maxTiles || MAX_TILES;
    return Promise.all([tileKeysForBBox(bbox), getJurisdictions()]).then(function (r) {
      var keys = r[0], J = r[1];
      if (keys.length > max) {
        var err = new Error("Area too large: " + keys.length + " tiles (max " + max + "). Zoom in.");
        err.tooLarge = true; throw err;
      }
      return Promise.all(keys.map(function (k) {
        if (!S.tile[k]) S.tile[k] = getJSON(S.base + "claimtiles/" + k + ".geojson").catch(function (e) { delete S.tile[k]; throw e; });
        return S.tile[k];
      })).then(function (tiles) {
        var seen = new Set(), feats = [];
        tiles.forEach(function (t) {
          (t.features || []).forEach(function (f) {
            var key = f.properties.j + ":" + f.properties.c;
            if (seen.has(key)) return;
            seen.add(key);
            feats.push({ type: "Feature", id: key, properties: toApiProps(f.properties, J), geometry: f.geometry });
          });
        });
        return { type: "FeatureCollection", features: feats };
      });
    });
  }

  /** Zoomed-out overview: one point per ~5 km cell with staked ground (property n = claims). */
  function getCoverage() {
    if (!S.cov) S.cov = getJSON(S.base + "claim_coverage.geojson");
    return S.cov;
  }

  function popupHTML(p, juris) {
    var j = juris && juris.byCode ? juris.byCode[p.jurisdiction] : null;
    var expLabel = j && j.expiry_kind === "anniversary" ? "Anniversary" : "Good to";
    var url = p.record_url || recordUrl(j, p.id);        // vector tiles omit it; rebuild from template
    var row = function (k, v) { return v === "" || v == null ? "" : "<tr><th style='text-align:left;padding:1px 8px 1px 0;color:#666;font-weight:500'>" + k + "</th><td>" + esc(v) + "</td></tr>"; };
    return "<div style='font:12.5px/1.45 system-ui,sans-serif;min-width:190px'>" +
      "<div style='font-weight:700;margin-bottom:4px'>Claim " + esc(p.id) + " <span style='color:#888;font-weight:500'>· " + esc(p.jurisdiction) + "</span></div>" +
      "<table style='border-collapse:collapse'>" +
      row("Holder", p.holder) + row("Name", p.name) + row("Issued", p.issued) + row(expLabel, p.expiry) +
      row("Area", p.area_ha != null && p.area_ha !== "" ? p.area_ha + " ha" : "") + "</table>" +
      (url ? "<div style='margin-top:4px'><a href='" + esc(url) + "' target='_blank' rel='noopener'>Official record ↗</a></div>" : "") +
      "</div>";
  }

  // --------------------------------------------------------------------------
  // MapLibre GL JS (vector tiles via PMTiles) — needs maplibre-gl + pmtiles
  // --------------------------------------------------------------------------
  var protocolAdded = false;
  function addToMapLibre(map, opts) {
    opts = opts || {};
    var ml = opts.maplibregl || root.maplibregl, pm = opts.pmtiles || root.pmtiles;
    if (!ml || !pm) throw new Error("addToMapLibre needs maplibregl and pmtiles");
    if (!protocolAdded) {
      // pmtiles 3.x: .tile = MapLibre v3 (callback) API, .tilev4 = MapLibre v4+ (promise) API.
      // pmtiles 4.x: .tile is already the v4+ API. Pick whichever matches this MapLibre.
      var proto = new pm.Protocol();
      var mlMajor = parseInt(String((ml.getVersion && ml.getVersion()) || ml.version || "4"), 10);
      ml.addProtocol("pmtiles", mlMajor >= 4 ? (proto.tilev4 || proto.tile) : proto.tile);
      protocolAdded = true;
    }
    var src = opts.sourceId || "closeology-claims";
    return Promise.all([getJSON(apiUrl("claims.style.json")), getJurisdictions()]).then(function (r) {
      var style = r[0], J = r[1];
      var s = style.sources["closeology-claims"];
      s.url = "pmtiles://" + apiUrl("claims.pmtiles");
      map.addSource(src, s);
      style.layers.forEach(function (l) { l.source = src; map.addLayer(l, opts.beforeId); });
      var popup = new ml.Popup({ maxWidth: "320px" });
      var onClick = function (e) {
        var f = e.features && e.features[0];
        if (f) popup.setLngLat(e.lngLat).setHTML(popupHTML(f.properties, J)).addTo(map);
      };
      var enter = function () { map.getCanvas().style.cursor = "pointer"; };
      var leave = function () { map.getCanvas().style.cursor = ""; };
      map.on("click", "closeology-claims-fill", onClick);
      map.on("mouseenter", "closeology-claims-fill", enter);
      map.on("mouseleave", "closeology-claims-fill", leave);
      return {
        layers: style.layers.map(function (l) { return l.id; }),
        remove: function () {
          map.off("click", "closeology-claims-fill", onClick);
          map.off("mouseenter", "closeology-claims-fill", enter);
          map.off("mouseleave", "closeology-claims-fill", leave);
          style.layers.forEach(function (l) { if (map.getLayer(l.id)) map.removeLayer(l.id); });
          if (map.getSource(src)) map.removeSource(src);
          popup.remove();
        }
      };
    });
  }

  // --------------------------------------------------------------------------
  // Leaflet (GeoJSON tiles)
  // --------------------------------------------------------------------------
  function addToLeaflet(map, L, opts) {
    opts = opts || {};
    L = L || root.L;
    var minZoom = opts.minZoom || GEOJSON_MINZOOM;
    var renderer = opts.renderer || L.canvas({ padding: 0.5 });
    var style = opts.style || { color: COLORS.line, weight: 1, fillColor: COLORS.fill, fillOpacity: 0.18 };
    var J = null, seq = 0, shown = new Set();
    var claims = L.geoJSON(null, {
      renderer: renderer,                       // canvas: tens of thousands of polygons stay fast
      style: function () { return style; },
      onEachFeature: function (f, l) { l.bindPopup(function () { return popupHTML(f.properties, J); }); }
    }).addTo(map);
    var coverage = L.geoJSON(null, {
      pointToLayer: function (f, ll) {
        return L.circleMarker(ll, { renderer: renderer, radius: 2.4, stroke: false, fillColor: COLORS.fill,
          fillOpacity: Math.min(0.9, 0.3 + f.properties.n / 30), interactive: false });
      }
    });
    function refresh() {
      var my = ++seq, z = map.getZoom();
      if (z < minZoom) {
        claims.clearLayers(); shown.clear();
        if (opts.coverage === false) return;
        getCoverage().then(function (d) {
          if (my !== seq) return;
          if (!coverage.getLayers().length) coverage.addData(d);
          if (!map.hasLayer(coverage)) coverage.addTo(map);
        });
        return;
      }
      if (map.hasLayer(coverage)) map.removeLayer(coverage);
      var b = map.getBounds();
      getJurisdictions().then(function (jj) { J = jj; return getClaimsInBBox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], opts); })
        .then(function (fc) {
          if (my !== seq) return;
          var add = fc.features.filter(function (f) { if (shown.has(f.id)) return false; shown.add(f.id); return true; });
          if (add.length) claims.addData({ type: "FeatureCollection", features: add });
        }).catch(function (e) { if (!e.tooLarge && root.console) console.warn("[closeology]", e.message); });
    }
    map.on("moveend", refresh);
    refresh();
    return {
      claims: claims, coverage: coverage, refresh: refresh,
      remove: function () { map.off("moveend", refresh); map.removeLayer(claims); if (map.hasLayer(coverage)) map.removeLayer(coverage); }
    };
  }

  // --------------------------------------------------------------------------
  // OpenLayers (GeoJSON tiles)
  // --------------------------------------------------------------------------
  function addToOpenLayers(map, ol, opts) {
    opts = opts || {};
    ol = ol || root.ol;
    var minZoom = opts.minZoom || GEOJSON_MINZOOM;
    var fmt = new ol.format.GeoJSON();
    var source = new ol.source.Vector({
      strategy: ol.loadingstrategy.bbox,
      loader: function (extent, resolution, projection, success, failure) {
        var z = map.getView().getZoomForResolution(resolution);
        if (z < minZoom - 1e-6) { success([]); return; }
        var ll = ol.proj.transformExtent(extent, projection, "EPSG:4326");
        getClaimsInBBox(ll, opts).then(function (fc) {
          var feats = fmt.readFeatures(fc, { featureProjection: projection }).filter(function (f) {
            return !source.getFeatureById(f.getId());
          });
          source.addFeatures(feats);
          success(feats);
        }).catch(function () { source.removeLoadedExtent(extent); failure(); });
      }
    });
    var style = new ol.style.Style({
      fill: new ol.style.Fill({ color: "rgba(212,56,13,0.18)" }),
      stroke: new ol.style.Stroke({ color: COLORS.line, width: 1 })
    });
    var claims = new ol.layer.Vector({ source: source, style: style, minZoom: minZoom - 1e-6 });
    var layers = [claims];
    if (opts.coverage !== false) {
      var covSource = new ol.source.Vector({
        loader: function (extent, resolution, projection, success, failure) {
          getCoverage().then(function (d) {
            var feats = fmt.readFeatures(d, { featureProjection: projection });
            covSource.addFeatures(feats); success(feats);
          }).catch(failure);
        }
      });
      layers.push(new ol.layer.Vector({
        source: covSource, maxZoom: minZoom - 1e-6,
        style: new ol.style.Style({ image: new ol.style.Circle({ radius: 2.4, fill: new ol.style.Fill({ color: "rgba(212,56,13,0.6)" }) }) })
      }));
    }
    layers.forEach(function (l) { map.addLayer(l); });
    return {
      claims: claims, layers: layers, source: source, popupHTML: function (f) {
        return getJurisdictions().then(function (J) { return popupHTML(f.getProperties(), J); });
      },
      remove: function () { layers.forEach(function (l) { map.removeLayer(l); }); }
    };
  }

  // --------------------------------------------------------------------------
  // Google Maps JavaScript API (GeoJSON tiles via google.maps.Data)
  // --------------------------------------------------------------------------
  function addToGoogleMaps(map, opts) {
    opts = opts || {};
    var g = (opts.google || root.google).maps;
    var minZoom = opts.minZoom || GEOJSON_MINZOOM;
    var maxCov = opts.maxCoveragePoints || 6000;
    var data = new g.Data({ map: map });
    data.setStyle(function (f) {
      if (f.getProperty("_cov")) {
        return { clickable: false, icon: { path: g.SymbolPath.CIRCLE, scale: 2.4, fillColor: COLORS.fill, fillOpacity: 0.65, strokeWeight: 0 } };
      }
      return { fillColor: COLORS.fill, fillOpacity: 0.18, strokeColor: COLORS.line, strokeWeight: 1 };
    });
    var info = new g.InfoWindow(), J = null, seq = 0, mode = null;
    data.addListener("click", function (e) {
      if (e.feature.getProperty("_cov")) return;
      var p = {}; e.feature.forEachProperty(function (v, k) { p[k] = v; });
      info.setContent(popupHTML(p, J)); info.setPosition(e.latLng); info.open({ map: map });
    });
    function clear() { data.forEach(function (f) { data.remove(f); }); }
    function refresh() {
      var my = ++seq, z = map.getZoom(), b = map.getBounds();
      if (!b) return;
      var ne = b.getNorthEast(), sw = b.getSouthWest();
      var bbox = [sw.lng(), sw.lat(), ne.lng(), ne.lat()];
      if (z < minZoom) {
        if (mode !== "cov") { clear(); mode = "cov"; }
        if (opts.coverage === false) return;
        getCoverage().then(function (d) {
          if (my !== seq) return;
          clear();
          var pts = d.features.filter(function (f) {
            var c = f.geometry.coordinates;
            return c[0] >= bbox[0] && c[0] <= bbox[2] && c[1] >= bbox[1] && c[1] <= bbox[3];
          }).slice(0, maxCov).map(function (f) { return { type: "Feature", properties: { _cov: 1, n: f.properties.n }, geometry: f.geometry }; });
          data.addGeoJson({ type: "FeatureCollection", features: pts });
        });
        return;
      }
      if (mode !== "claims") { clear(); mode = "claims"; }
      getJurisdictions().then(function (jj) { J = jj; return getClaimsInBBox(bbox, opts); }).then(function (fc) {
        if (my !== seq) return;
        var add = fc.features.filter(function (f) { return !data.getFeatureById(f.id); });
        if (add.length) data.addGeoJson({ type: "FeatureCollection", features: add });
      }).catch(function (e) { if (!e.tooLarge && root.console) console.warn("[closeology]", e.message); });
    }
    var idle = map.addListener("idle", refresh);
    refresh();
    return { data: data, refresh: refresh, remove: function () { g.event.removeListener(idle); data.setMap(null); info.close(); } };
  }

  return {
    version: "1.0.0",
    setBase: setBase,
    getManifest: getManifest,
    getJurisdictions: getJurisdictions,
    getClaim: getClaim,
    getClaimsInBBox: getClaimsInBBox,
    tileKeysForBBox: tileKeysForBBox,
    getCoverage: getCoverage,
    fnv1a32: fnv1a32,
    popupHTML: popupHTML,
    addToMapLibre: addToMapLibre,
    addToLeaflet: addToLeaflet,
    addToOpenLayers: addToOpenLayers,
    addToGoogleMaps: addToGoogleMaps
  };
});
