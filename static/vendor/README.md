# Dashboard and map libraries

Pinned browser bundles are served locally; pages make no runtime CDN requests for scripts or styles. Map tiles come from OpenStreetMap.

- Chart.js 4.5.1, `chart-4.5.1.umd.min.js`. [Distribution](https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js), [source](https://github.com/chartjs/Chart.js/tree/v4.5.1), [MIT license](https://github.com/chartjs/Chart.js/blob/v4.5.1/LICENSE.md).
- DataTables 3.1.3, `dataTables-3.1.3.min.js` and `dataTables-3.1.3.min.css`. [JavaScript distribution](https://cdn.datatables.net/3.1.3/js/dataTables.min.js), [CSS distribution](https://cdn.datatables.net/3.1.3/css/dataTables.dataTables.min.css), [MIT license](https://datatables.net/license/mit). This version works without jQuery.

- Leaflet 1.9.4, `leaflet-1.9.4/` (`leaflet.js`, `leaflet.css`, `images/`). [npm package](https://www.npmjs.com/package/leaflet/v/1.9.4), [source](https://github.com/Leaflet/Leaflet/tree/v1.9.4), BSD-2-Clause license in `leaflet-1.9.4/LICENSE`. Shows the shop on a map.
- Leaflet-Geoman Free 2.20.2, `leaflet-geoman-2.20.2/` (`leaflet-geoman.min.js`, `leaflet-geoman.css`). [npm package](https://www.npmjs.com/package/@geoman-io/leaflet-geoman-free/v/2.20.2), [source](https://github.com/geoman-io/leaflet-geoman), MIT license in `leaflet-geoman-2.20.2/LICENSE`. Draws and edits the delivery zone polygon.

The original bundle copyright headers are retained. The permission notices of Chart.js and DataTables are included in `LICENSES.txt`; Leaflet and Leaflet-Geoman carry their own `LICENSE` files. Map tiles are © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright); the attribution is shown on every map.
