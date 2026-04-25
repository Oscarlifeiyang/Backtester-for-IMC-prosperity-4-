const productSelect = document.getElementById("product-select");
const timestampSlider = document.getElementById("timestamp-slider");
const timestampLabel = document.getElementById("timestamp-label");
const dayLabel = document.getElementById("day-label");
const statsRoot = document.getElementById("stats");
const priceChart = document.getElementById("price-chart");
const pnlChart = document.getElementById("pnl-chart");
const spreadChart = document.getElementById("spread-chart");
const positionChart = document.getElementById("position-chart");
const detailGrid = document.getElementById("detail-grid");
const timestampPositionBody = document.getElementById("timestamp-position-body");
const recentTradesBody = document.getElementById("recent-trades-body");
const selectedPill = document.getElementById("selected-pill");
const sandboxStatus = document.getElementById("sandbox-status");
const sandboxLog = document.getElementById("sandbox-log");
const lambdaLog = document.getElementById("lambda-log");

const chartModal = document.getElementById("chart-modal");
const modalTitle = document.getElementById("modal-title");
const modalSubtitle = document.getElementById("modal-subtitle");
const modalChart = document.getElementById("modal-chart");
const modalChartViewport = document.getElementById("modal-chart-viewport");
const modalZoomOut = document.getElementById("modal-zoom-out");
const modalZoomIn = document.getElementById("modal-zoom-in");
const modalZoomLabel = document.getElementById("modal-zoom-label");
const modalFocusLabel = document.getElementById("modal-focus-label");
const modalWindowSize = document.getElementById("modal-window-size");
const modalReset = document.getElementById("modal-reset");
const modalDownload = document.getElementById("modal-download");

const MODAL_ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];

const state = {
  data: null,
  activityByProduct: new Map(),
  tradesByProduct: new Map(),
  positionsByProduct: new Map(),
  sandboxByTimestamp: new Map(),
  modal: {
    chartKind: null,
    zoomIndex: 0,
  },
};

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "N/A";
  }

  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function getTradeKind(trade) {
  if (trade.buyer === "SUBMISSION" && trade.seller === "SUBMISSION") {
    return "submission-cross";
  }

  if (trade.buyer === "SUBMISSION") {
    return "submission-buy";
  }

  if (trade.seller === "SUBMISSION") {
    return "submission-sell";
  }

  return "market";
}

function getTradeKindLabel(trade) {
  switch (getTradeKind(trade)) {
    case "submission-buy":
      return "Submission Buy";
    case "submission-sell":
      return "Submission Sell";
    case "submission-cross":
      return "Submission";
    default:
      return "Market";
  }
}

function formatTradeParty(party) {
  return party || "MARKET";
}

function getTradeSignedQuantity(trade) {
  if (trade.buyer === "SUBMISSION" && trade.seller === "SUBMISSION") {
    return 0;
  }

  if (trade.buyer === "SUBMISSION") {
    return Number(trade.quantity);
  }

  if (trade.seller === "SUBMISSION") {
    return -Number(trade.quantity);
  }

  return 0;
}

function renderTradeBadge(trade) {
  const kind = getTradeKind(trade);
  const label = getTradeKindLabel(trade);
  return `<span class="trade-pill trade-pill-${kind}">${escapeHtml(label)}</span>`;
}

function svgTradeMarker(trade, cx, cy, radius) {
  const kind = getTradeKind(trade);

  if (kind === "submission-buy") {
    const points = [
      `${cx.toFixed(2)},${(cy - radius - 1).toFixed(2)}`,
      `${(cx - radius).toFixed(2)},${(cy + radius * 0.8).toFixed(2)}`,
      `${(cx + radius).toFixed(2)},${(cy + radius * 0.8).toFixed(2)}`,
    ].join(" ");
    return `<polygon points="${points}" fill="rgba(29,78,216,0.78)" stroke="#1d4ed8" stroke-width="1.5" />`;
  }

  if (kind === "submission-sell") {
    const points = [
      `${(cx - radius).toFixed(2)},${(cy - radius * 0.8).toFixed(2)}`,
      `${(cx + radius).toFixed(2)},${(cy - radius * 0.8).toFixed(2)}`,
      `${cx.toFixed(2)},${(cy + radius + 1).toFixed(2)}`,
    ].join(" ");
    return `<polygon points="${points}" fill="rgba(220,38,38,0.78)" stroke="#dc2626" stroke-width="1.5" />`;
  }

  if (kind === "submission-cross") {
    const points = [
      `${cx.toFixed(2)},${(cy - radius - 1).toFixed(2)}`,
      `${(cx + radius).toFixed(2)},${cy.toFixed(2)}`,
      `${cx.toFixed(2)},${(cy + radius + 1).toFixed(2)}`,
      `${(cx - radius).toFixed(2)},${cy.toFixed(2)}`,
    ].join(" ");
    return `<polygon points="${points}" fill="rgba(180,83,9,0.72)" stroke="#b45309" stroke-width="1.5" />`;
  }

  return `<circle cx="${cx}" cy="${cy}" r="${radius}" fill="rgba(15,118,110,0.25)" stroke="rgba(15,118,110,1)" stroke-width="1.5" />`;
}

function getSelectedProduct() {
  return productSelect.value || state.data.meta.products[0];
}

function getProductRows(product) {
  return state.activityByProduct.get(product) || [];
}

function getProductTrades(product) {
  return state.tradesByProduct.get(product) || [];
}

function getProductPositions(product) {
  return state.positionsByProduct.get(product) || [];
}

function getBidAskSpread(row) {
  if (!row) {
    return null;
  }

  if (row.bid_ask_spread !== undefined && row.bid_ask_spread !== null && !Number.isNaN(row.bid_ask_spread)) {
    return Number(row.bid_ask_spread);
  }

  if (row.bid_price_1 === null || row.bid_price_1 === undefined || row.ask_price_1 === null || row.ask_price_1 === undefined) {
    return null;
  }

  return Number(row.ask_price_1) - Number(row.bid_price_1);
}

function getSelectedIndex(rows) {
  const maxIndex = Math.max(rows.length - 1, 0);
  const requested = Number(timestampSlider.value || 0);
  return Math.max(0, Math.min(maxIndex, requested));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getModalZoomScale() {
  return MODAL_ZOOM_STEPS[state.modal.zoomIndex] || MODAL_ZOOM_STEPS[0];
}

function getModalViewportCenterRatio() {
  const scrollableWidth = modalChartViewport.scrollWidth - modalChartViewport.clientWidth;
  if (scrollableWidth <= 0) {
    return 0.5;
  }

  const center = modalChartViewport.scrollLeft + modalChartViewport.clientWidth / 2;
  return clamp(center / modalChartViewport.scrollWidth, 0, 1);
}

function setModalViewportCenterRatio(ratio) {
  const scrollableWidth = modalChartViewport.scrollWidth - modalChartViewport.clientWidth;
  if (scrollableWidth <= 0) {
    modalChartViewport.scrollLeft = 0;
    return;
  }

  const target = ratio * modalChartViewport.scrollWidth - modalChartViewport.clientWidth / 2;
  modalChartViewport.scrollLeft = clamp(target, 0, scrollableWidth);
}

function centerModalOnTimestamp(rows, timestamp) {
  if (rows.length === 0) {
    modalChartViewport.scrollLeft = 0;
    return;
  }

  const firstTimestamp = rows[0].timestamp;
  const lastTimestamp = rows[rows.length - 1].timestamp;
  if (firstTimestamp === lastTimestamp) {
    setModalViewportCenterRatio(0.5);
    return;
  }

  const ratio = (timestamp - firstTimestamp) / (lastTimestamp - firstTimestamp);
  setModalViewportCenterRatio(clamp(ratio, 0, 1));
}

function sampleRows(rows, maxPoints) {
  if (rows.length <= maxPoints) {
    return rows;
  }

  const sampled = [];
  const step = Math.ceil(rows.length / maxPoints);

  for (let index = 0; index < rows.length; index += step) {
    sampled.push(rows[index]);
  }

  if (sampled[sampled.length - 1] !== rows[rows.length - 1]) {
    sampled.push(rows[rows.length - 1]);
  }

  return sampled;
}

function makeScale(domainMin, domainMax, rangeMin, rangeMax) {
  if (domainMin === domainMax) {
    const midpoint = (rangeMin + rangeMax) / 2;
    return () => midpoint;
  }

  const ratio = (rangeMax - rangeMin) / (domainMax - domainMin);
  return value => rangeMin + (value - domainMin) * ratio;
}

function svgLine(points, stroke, strokeWidth, dashArray = "") {
  if (points.length === 0) {
    return "";
  }

  const d = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point[0].toFixed(2)} ${point[1].toFixed(2)}`)
    .join(" ");
  const dash = dashArray ? ` stroke-dasharray="${dashArray}"` : "";
  return `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="${strokeWidth}" stroke-linejoin="round" stroke-linecap="round"${dash} />`;
}

function buildChartMarkup(rows, trades, options) {
  const width = options.width;
  const height = options.height;
  const margin = options.margin || { top: 24, right: 22, bottom: 42, left: 70 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;

  if (rows.length === 0) {
    return `<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-size="18">No chart data</text>`;
  }

  const sampledRows = sampleRows(rows, options.maxPoints || 1600);
  const timestamps = rows.map(row => row.timestamp);
  const allY = [];

  for (const row of sampledRows) {
    for (const entry of options.series) {
      const value = entry.accessor(row);
      if (value !== null && value !== undefined && !Number.isNaN(value)) {
        allY.push(Number(value));
      }
    }
  }

  if (options.showTrades) {
    for (const trade of trades) {
      allY.push(Number(trade.price));
    }
  }

  if (allY.length === 0) {
    return `<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-size="18">No numeric values available</text>`;
  }

  const minX = Math.min(...timestamps);
  const maxX = Math.max(...timestamps);
  const minY = Math.min(...allY);
  const maxY = Math.max(...allY);
  const padding = Math.max((maxY - minY) * 0.08, 1);

  const xScale = makeScale(minX, maxX, margin.left, margin.left + chartWidth);
  const yScale = makeScale(minY - padding, maxY + padding, margin.top + chartHeight, margin.top);

  const dayStarts = [];
  for (let index = 0; index < rows.length; index += 1) {
    if (index === 0 || rows[index].day !== rows[index - 1].day) {
      dayStarts.push(rows[index]);
    }
  }

  let markup = `<rect x="0" y="0" width="${width}" height="${height}" rx="18" fill="transparent" />`;

  for (let step = 0; step <= 4; step += 1) {
    const yValue = minY - padding + ((maxY - minY + 2 * padding) * step) / 4;
    const y = yScale(yValue);
    markup += `<line x1="${margin.left}" y1="${y}" x2="${margin.left + chartWidth}" y2="${y}" stroke="rgba(15,23,42,0.08)" />`;
    markup += `<text x="${margin.left - 12}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="12">${formatNumber(yValue, 0)}</text>`;
  }

  markup += `<line x1="${margin.left}" y1="${margin.top + chartHeight}" x2="${margin.left + chartWidth}" y2="${margin.top + chartHeight}" stroke="rgba(15,23,42,0.25)" />`;
  markup += `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + chartHeight}" stroke="rgba(15,23,42,0.25)" />`;

  for (const row of dayStarts) {
    const x = xScale(row.timestamp);
    markup += `<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${margin.top + chartHeight}" stroke="rgba(180,83,9,0.25)" stroke-dasharray="5 5" />`;
    markup += `<text x="${x + 6}" y="${margin.top + 14}" fill="#92400e" font-size="12">Day ${row.day}</text>`;
  }

  for (const entry of options.series) {
    const points = sampledRows
      .map(row => {
        const yValue = entry.accessor(row);
        if (yValue === null || yValue === undefined || Number.isNaN(yValue)) {
          return null;
        }
        return [xScale(row.timestamp), yScale(Number(yValue))];
      })
      .filter(Boolean);

    markup += svgLine(points, entry.color, entry.strokeWidth || 2, entry.dashArray || "");
  }

  if (options.showTrades) {
    const visibleTrades = trades.filter(trade => trade.timestamp >= minX && trade.timestamp <= maxX);
    const sampledTrades = sampleRows(visibleTrades, options.maxTradePoints || 900);
    for (const trade of sampledTrades) {
      const cx = xScale(trade.timestamp);
      const cy = yScale(trade.price);
      const radius = Math.max(3, Math.min(10, 2 + trade.quantity / 2));
      markup += svgTradeMarker(trade, cx, cy, radius);
    }
  }

  const selectedTimestamp = Math.min(maxX, Math.max(minX, options.selectedTimestamp));
  const cursorX = xScale(selectedTimestamp);
  markup += `<line x1="${cursorX}" y1="${margin.top}" x2="${cursorX}" y2="${margin.top + chartHeight}" stroke="#b45309" stroke-width="2" />`;

  const selectedRow = rows.find(row => row.timestamp === options.selectedTimestamp);
  if (selectedRow) {
    const selectedValue = options.selectedAccessor(selectedRow);
    if (selectedValue !== null && selectedValue !== undefined && !Number.isNaN(selectedValue)) {
      markup += `<circle cx="${cursorX}" cy="${yScale(Number(selectedValue))}" r="5" fill="#b45309" />`;
    }
  }

  markup += `<text x="${margin.left + chartWidth}" y="${height - 12}" text-anchor="end" fill="#64748b" font-size="12">timestamp</text>`;
  return markup;
}

function renderLineChart(svg, rows, trades, options) {
  svg.innerHTML = buildChartMarkup(rows, trades, options);
}

function renderStats(product, rows) {
  const selectedRow = rows[getSelectedIndex(rows)];
  const productTrades = getProductTrades(product);
  const productPositions = getProductPositions(product);
  const warningCount = state.data.sandbox_logs.filter(row => row.sandboxLog).length;
  const lastPnl = rows.length > 0 ? rows[rows.length - 1].profit_and_loss : 0;
  const lastPosition = productPositions.length > 0 ? productPositions[productPositions.length - 1].position : 0;
  const latestSpread = rows.length > 0 ? getBidAskSpread(rows[rows.length - 1]) : null;

  const stats = [
    ["Days", state.data.meta.days.join(", ")],
    ["Products", state.data.meta.products.length],
    ["Timestamps", state.data.meta.timestamp_count.toLocaleString()],
    ["Trades", productTrades.length.toLocaleString()],
    ["Latest Spread", formatNumber(latestSpread, 1)],
    ["Final Position", formatNumber(lastPosition, 0)],
    ["Final PnL", formatNumber(lastPnl, 1)],
    ["Warnings", warningCount.toLocaleString()],
  ];

  statsRoot.innerHTML = stats
    .map(([label, value]) => `
      <article class="stat">
        <h2>${escapeHtml(label)}</h2>
        <strong>${escapeHtml(value)}</strong>
      </article>
    `)
    .join("");

  if (selectedRow) {
    timestampLabel.textContent = `Timestamp ${selectedRow.timestamp.toLocaleString()}`;
    dayLabel.textContent = `Day ${selectedRow.day}`;
  }
}

function renderDetails(product, rows) {
  const row = rows[getSelectedIndex(rows)];
  if (!row) {
    detailGrid.innerHTML = `<div class="empty">No activity rows for this product.</div>`;
    timestampPositionBody.innerHTML = "";
    sandboxLog.textContent = "";
    lambdaLog.textContent = "";
    return;
  }

  selectedPill.textContent = `${product} @ ${row.timestamp}`;
  const tradesAtTimestamp = getProductTrades(product).filter(trade => trade.timestamp === row.timestamp);
  const positionRow = getProductPositions(product).find(position => position.timestamp === row.timestamp) || {
    timestamp: row.timestamp,
    position: 0,
    delta: 0,
    buyQuantity: 0,
    sellQuantity: 0,
  };

  const cards = [
    ["Best bid", row.bid_price_1, 0],
    ["Best ask", row.ask_price_1, 0],
    ["Bid ask spread", getBidAskSpread(row), 1],
    ["Mid price", row.mid_price, 1],
    ["Position", positionRow.position, 0],
    ["PnL", row.profit_and_loss, 1],
    ["Trade count", tradesAtTimestamp.length, 0],
    ["Submission delta", positionRow.delta, 0],
  ];

  detailGrid.innerHTML = cards
    .map(([label, value, digits]) => `
      <div class="detail-card">
        <h3>${escapeHtml(label)}</h3>
        <strong>${escapeHtml(formatNumber(value, digits))}</strong>
      </div>
    `)
    .join("");

  timestampPositionBody.innerHTML = `
    <tr>
      <td>${formatNumber(positionRow.position)}</td>
      <td>${formatNumber(positionRow.delta)}</td>
      <td>${formatNumber(positionRow.buyQuantity)}</td>
      <td>${formatNumber(positionRow.sellQuantity)}</td>
    </tr>
  `;

  const sandboxRow = state.sandboxByTimestamp.get(row.timestamp);
  const sandboxText = sandboxRow ? sandboxRow.sandboxLog : "";
  const lambdaText = sandboxRow ? sandboxRow.lambdaLog : "";

  sandboxStatus.textContent = sandboxText ? "Warnings present" : "No warnings";
  sandboxStatus.className = sandboxText ? "pill danger" : "pill warn";
  sandboxLog.textContent = sandboxText || "No sandbox warnings at this timestamp.";
  lambdaLog.textContent = lambdaText || "No trader output captured at this timestamp.";
}

function renderRecentTrades(product) {
  const rows = getProductTrades(product).slice(-25).reverse();
  if (rows.length === 0) {
    recentTradesBody.innerHTML = `<tr><td colspan="6" class="muted">No trades recorded for this product.</td></tr>`;
    return;
  }

  recentTradesBody.innerHTML = rows
    .map(trade => `
      <tr>
        <td>${formatNumber(trade.timestamp)}</td>
        <td>${formatNumber(trade.price)}</td>
        <td>${formatNumber(trade.quantity)}</td>
        <td>${renderTradeBadge(trade)}</td>
        <td>${escapeHtml(formatTradeParty(trade.buyer))}</td>
        <td>${escapeHtml(formatTradeParty(trade.seller))}</td>
      </tr>
    `)
    .join("");
}

function getMainChartConfig(product, rows, selectedTimestamp) {
  const positionRows = getProductPositions(product);
  return {
    price: {
      rows,
      trades: getProductTrades(product),
      options: {
        width: 1100,
        height: 380,
        maxPoints: 1600,
        maxTradePoints: 900,
        showTrades: true,
        selectedTimestamp,
        selectedAccessor: row => row.mid_price,
        series: [
          { accessor: row => row.bid_price_1, color: "#1d4ed8", strokeWidth: 2 },
          { accessor: row => row.ask_price_1, color: "#dc2626", strokeWidth: 2 },
          { accessor: row => row.mid_price, color: "#111827", strokeWidth: 2.5 },
        ],
      },
    },
    pnl: {
      rows,
      trades: [],
      options: {
        width: 1100,
        height: 280,
        maxPoints: 1600,
        showTrades: false,
        selectedTimestamp,
        selectedAccessor: row => row.profit_and_loss,
        series: [
          { accessor: row => row.profit_and_loss, color: "#0f766e", strokeWidth: 2.5 },
        ],
      },
    },
    spread: {
      rows,
      trades: [],
      options: {
        width: 1100,
        height: 280,
        maxPoints: 1600,
        showTrades: false,
        selectedTimestamp,
        selectedAccessor: row => getBidAskSpread(row),
        series: [
          { accessor: row => getBidAskSpread(row), color: "#155e75", strokeWidth: 2.5 },
        ],
      },
    },
    position: {
      rows: positionRows,
      trades: [],
      options: {
        width: 1100,
        height: 280,
        maxPoints: 1600,
        showTrades: false,
        selectedTimestamp,
        selectedAccessor: row => row.position,
        series: [
          { accessor: row => row.position, color: "#b45309", strokeWidth: 2.5 },
          { accessor: () => 0, color: "rgba(15,23,42,0.28)", strokeWidth: 1.5, dashArray: "6 6" },
        ],
      },
    },
  };
}

function renderCharts(product, rows) {
  const selectedRow = rows[getSelectedIndex(rows)];
  const selectedTimestamp = selectedRow ? selectedRow.timestamp : 0;
  const configs = getMainChartConfig(product, rows, selectedTimestamp);

  renderLineChart(priceChart, configs.price.rows, configs.price.trades, configs.price.options);
  renderLineChart(pnlChart, configs.pnl.rows, configs.pnl.trades, configs.pnl.options);
  renderLineChart(spreadChart, configs.spread.rows, configs.spread.trades, configs.spread.options);
  renderLineChart(positionChart, configs.position.rows, configs.position.trades, configs.position.options);
}

function openChartModal(chartKind) {
  state.modal.chartKind = chartKind;
  state.modal.zoomIndex = 0;

  chartModal.classList.remove("hidden");
  chartModal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  renderModal({ centerSelectedTimestamp: true });
}

function closeChartModal() {
  chartModal.classList.add("hidden");
  chartModal.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

function renderModal(options = {}) {
  if (!state.modal.chartKind) {
    return;
  }

  const product = getSelectedProduct();
  const rows = getProductRows(product);
  if (rows.length === 0) {
    modalChart.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-size="18">No chart data</text>`;
    return;
  }

  const selectedRow = rows[getSelectedIndex(rows)];
  const selectedTimestamp = selectedRow ? selectedRow.timestamp : rows[0].timestamp;
  const preserveRatio = options.preserveCenterRatio;
  const zoomScale = getModalZoomScale();
  const baseWidth = 1400;
  const height = 560;
  const width = Math.round(baseWidth * zoomScale);

  modalZoomLabel.textContent = `${Math.round(zoomScale * 100)}%`;
  modalFocusLabel.textContent = `Centered near timestamp ${selectedTimestamp.toLocaleString()}`;
  modalWindowSize.textContent = `${rows.length.toLocaleString()} points`;
  modalZoomOut.disabled = state.modal.zoomIndex === 0;
  modalZoomIn.disabled = state.modal.zoomIndex === MODAL_ZOOM_STEPS.length - 1;
  modalChart.setAttribute("viewBox", `0 0 ${width} ${height}`);
  modalChart.style.width = `${width}px`;
  modalChart.style.minWidth = `${width}px`;

  if (state.modal.chartKind === "price") {
    modalTitle.textContent = `${product} Price And Trade Chart`;
    modalSubtitle.textContent = "Expanded price chart. Zoom in and pan sideways to inspect local moves more clearly.";
    renderLineChart(modalChart, rows, getProductTrades(product), {
      width,
      height,
      maxPoints: Math.max(2400, Math.round(width * 2)),
      maxTradePoints: Math.max(1400, Math.round(width)),
      showTrades: true,
      selectedTimestamp,
      selectedAccessor: row => row.mid_price,
      series: [
        { accessor: row => row.bid_price_1, color: "#1d4ed8", strokeWidth: 2 },
        { accessor: row => row.ask_price_1, color: "#dc2626", strokeWidth: 2 },
        { accessor: row => row.mid_price, color: "#111827", strokeWidth: 2.5 },
      ],
    });
  } else if (state.modal.chartKind === "pnl") {
    modalTitle.textContent = `${product} Profit And Loss`;
    modalSubtitle.textContent = "Expanded profit-and-loss chart. Zoom and pan across the full timeline.";
    renderLineChart(modalChart, rows, [], {
      width,
      height,
      maxPoints: Math.max(2400, Math.round(width * 2)),
      showTrades: false,
      selectedTimestamp,
      selectedAccessor: row => row.profit_and_loss,
      series: [
        { accessor: row => row.profit_and_loss, color: "#0f766e", strokeWidth: 2.5 },
      ],
    });
  } else if (state.modal.chartKind === "spread") {
    modalTitle.textContent = `${product} Bid Ask Spread`;
    modalSubtitle.textContent = "Expanded bid-ask spread chart using best ask minus best bid at each timestamp.";
    renderLineChart(modalChart, rows, [], {
      width,
      height,
      maxPoints: Math.max(2400, Math.round(width * 2)),
      showTrades: false,
      selectedTimestamp,
      selectedAccessor: row => getBidAskSpread(row),
      series: [
        { accessor: row => getBidAskSpread(row), color: "#155e75", strokeWidth: 2.5 },
      ],
    });
  } else {
    const positionRows = getProductPositions(product);
    modalTitle.textContent = `${product} Position`;
    modalSubtitle.textContent = "Expanded position chart derived from SUBMISSION fills. Zoom and pan across the full timeline.";
    renderLineChart(modalChart, positionRows, [], {
      width,
      height,
      maxPoints: Math.max(2400, Math.round(width * 2)),
      showTrades: false,
      selectedTimestamp,
      selectedAccessor: row => row.position,
      series: [
        { accessor: row => row.position, color: "#b45309", strokeWidth: 2.5 },
        { accessor: () => 0, color: "rgba(15,23,42,0.28)", strokeWidth: 1.5, dashArray: "6 6" },
      ],
    });
  }

  requestAnimationFrame(() => {
    if (options.centerSelectedTimestamp) {
      centerModalOnTimestamp(rows, selectedTimestamp);
    } else if (preserveRatio !== undefined) {
      setModalViewportCenterRatio(preserveRatio);
    }
  });
}

function resetModalWindow() {
  state.modal.zoomIndex = 0;
  if (!chartModal.classList.contains("hidden")) {
    renderModal({ centerSelectedTimestamp: true });
  }
}

function changeModalZoom(delta) {
  const nextZoomIndex = clamp(state.modal.zoomIndex + delta, 0, MODAL_ZOOM_STEPS.length - 1);
  if (nextZoomIndex === state.modal.zoomIndex) {
    return;
  }

  const preserveCenterRatio = getModalViewportCenterRatio();
  state.modal.zoomIndex = nextZoomIndex;
  renderModal({ preserveCenterRatio });
}

function downloadModalSvg() {
  const blob = new Blob([modalChart.outerHTML], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const product = getSelectedProduct().toLowerCase();
  const kind = state.modal.chartKind || "chart";
  const link = document.createElement("a");

  link.href = url;
  link.download = `${product}-${kind}.svg`;
  link.click();

  URL.revokeObjectURL(url);
}

function render() {
  const product = getSelectedProduct();
  const rows = getProductRows(product);

  timestampSlider.max = String(Math.max(rows.length - 1, 0));
  if (Number(timestampSlider.value) > rows.length - 1) {
    timestampSlider.value = String(Math.max(rows.length - 1, 0));
  }

  renderStats(product, rows);
  renderDetails(product, rows);
  renderRecentTrades(product);
  renderCharts(product, rows);

  if (!chartModal.classList.contains("hidden")) {
    renderModal({ preserveCenterRatio: getModalViewportCenterRatio() });
  }
}

function indexData(data) {
  state.activityByProduct = new Map();
  state.tradesByProduct = new Map();
  state.positionsByProduct = new Map();
  state.sandboxByTimestamp = new Map();

  for (const row of data.activity_logs) {
    if (!state.activityByProduct.has(row.product)) {
      state.activityByProduct.set(row.product, []);
    }
    state.activityByProduct.get(row.product).push(row);
  }

  for (const trade of data.trades) {
    if (!state.tradesByProduct.has(trade.symbol)) {
      state.tradesByProduct.set(trade.symbol, []);
    }
    state.tradesByProduct.get(trade.symbol).push(trade);
  }

  for (const [product, rows] of state.activityByProduct.entries()) {
    const trades = getProductTrades(product);
    const deltasByTimestamp = new Map();

    for (const trade of trades) {
      const signedQuantity = getTradeSignedQuantity(trade);
      if (!deltasByTimestamp.has(trade.timestamp)) {
        deltasByTimestamp.set(trade.timestamp, { delta: 0, buyQuantity: 0, sellQuantity: 0 });
      }

      const entry = deltasByTimestamp.get(trade.timestamp);
      entry.delta += signedQuantity;
      if (signedQuantity > 0) {
        entry.buyQuantity += signedQuantity;
      } else if (signedQuantity < 0) {
        entry.sellQuantity += Math.abs(signedQuantity);
      }
    }

    let runningPosition = 0;
    let previousDay = null;
    const positionRows = rows.map(row => {
      // Merged backtests concatenate separate days; each day starts at inventory zero.
      if (previousDay !== null && row.day !== previousDay) {
        runningPosition = 0;
      }
      previousDay = row.day;

      const deltaEntry = deltasByTimestamp.get(row.timestamp) || { delta: 0, buyQuantity: 0, sellQuantity: 0 };
      runningPosition += deltaEntry.delta;
      return {
        day: row.day,
        timestamp: row.timestamp,
        position: runningPosition,
        delta: deltaEntry.delta,
        buyQuantity: deltaEntry.buyQuantity,
        sellQuantity: deltaEntry.sellQuantity,
      };
    });

    state.positionsByProduct.set(product, positionRows);
  }

  for (const row of data.sandbox_logs) {
    state.sandboxByTimestamp.set(row.timestamp, row);
  }
}

function installEventHandlers() {
  productSelect.addEventListener("change", () => {
    timestampSlider.value = "0";
    resetModalWindow();
    closeChartModal();
    render();
  });

  timestampSlider.addEventListener("input", render);

  modalZoomOut.addEventListener("click", () => changeModalZoom(-1));
  modalZoomIn.addEventListener("click", () => changeModalZoom(1));
  modalReset.addEventListener("click", resetModalWindow);
  modalDownload.addEventListener("click", downloadModalSvg);

  modalChartViewport.addEventListener("wheel", event => {
    if (chartModal.classList.contains("hidden")) {
      return;
    }

    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      changeModalZoom(event.deltaY < 0 ? 1 : -1);
      return;
    }

    const horizontalDelta = Math.abs(event.deltaX) > 0 ? event.deltaX : event.deltaY;
    if (horizontalDelta !== 0 && modalChartViewport.scrollWidth > modalChartViewport.clientWidth) {
      event.preventDefault();
      modalChartViewport.scrollLeft += horizontalDelta;
    }
  }, { passive: false });

  for (const button of document.querySelectorAll("[data-chart-kind]")) {
    button.addEventListener("click", event => {
      openChartModal(event.currentTarget.dataset.chartKind);
    });
  }

  for (const closable of document.querySelectorAll("[data-close-modal]")) {
    closable.addEventListener("click", closeChartModal);
  }

  document.addEventListener("keydown", event => {
    if (chartModal.classList.contains("hidden")) {
      return;
    }

    if (event.key === "Escape") {
      closeChartModal();
      return;
    }

    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      changeModalZoom(1);
      return;
    }

    if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      changeModalZoom(-1);
      return;
    }

    if (event.key === "0") {
      event.preventDefault();
      resetModalWindow();
      return;
    }

    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      modalChartViewport.scrollLeft += direction * Math.max(160, modalChartViewport.clientWidth * 0.2);
    }
  });
}

async function shutdownServer() {
  try {
    await fetch("/__shutdown", { method: "POST", keepalive: true });
  } catch {
    // The page is already loaded; the server can disappear without affecting the UI.
  }
}

async function init() {
  const response = await fetch("/data.json");
  state.data = await response.json();

  indexData(state.data);

  productSelect.innerHTML = state.data.meta.products
    .map(product => `<option value="${escapeHtml(product)}">${escapeHtml(product)}</option>`)
    .join("");

  installEventHandlers();
  render();
  shutdownServer();
}

init().catch(error => {
  statsRoot.innerHTML = `
    <article class="stat">
      <h2>Error</h2>
      <strong>Failed to load visualizer data</strong>
    </article>
  `;
  sandboxLog.textContent = String(error);
});
