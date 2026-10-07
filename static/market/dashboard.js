(() => {
  "use strict";

  const rubles = new Intl.NumberFormat("ru-RU", {
    style: "currency", currency: "RUB", maximumFractionDigits: 2,
  });
  const language = {
    emptyTable: "Нет данных за выбранный период",
    info: "_START_–_END_ из _TOTAL_",
    infoEmpty: "0 записей",
    infoFiltered: " (всего _MAX_)",
    lengthMenu: "Показывать _MENU_",
    loadingRecords: "Загрузка…",
    processing: "Загрузка…",
    search: "Поиск:",
    searchPlaceholder: "Номер, статус, сумма",
    zeroRecords: "По запросу ничего не найдено",
    decimal: ",",
    thousands: "\u00a0",
    paginate: { first: "Первая", last: "Последняя", next: "→", previous: "←" },
    aria: { orderable: "Сортировать столбец", orderableReverse: "Изменить порядок сортировки" },
  };

  function drawSalesChart() {
    const canvas = document.querySelector("#sales-chart");
    const source = document.querySelector("#sales-trend-data");
    if (!canvas || !source || !window.Chart) return;
    const plot = canvas.closest(".sales-chart-plot");
    try {
      const points = JSON.parse(source.textContent);
      const dates = points.map(point => point.day.split("-").reverse().join("."));
      const values = points.map(point => Number(point.value));
      if (values.some(value => !Number.isFinite(value))) return;
      const theme = getComputedStyle(document.documentElement);
      const forest = theme.getPropertyValue("--forest").trim() || "#304b39";
      const muted = theme.getPropertyValue("--muted").trim() || "#77796e";
      const border = theme.getPropertyValue("--border-soft").trim() || "#e6e6df";
      plot.hidden = false;
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: dates.map(day => day.slice(0, 5)),
          datasets: [{ label: "Оплаченные товары", data: values, backgroundColor: forest, borderRadius: 3 }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          locale: "ru-RU",
          font: { family: getComputedStyle(document.body).fontFamily },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: items => dates[items[0].dataIndex],
                label: item => rubles.format(item.parsed.y),
              },
            },
          },
          scales: {
            x: { grid: { display: false }, ticks: { color: muted, maxTicksLimit: 14, maxRotation: 0 } },
            y: { beginAtZero: true, grid: { color: border }, ticks: { color: muted, callback: value => rubles.format(value) } },
          },
        },
      });
      document.querySelector("#sales-chart-values").open = false;
    } catch {
      plot.hidden = true;
    }
  }

  function serverTable(table) {
    const tbody = table.tBodies[0];
    const fallback = tbody.innerHTML;
    const section = table.closest("[data-order-section]");
    let grid;
    let failed = false;
    // DataTables needs one cell per column; the SSR empty-state row spans columns.
    tbody.replaceChildren();
    grid = new DataTable(table, {
      language,
      serverSide: true,
      processing: true,
      searchDelay: 300,
      pageLength: 20,
      lengthMenu: [20, 50, 100],
      order: [[1, "desc"]],
      ajax: (request, callback) => {
        const url = new URL(table.dataset.ordersUrl, window.location.origin);
        url.searchParams.set("days", table.dataset.days);
        for (const key of ["draw", "start", "length"]) url.searchParams.set(key, request[key]);
        url.searchParams.set("search[value]", request.search.value);
        request.order.forEach((item, index) => {
          url.searchParams.set(`order[${index}][column]`, item.column);
          url.searchParams.set(`order[${index}][dir]`, item.dir);
        });
        fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then(response => {
            if (!response.ok) throw new Error("Table request failed");
            return response.json();
          })
          .then(data => {
            if (failed) return;
            callback(data);
            section.classList.add("orders-enhanced");
          })
          .catch(() => {
            if (failed) return;
            failed = true;
            grid.destroy();
            tbody.innerHTML = fallback;
            section.classList.remove("orders-enhanced");
            const notice = document.createElement("p");
            notice.className = "notice";
            notice.textContent = "Поиск по таблице временно недоступен. Заказы показаны обычным списком; можно обновить страницу.";
            section.prepend(notice);
          });
      },
    });
  }

  function enhanceTables() {
    if (!window.DataTable) return;
    document.querySelectorAll("table.market-table").forEach(table => {
      if (table.hasAttribute("data-server-paginated")) return;
      if (table.dataset.ordersUrl) {
        serverTable(table);
      } else if (!table.classList.contains("chart-values") && !table.querySelector("tbody [colspan]")) {
        const actions = Array.from(table.tHead.rows[0].cells)
          .map((cell, index) => !cell.textContent.trim() || cell.textContent.trim() === "Управление" ? index : -1)
          .filter(index => index >= 0);
        new DataTable(table, {
          language,
          pageLength: 20,
          lengthMenu: [20, 50, 100],
          order: [],
          columnDefs: [{ targets: actions, orderable: false }],
        });
      }
    });
  }

  drawSalesChart();
  enhanceTables();
})();
