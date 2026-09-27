/* Portfolio page: YTD performance line chart, portfolio value vs. an SPY
   benchmark rebased to the same starting dollar amount for a fair $ comparison. */

function initPerformanceChart(containerId, legendId, chartData) {
    const container = document.getElementById(containerId);
    if (!container || typeof LightweightCharts === 'undefined') return;

    const portfolioPoints = (chartData && chartData.portfolio) || [];
    const benchmarkPoints = (chartData && chartData.benchmark) || [];

    if (portfolioPoints.length < 2) {
        container.innerHTML = '<div class="empty-state">'
            + '<div class="empty-state-icon">\uD83D\uDCC8</div>'
            + '<div class="empty-state-title">Not enough history yet</div>'
            + '<p class="empty-state-text">Performance history is captured automatically each time this page is visited. Check back after a few more visits/days.</p>'
            + '</div>';
        return;
    }

    const chart = LightweightCharts.createChart(container, {
        height: 320,
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

    const portfolioSeries = chart.addLineSeries({
        color: '#d71920',
        lineWidth: 2,
    });
    portfolioSeries.setData(portfolioPoints);

    if (benchmarkPoints.length > 1) {
        const benchmarkSeries = chart.addLineSeries({
            color: '#2563eb',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
        });
        benchmarkSeries.setData(benchmarkPoints);
    }

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver((entries) => {
        const width = entries[0].contentRect.width;
        if (width > 0) {
            chart.applyOptions({ width });
        }
    });
    resizeObserver.observe(container);

    const legend = document.getElementById(legendId);
    if (legend) {
        let legendHtml = '<span class="chart-legend-item" style="color:#d71920"><span class="chart-legend-swatch"></span>Portfolio Value</span>';
        if (benchmarkPoints.length > 1) {
            legendHtml += '<span class="chart-legend-item" style="color:#2563eb"><span class="chart-legend-swatch chart-legend-swatch-dashed"></span>SPY (rebased to starting $)</span>';
        }
        legend.innerHTML = legendHtml;
    }
}
