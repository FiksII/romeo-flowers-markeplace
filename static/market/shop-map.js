"use strict";

// Maps in the partner cabinet: Yandex Tiles when configured, otherwise OpenStreetMap.
// data-shop-map="location" shows the shop marker and follows the address picked in the form;
// data-shop-map="zone" lets the seller draw the delivery polygon with Leaflet-Geoman and
// writes it as JSON [[lat, lon], ...] into the hidden delivery_zone field.
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const mapConfig = JSON.parse(document.getElementById("shop-map-config")?.textContent || "{}");
const yandexKey = mapConfig.yandexTilesKey;
const tileUrl = yandexKey ? `https://tiles.api-maps.yandex.ru/v1/tiles/?apikey=${encodeURIComponent(yandexKey)}&lang=ru_RU&l=map&projection=web_mercator&x={x}&y={y}&z={z}` : TILES;
const tileAttribution = yandexKey ? '© <a href="https://yandex.ru/maps/" target="_blank" rel="noopener">Яндекс</a>' : ATTRIBUTION;
const MOSCOW = [55.7558, 37.6173];
const ZONE_STYLE = { color: "#304b39", weight: 2, fillColor: "#304b39", fillOpacity: 0.16 };

const startMap = (element) => {
  const lat = parseFloat(element.dataset.lat), lon = parseFloat(element.dataset.lon);
  const known = Number.isFinite(lat) && Number.isFinite(lon);
  const center = known ? [lat, lon] : MOSCOW;
  const map = L.map(element, { scrollWheelZoom: false }).setView(center, known ? 14 : 10);
  map.attributionControl.setPrefix(false);
  // Send only the site origin for tile images and API key Referer restrictions.
  L.tileLayer(tileUrl, {
    maxZoom: 19,
    attribution: tileAttribution,
    referrerPolicy: "strict-origin-when-cross-origin",
  }).addTo(map);
  if (yandexKey) {
    const logo = L.control({ position: "bottomleft" });
    logo.onAdd = () => {
      const container = L.DomUtil.create("div");
      const link = document.createElement("a");
      link.href = "https://yandex.ru/maps/";
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.src = mapConfig.yandexLogo;
      image.alt = "Яндекс Карты";
      image.width = 88;
      image.height = 48;
      link.append(image);
      container.append(link);
      L.DomEvent.disableClickPropagation(container);
      return container;
    };
    logo.addTo(map);
  }
  const marker = known ? L.marker(center, { keyboard: false, title: "Магазин" }).addTo(map) : null;
  // Leaflet sizes itself at creation; fix it when the layout settles or the tab opens.
  setTimeout(() => map.invalidateSize(), 0);
  return { map, marker, center };
};

document.querySelectorAll('[data-shop-map="location"]').forEach((element) => {
  if (!window.L) return;
  const state = startMap(element);
  let marker = state.marker;
  const form = element.closest("form");
  form?.addEventListener("address:selected", (event) => {
    const point = [parseFloat(event.detail.latitude), parseFloat(event.detail.longitude)];
    if (!point.every(Number.isFinite)) return;
    if (marker) marker.setLatLng(point);
    else marker = L.marker(point, { keyboard: false, title: "Магазин" }).addTo(state.map);
    state.map.setView(point, 16);
  });
});

document.querySelectorAll('[data-shop-map="zone"]').forEach((element) => {
  if (!window.L || !L.PM) return;
  const form = element.closest("form");
  const field = form.querySelector('[name="delivery_zone"]');
  const status = form.querySelector("[data-zone-status]");
  const { map } = startMap(element);
  map.pm.setLang("ru");
  map.pm.setGlobalOptions({ pathOptions: ZONE_STYLE, templineStyle: ZONE_STYLE, hintlineStyle: { ...ZONE_STYLE, dashArray: [5, 5] } });
  map.pm.addControls({
    position: "topleft",
    drawMarker: false, drawCircleMarker: false, drawPolyline: false, drawText: false,
    drawCircle: false, drawRectangle: true, drawPolygon: true,
    editMode: true, dragMode: true, cutPolygon: false, rotateMode: false, removalMode: true,
  });
  let zone = null;
  const points = () => zone.getLatLngs()[0].map((point) => [Number(point.lat.toFixed(6)), Number(point.lng.toFixed(6))]);
  const sync = () => {
    if (!zone) {
      field.value = "";
      if (status) status.textContent = "Зона ещё не нарисована.";
      return;
    }
    field.value = JSON.stringify(points());
    if (status) status.textContent = `Зона задана: ${zone.getLatLngs()[0].length} точек. Не забудьте сохранить.`;
  };
  const adopt = (layer) => {
    if (zone && zone !== layer) map.removeLayer(zone);
    zone = layer;
    zone.setStyle(ZONE_STYLE);
    ["pm:edit", "pm:dragend", "pm:vertexadded", "pm:vertexremoved", "pm:markerdragend"].forEach((name) => zone.on(name, sync));
    zone.on("pm:remove", () => { zone = null; sync(); });
    sync();
  };
  map.on("pm:create", (event) => { if (event.shape === "Polygon" || event.shape === "Rectangle") adopt(event.layer); });
  map.on("pm:remove", (event) => { if (event.layer === zone) { zone = null; sync(); } });

  try {
    const saved = JSON.parse(field.value || "[]");
    if (Array.isArray(saved) && saved.length >= 3) {
      adopt(L.polygon(saved, ZONE_STYLE).addTo(map));
      map.fitBounds(zone.getBounds(), { padding: [24, 24] });
      status && (status.textContent = `Зона задана: ${saved.length} точек.`);
    }
  } catch (error) {
    field.value = "";
  }
});
