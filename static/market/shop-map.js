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
  const editor = element.closest("[data-zone-editor]") || element.closest("form");
  const form = element.closest("form");
  const field = editor.querySelector('[name="delivery_zone"]');
  const status = editor.querySelector("[data-zone-status]");
  if (!window.L || !L.PM) {
    if (status) status.textContent = "Карта не загрузилась. Обновите страницу, чтобы настроить зону.";
    return;
  }
  const state = startMap(element), { map } = state;
  let marker = state.marker;
  const zones = L.featureGroup().addTo(map);
  const pending = new Set();
  let areas = [], version = "";
  const picker = editor.querySelector("[data-area-picker]");
  const list = picker?.querySelector("[data-area-list]");
  const selected = picker?.querySelector("[data-area-selected]");
  const loading = picker?.querySelector("[data-area-loading]");
  const search = picker?.querySelector("[data-area-search]");
  const region = picker?.querySelector("[data-area-region]");
  const areaLayers = (id) => zones.getLayers().filter((layer) => layer.feature?.properties?.area_id === id);
  const normal = (text) => text.toLocaleLowerCase("ru").replaceAll("ё", "е");
  const syncChoices = () => {
    list?.querySelectorAll("input[data-area-id]").forEach((input) => {
      input.checked = areaLayers(input.dataset.areaId).length > 0;
      input.disabled = pending.has(input.dataset.areaId);
    });
    if (!selected) return;
    selected.replaceChildren();
    const seen = new Set();
    zones.eachLayer((layer) => {
      const props = layer.feature?.properties || {};
      const id = props.area_id;
      if (!id || seen.has(id)) return;
      seen.add(id);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = (props.name || "Территория") + " ×";
      button.setAttribute("aria-label", "Удалить: " + (props.name || "территория"));
      button.addEventListener("click", () => { areaLayers(id).forEach((row) => zones.removeLayer(row)); sync(); });
      selected.append(button);
    });
  };
  const sync = () => {
    const data = zones.toGeoJSON(6);
    field.value = data.features.length ? JSON.stringify(data) : "";
    if (status) status.textContent = data.features.length ? `Контуров: ${data.features.length}. Не забудьте сохранить.` : "Зона ещё не нарисована. Выберите территорию или нарисуйте контур.";
    syncChoices();
  };
  const adopt = (layer, properties = {}) => {
    layer.feature = { type: "Feature", properties, geometry: layer.toGeoJSON().geometry };
    layer.setStyle(ZONE_STYLE);
    zones.addLayer(layer);
    if (properties.name) {
      const label = document.createElement("span");
      label.textContent = properties.name;
      layer.bindTooltip(label);
    }
    ["pm:edit", "pm:dragend", "pm:vertexadded", "pm:vertexremoved", "pm:markerdragend"].forEach((name) => layer.on(name, sync));
    layer.on("pm:remove", () => { zones.removeLayer(layer); sync(); });
  };
  const addFeature = (feature) => {
    L.geoJSON(feature).eachLayer((layer) => adopt(layer, feature.properties || {}));
  };
  map.pm.setLang("ru");
  map.pm.setGlobalOptions({ pathOptions: ZONE_STYLE, templineStyle: ZONE_STYLE, hintlineStyle: { ...ZONE_STYLE, dashArray: [5, 5] }, allowSelfIntersection: false });
  map.pm.addControls({
    position: "topleft", drawMarker: false, drawCircleMarker: false, drawPolyline: false, drawText: false,
    drawCircle: false, drawRectangle: true, drawPolygon: true,
    editMode: true, dragMode: true, cutPolygon: false, rotateMode: false, removalMode: true,
  });
  map.on("pm:create", (event) => {
    if (event.shape === "Polygon" || event.shape === "Rectangle") { adopt(event.layer); sync(); }
  });
  map.on("pm:remove", (event) => { zones.removeLayer(event.layer); sync(); });
  form?.addEventListener("address:selected", (event) => {
    const point = [parseFloat(event.detail.latitude), parseFloat(event.detail.longitude)];
    if (!point.every(Number.isFinite)) return;
    if (marker) marker.setLatLng(point);
    else marker = L.marker(point, { keyboard: false, title: "Магазин" }).addTo(map);
    map.setView(point, 12);
  });
  try {
    const saved = JSON.parse(field.value || "[]");
    if (Array.isArray(saved) && saved.length >= 3) adopt(L.polygon(saved, ZONE_STYLE));
    else if (saved?.type === "FeatureCollection") saved.features.forEach(addFeature);
    if (zones.getLayers().length) map.fitBounds(zones.getBounds(), { padding: [24, 24] });
    syncChoices();
    if (!zones.getLayers().length && status) status.textContent = "Зона ещё не нарисована. Выберите территорию или нарисуйте контур.";
  } catch (error) {
    if (status) status.textContent = "Не удалось показать сохранённую зону. Нарисуйте её заново.";
  }
  const toggle = async (area, checked, input) => {
    if (!checked) { areaLayers(area.id).forEach((layer) => zones.removeLayer(layer)); sync(); return; }
    if (areaLayers(area.id).length || pending.has(area.id)) return;
    pending.add(area.id);
    input.disabled = true;
    if (loading) loading.textContent = "Загрузка границ: " + area.name;
    try {
      const url = new URL(area.id + ".geojson?v=" + encodeURIComponent(version), new URL(picker.dataset.indexUrl, document.baseURI));
      const response = await fetch(url);
      if (!response.ok) throw new Error("Area unavailable");
      const feature = await response.json();
      feature.properties = { area_id: area.id, name: area.name };
      addFeature(feature);
      sync();
      map.fitBounds(zones.getBounds(), { padding: [24, 24] });
      if (loading) loading.textContent = "Территория добавлена. Границы можно изменить на карте.";
    } catch (error) {
      input.checked = false;
      if (loading) loading.textContent = "Не удалось загрузить границы. Попробуйте выбрать территорию ещё раз или нарисуйте её на карте.";
    } finally {
      pending.delete(area.id);
      input.disabled = false;
      syncChoices();
    }
  };
  const render = () => {
    if (!list) return;
    list.replaceChildren();
    const query = normal(search.value.trim());
    const matches = areas.filter((area) => (!region.value || region.value === area.kind) && normal(area.name + " " + (area.aliases || "")).includes(query));
    matches.forEach((area) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.areaId = area.id;
      input.checked = areaLayers(area.id).length > 0;
      input.disabled = pending.has(area.id);
      input.addEventListener("change", () => toggle(area, input.checked, input));
      const text = document.createElement("span");
      text.textContent = area.name;
      label.append(input, text);
      list.append(label);
    });
    if (!matches.length) list.textContent = "Территории не найдены. Можно нарисовать свою зону на карте.";
  };
  search?.addEventListener("input", render);
  region?.addEventListener("change", render);
  if (picker) fetch(picker.dataset.indexUrl).then((response) => {
    if (!response.ok) throw new Error("Index unavailable");
    return response.json();
  }).then((data) => {
    areas = data.areas; version = data.version;
    render();
    loading.textContent = `Территорий: ${areas.length}. Выберите несколько или нарисуйте свою зону.`;
  }).catch(() => { loading.textContent = "Список территорий недоступен. Нарисуйте зону на карте или обновите страницу."; });
  form?.addEventListener("submit", (event) => {
    if (pending.size) {
      event.preventDefault();
      if (status) status.textContent = "Дождитесь загрузки выбранных территорий.";
    }
  });
});
