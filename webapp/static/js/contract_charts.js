/* Contract page charts: OHLC candlestick + volume (lightweight-charts) and a
   canvas-drawn daily return distribution with +/-1, 2, 2.5 sigma markers. */

function toChartTime(msEpoch) {
    return Math.floor(msEpoch / 1000);
}

function sortByTime(priceHistory) {
    return [...priceHistory].sort((a, b) => a.t - b.t);
}

function buildCandleSeriesData(sortedHistory) {
    return sortedHistory.map((bar) => ({
        time: toChartTime(bar.t),
        open: bar.o,
        high: bar.h,
        low: bar.l,
        close: bar.c,
    }));
}

function buildVolumeSeriesData(sortedHistory) {
    return sortedHistory.map((bar, index) => {
        const prevClose = index > 0 ? sortedHistory[index - 1].c : bar.o;
        return {
            time: toChartTime(bar.t),
            value: bar.v,
            color: bar.c >= prevClose ? 'rgba(22, 163, 74, 0.55)' : 'rgba(220, 38, 38, 0.55)',
        };
    });
}

function initPriceChart(containerId, sortedHistory) {
    const container = document.getElementById(containerId);
    if (!container || typeof LightweightCharts === 'undefined' || !sortedHistory.length) {
        return;
    }

    const chart = LightweightCharts.createChart(container, {
        height: 440,
        layout: {
            background: { color: 'transparent' },
            textColor: '#6b7280',
        },
        grid: {
            vertLines: { color: '#e1e4e8' },
            horzLines: { color: '#e1e4e8' },
        },
        rightPriceScale: {
            borderColor: '#e1e4e8',
        },
        timeScale: {
            borderColor: '#e1e4e8',
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });

    const candleSeries = chart.addCandlestickSeries({
        upColor: '#16a34a',
        downColor: '#dc2626',
        borderUpColor: '#16a34a',
        borderDownColor: '#dc2626',
        wickUpColor: '#16a34a',
        wickDownColor: '#dc2626',
        priceScaleId: 'right',
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.3 } });
    candleSeries.setData(buildCandleSeriesData(sortedHistory));

    const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });
    volumeSeries.setData(buildVolumeSeriesData(sortedHistory));

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver((entries) => {
        const width = entries[0].contentRect.width;
        if (width > 0) {
            chart.applyOptions({ width });
        }
    });
    resizeObserver.observe(container);
}

function computeDailyReturns(sortedHistory) {
    const returns = [];
    for (let i = 1; i < sortedHistory.length; i++) {
        const prevClose = sortedHistory[i - 1].c;
        const close = sortedHistory[i].c;
        if (prevClose > 0) {
            returns.push((close - prevClose) / prevClose);
        }
    }
    return returns;
}

function mean(values) {
    return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function stdDev(values, avg) {
    if (values.length < 2) return 0;
    const variance = values.reduce((sum, v) => sum + (v - avg) ** 2, 0) / (values.length - 1);
    return Math.sqrt(variance);
}

function renderReturnDistribution(canvasId, statsContainerId, sortedHistory) {
    const canvas = document.getElementById(canvasId);
    const statsContainer = document.getElementById(statsContainerId);
    if (!canvas || sortedHistory.length < 3) return;

    const returns = computeDailyReturns(sortedHistory);
    const lastClose = sortedHistory[sortedHistory.length - 1].c;
    const avg = mean(returns);
    const sd = stdDev(returns, avg);

    const binCount = 40;
    const min = Math.min(...returns);
    const max = Math.max(...returns);
    const binWidth = (max - min) / binCount || 1;
    const bins = new Array(binCount).fill(0);

    returns.forEach((r) => {
        let idx = Math.floor((r - min) / binWidth);
        idx = Math.min(Math.max(idx, 0), binCount - 1);
        bins[idx] += 1;
    });

    const maxBinCount = Math.max(...bins, 1);

    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.parentElement.clientWidth;
    const cssHeight = 280;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    canvas.style.width = cssWidth + 'px';
    canvas.style.height = cssHeight + 'px';

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const padding = { top: 34, right: 20, bottom: 30, left: 20 };
    const plotWidth = cssWidth - padding.left - padding.right;
    const plotHeight = cssHeight - padding.top - padding.bottom;

    const xForReturn = (r) => padding.left + ((r - min) / (max - min)) * plotWidth;
    const yForCount = (count) => padding.top + plotHeight - (count / maxBinCount) * plotHeight;

    // Histogram bars
    ctx.fillStyle = 'rgba(37, 99, 235, 0.55)';
    const barGap = 1;
    bins.forEach((count, i) => {
        const x = padding.left + i * (plotWidth / binCount);
        const barWidth = Math.max(plotWidth / binCount - barGap, 1);
        const y = yForCount(count);
        ctx.fillRect(x, y, barWidth, padding.top + plotHeight - y);
    });

    // Baseline
    ctx.strokeStyle = '#e1e4e8';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top + plotHeight);
    ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
    ctx.stroke();

    // Standard deviation markers, alternating label height to reduce overlap
    const levels = [
        { sigma: -2.5, color: '#dc2626' },
        { sigma: -2, color: '#d97706' },
        { sigma: -1, color: '#9ca3af' },
        { sigma: 1, color: '#9ca3af' },
        { sigma: 2, color: '#d97706' },
        { sigma: 2.5, color: '#dc2626' },
    ];

    ctx.font = '600 10px var(--font-mono, monospace)';
    ctx.textAlign = 'center';

    levels.forEach(({ sigma, color }, i) => {
        const returnLevel = avg + sigma * sd;
        if (returnLevel < min || returnLevel > max) return;

        const x = xForReturn(returnLevel);
        ctx.strokeStyle = color;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + plotHeight);
        ctx.stroke();
        ctx.setLineDash([]);

        const priceMark = lastClose * (1 + returnLevel);
        const labelY = i % 2 === 0 ? padding.top - 20 : padding.top - 8;
        ctx.fillStyle = color;
        ctx.fillText(`${sigma > 0 ? '+' : ''}${sigma}\u03c3`, x, labelY);
        ctx.fillText(`$${priceMark.toFixed(2)}`, x, labelY + 10);
    });

    // Mean line
    const meanX = xForReturn(avg);
    ctx.strokeStyle = '#1a1a1a';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(meanX, padding.top);
    ctx.lineTo(meanX, padding.top + plotHeight);
    ctx.stroke();

    if (statsContainer) {
        statsContainer.innerHTML = `
            <div class="dist-stat"><span class="dist-stat-label">Mean Daily Return</span><span class="dist-stat-value">${(avg * 100).toFixed(2)}%</span></div>
            <div class="dist-stat"><span class="dist-stat-label">Std Dev (&sigma;)</span><span class="dist-stat-value">${(sd * 100).toFixed(2)}%</span></div>
            <div class="dist-stat"><span class="dist-stat-label">Sample Size</span><span class="dist-stat-value">${returns.length} days</span></div>
        `;
    }
}

function initContractCharts(priceHistory) {
    if (!Array.isArray(priceHistory) || priceHistory.length < 2) return;

    const sortedHistory = sortByTime(priceHistory);

    initPriceChart('ohlc-chart', sortedHistory);
    renderReturnDistribution('return-distribution-chart', 'distribution-stats', sortedHistory);
    populateOrderPriceOptions('price', sortedHistory);

    let resizeTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            renderReturnDistribution('return-distribution-chart', 'distribution-stats', sortedHistory);
        }, 150);
    });
}

// Builds a limit-price dropdown spanning +/-2 standard deviations (based on
// daily return volatility) around the last close, in $1 increments.
function populateOrderPriceOptions(selectId, sortedHistory) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const spot = sortedHistory[sortedHistory.length - 1].c;
    const returns = computeDailyReturns(sortedHistory);
    const avg = mean(returns);
    const sd = stdDev(returns, avg);
    const priceStdDev = spot * sd;

    const step = 1;
    let low = Math.max(step, Math.floor(spot - 2 * priceStdDev));
    let high = Math.ceil(spot + 2 * priceStdDev);
    if (high <= low) {
        high = low + step;
    }

    select.innerHTML = '';
    let closestOption = null;
    let closestDiff = Infinity;

    for (let value = low; value <= high; value += step) {
        const option = document.createElement('option');
        option.value = value.toFixed(2);
        option.textContent = `$${value.toFixed(2)}`;

        const diff = Math.abs(value - spot);
        if (diff < closestDiff) {
            closestDiff = diff;
            closestOption = option;
        }

        select.appendChild(option);
    }

    if (closestOption) {
        closestOption.selected = true;
        closestOption.textContent += ' (Spot)';
    }
}
