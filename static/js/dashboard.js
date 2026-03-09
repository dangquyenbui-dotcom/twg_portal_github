/**
 * TWG Sales Dashboard — Chart rendering & year selection
 * Uses Chart.js for visualizations, vanilla JS for interactivity.
 * All data passed via window.__DASH_DATA__
 */

(function () {
    'use strict';

    var dashData = window.__DASH_DATA__ || {};
    var charts = {};

    // Month names for chart labels
    var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // ── Theme-aware colors ──
    function getColors() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        return {
            green:   isDark ? '#10B981' : '#059669',
            blue:    isDark ? '#3B82F6' : '#2563EB',
            amber:   isDark ? '#F59E0B' : '#D97706',
            purple:  isDark ? '#8B5CF6' : '#7C3AED',
            red:     isDark ? '#EF4444' : '#DC2626',
            text:    isDark ? '#8B95B0' : '#4B5563',
            textMuted: isDark ? '#5C6584' : '#9CA3AF',
            grid:    isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
            bg:      isDark ? '#111111' : '#FFFFFF',
            palette: isDark
                ? ['#3B82F6','#10B981','#F59E0B','#8B5CF6','#EF4444','#06B6D4','#EC4899','#84CC16','#F97316','#6366F1',
                   '#14B8A6','#E11D48','#A855F7','#22D3EE','#FB923C','#4ADE80','#C084FC','#FBBF24']
                : ['#2563EB','#059669','#D97706','#7C3AED','#DC2626','#0891B2','#DB2777','#65A30D','#EA580C','#4F46E5',
                   '#0D9488','#BE123C','#9333EA','#06B6D4','#F59E0B','#16A34A','#A855F7','#EAB308'],
        };
    }

    // ── Chart.js global defaults ──
    function setChartDefaults() {
        var c = getColors();
        Chart.defaults.font.family = "'DM Sans', -apple-system, sans-serif";
        Chart.defaults.color = c.text;
        Chart.defaults.borderColor = c.grid;
        Chart.defaults.plugins.legend.display = false;
        Chart.defaults.plugins.tooltip.backgroundColor = c.bg;
        Chart.defaults.plugins.tooltip.titleColor = c.text;
        Chart.defaults.plugins.tooltip.bodyColor = c.text;
        Chart.defaults.plugins.tooltip.borderColor = c.grid;
        Chart.defaults.plugins.tooltip.borderWidth = 1;
        Chart.defaults.plugins.tooltip.cornerRadius = 8;
        Chart.defaults.plugins.tooltip.padding = 10;
    }

    function fmt(n) {
        return '$' + Number(n || 0).toLocaleString('en-US');
    }

    function fmtNum(n) {
        return Number(n || 0).toLocaleString('en-US');
    }

    function fmtShort(n) {
        if (n >= 1000000) return '$' + (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return '$' + (n / 1000).toFixed(0) + 'K';
        return '$' + n;
    }

    // ══════════════════════════════════════════════════════
    // SALES BY MONTH CHART (the hero chart)
    // ══════════════════════════════════════════════════════

    function renderMonthlyChart(data) {
        var c = getColors();
        var monthly = data.monthly_totals || [];
        var canvas = document.getElementById('chartMonthly');
        if (!canvas) return;

        if (charts.monthly) charts.monthly.destroy();

        // Build labels and data for all 12 months
        var labels = [];
        var amounts = [];
        var units = [];

        for (var i = 0; i < 12; i++) {
            labels.push(MONTH_NAMES[i]);
            var found = null;
            for (var j = 0; j < monthly.length; j++) {
                if (monthly[j].mo === i + 1) {
                    found = monthly[j];
                    break;
                }
            }
            amounts.push(found ? found.amount : 0);
            units.push(found ? found.units : 0);
        }

        charts.monthly = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Sales Amount (USD)',
                    data: amounts,
                    backgroundColor: amounts.map(function(v) {
                        return v > 0 ? c.blue : 'rgba(128,128,128,0.15)';
                    }),
                    borderRadius: 6,
                    maxBarThickness: 48,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                var idx = ctx.dataIndex;
                                return [
                                    'Sales: ' + fmt(ctx.raw),
                                    'Units: ' + fmtNum(units[idx])
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { size: 12, weight: '600' } }
                    },
                    y: {
                        grid: { color: c.grid },
                        ticks: {
                            callback: function (v) { return fmtShort(v); },
                            font: { family: "'JetBrains Mono', monospace", size: 11 }
                        }
                    }
                }
            }
        });
    }

    // ══════════════════════════════════════════════════════
    // TERRITORY CHART
    // ══════════════════════════════════════════════════════

    function renderTerritoryChart(data) {
        var c = getColors();
        var items = (data.by_territory || []).slice(0, 15);
        var canvas = document.getElementById('chartTerritory');
        if (!canvas) return;

        if (charts.territory) charts.territory.destroy();

        charts.territory = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: items.map(function (i) { return i.name; }),
                datasets: [{
                    data: items.map(function (i) { return i.amount; }),
                    backgroundColor: c.palette.slice(0, items.length),
                    borderRadius: 4,
                    maxBarThickness: 28,
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: { label: function (ctx) { return fmt(ctx.raw); } }
                    }
                },
                scales: {
                    x: {
                        grid: { color: c.grid },
                        ticks: {
                            callback: function (v) { return fmtShort(v); },
                            font: { family: "'JetBrains Mono', monospace", size: 11 }
                        }
                    },
                    y: { grid: { display: false }, ticks: { font: { size: 12 } } }
                }
            }
        });
    }

    // ══════════════════════════════════════════════════════
    // PRODUCT LINE CHART
    // ══════════════════════════════════════════════════════

    function renderProductLineChart(data) {
        var c = getColors();
        var items = (data.by_product_line || []).slice(0, 12);
        var canvas = document.getElementById('chartProductLine');
        if (!canvas) return;

        if (charts.productLine) charts.productLine.destroy();

        charts.productLine = new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: items.map(function (i) { return i.name; }),
                datasets: [{
                    data: items.map(function (i) { return i.amount; }),
                    backgroundColor: c.palette.slice(0, items.length),
                    borderWidth: 2,
                    borderColor: c.bg,
                    hoverOffset: 6,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '55%',
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
                        labels: {
                            boxWidth: 10, boxHeight: 10, padding: 8,
                            font: { size: 11 },
                            usePointStyle: true, pointStyle: 'circle',
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                                var pct = total > 0 ? ((ctx.raw / total) * 100).toFixed(1) : 0;
                                return ctx.label + ': ' + fmt(ctx.raw) + ' (' + pct + '%)';
                            }
                        }
                    }
                }
            }
        });
    }

    // ══════════════════════════════════════════════════════
    // SALESMAN CHART
    // ══════════════════════════════════════════════════════

    function renderSalesmanChart(data) {
        var c = getColors();
        var items = (data.by_salesman || []).slice(0, 15);
        var canvas = document.getElementById('chartSalesman');
        if (!canvas) return;

        if (charts.salesman) charts.salesman.destroy();

        charts.salesman = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: items.map(function (i) { return i.name; }),
                datasets: [{
                    data: items.map(function (i) { return i.amount; }),
                    backgroundColor: c.blue,
                    borderRadius: 4,
                    maxBarThickness: 28,
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: { label: function (ctx) { return fmt(ctx.raw); } }
                    }
                },
                scales: {
                    x: {
                        grid: { color: c.grid },
                        ticks: {
                            callback: function (v) { return fmtShort(v); },
                            font: { family: "'JetBrains Mono', monospace", size: 11 }
                        }
                    },
                    y: { grid: { display: false }, ticks: { font: { size: 12 } } }
                }
            }
        });
    }

    // ══════════════════════════════════════════════════════
    // CUSTOMER TABLE
    // ══════════════════════════════════════════════════════

    function updateCustomerTable(data) {
        var tbody = document.getElementById('customerTableBody');
        if (!tbody) return;

        var items = data.by_customer || [];
        var html = '';

        for (var i = 0; i < items.length; i++) {
            var c = items[i];
            html += '<tr>'
                + '<td class="col-rank">' + (i + 1) + '</td>'
                + '<td class="col-name">' + escHtml(c.name) + '</td>'
                + '<td class="col-money">' + fmt(c.amount) + '</td>'
                + '<td class="col-num">' + fmtNum(c.units) + '</td>'
                + '<td class="col-num">' + fmtNum(c.orders) + '</td>'
                + '</tr>';
        }

        tbody.innerHTML = html || '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">No data</td></tr>';

        var countEl = document.getElementById('customerCount');
        if (countEl) countEl.textContent = items.length + ' customers';
    }

    function escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    // ══════════════════════════════════════════════════════
    // REFRESH BUTTON
    // ══════════════════════════════════════════════════════

    function handleRefresh() {
        var year = window.__SELECTED_YEAR__ || new Date().getFullYear();
        var btn = document.getElementById('refreshBtn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Refreshing...';
        }

        fetch('/sales/dashboard/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ year: year }),
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (data.redirect) {
                window.location.href = data.redirect;
            } else {
                window.location.reload();
            }
        })
        .catch(function () {
            window.location.reload();
        });
    }

    // ══════════════════════════════════════════════════════
    // MASTER RENDER
    // ══════════════════════════════════════════════════════

    function renderAll(data) {
        setChartDefaults();
        renderMonthlyChart(data);
        renderTerritoryChart(data);
        renderProductLineChart(data);
        renderSalesmanChart(data);
        updateCustomerTable(data);
    }

    // ── Theme change observer ──
    var observer = new MutationObserver(function (mutations) {
        for (var i = 0; i < mutations.length; i++) {
            if (mutations[i].attributeName === 'data-theme') {
                renderAll(dashData);
                break;
            }
        }
    });

    // ── Init on DOM ready ──
    document.addEventListener('DOMContentLoaded', function () {
        observer.observe(document.documentElement, { attributes: true });
        renderAll(dashData);

        // Refresh button
        var refreshBtn = document.getElementById('refreshBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', handleRefresh);

        // Year selector — navigate on change
        var yearSelect = document.getElementById('yearSelect');
        if (yearSelect) {
            yearSelect.addEventListener('change', function () {
                window.location.href = '/sales/dashboard?year=' + this.value;
            });
        }
    });

})();