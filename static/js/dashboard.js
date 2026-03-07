/**
 * TWG Sales Dashboard — Chart rendering & filter interactivity
 * Uses Chart.js for visualizations, vanilla JS for filters.
 * All data passed via window.__DASH_DATA__ and window.__FILTER_OPTIONS__
 */

(function () {
    'use strict';

    var dashData = window.__DASH_DATA__ || {};
    var filterOpts = window.__FILTER_OPTIONS__ || {};
    var charts = {};

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

    // ── Format number with commas ──
    function fmt(n) {
        return '$' + Number(n || 0).toLocaleString('en-US');
    }

    function fmtNum(n) {
        return Number(n || 0).toLocaleString('en-US');
    }

    // ══════════════════════════════════════════════════════
    // KPI UPDATE
    // ══════════════════════════════════════════════════════

    function updateKPIs(data) {
        var s = data.summary || {};
        var el;

        el = document.getElementById('kpi-total-amount');
        if (el) el.textContent = fmt(s.total_amount);

        el = document.getElementById('kpi-total-units');
        if (el) el.textContent = fmtNum(s.total_units);

        el = document.getElementById('kpi-total-orders');
        if (el) el.textContent = fmtNum(s.total_orders);

        el = document.getElementById('kpi-avg-order');
        if (el) el.textContent = fmt(s.avg_order_value);

        el = document.getElementById('kpi-total-lines');
        if (el) el.textContent = fmtNum(s.total_lines);

        // Region bar
        var rs = data.region_split || {};
        var total = (rs.us_amount || 0) + (rs.ca_amount_usd || 0);
        var usPct = total > 0 ? ((rs.us_amount / total) * 100) : 100;

        el = document.getElementById('bar-us');
        if (el) el.style.width = usPct + '%';

        el = document.getElementById('bar-ca');
        if (el) el.style.width = (100 - usPct) + '%';

        el = document.getElementById('legend-us-val');
        if (el) el.textContent = fmt(rs.us_amount);

        el = document.getElementById('legend-ca-val');
        if (el) el.textContent = fmt(rs.ca_amount_usd) + ' (CAD ' + fmt(rs.ca_amount) + ')';
    }

    // ══════════════════════════════════════════════════════
    // CHARTS
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
                        callbacks: {
                            label: function (ctx) { return fmt(ctx.raw); }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: c.grid },
                        ticks: {
                            callback: function (v) { return '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v); },
                            font: { family: "'JetBrains Mono', monospace", size: 11 }
                        }
                    },
                    y: {
                        grid: { display: false },
                        ticks: { font: { size: 12 } }
                    }
                }
            }
        });
    }

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
                            boxWidth: 10,
                            boxHeight: 10,
                            padding: 8,
                            font: { size: 11 },
                            usePointStyle: true,
                            pointStyle: 'circle',
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
                        callbacks: {
                            label: function (ctx) { return fmt(ctx.raw); }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: c.grid },
                        ticks: {
                            callback: function (v) { return '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v); },
                            font: { family: "'JetBrains Mono', monospace", size: 11 }
                        }
                    },
                    y: {
                        grid: { display: false },
                        ticks: { font: { size: 12 } }
                    }
                }
            }
        });
    }

    // ══════════════════════════════════════════════════════
    // TABLES
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
    // FILTERS
    // ══════════════════════════════════════════════════════

    function toggleFilterPanel() {
        var panel = document.getElementById('filterPanel');
        if (panel) panel.classList.toggle('open');
    }

    function getActiveFilters() {
        var filters = {};

        var selTerritory = document.getElementById('filterTerritory');
        var selSalesman = document.getElementById('filterSalesman');
        var selProductLine = document.getElementById('filterProductLine');

        if (selTerritory) {
            var vals = getSelectedValues(selTerritory);
            if (vals.length) filters.territories = vals;
        }
        if (selSalesman) {
            var vals2 = getSelectedValues(selSalesman);
            if (vals2.length) filters.salesmen = vals2;
        }
        if (selProductLine) {
            var vals3 = getSelectedValues(selProductLine);
            if (vals3.length) filters.product_lines = vals3;
        }

        return filters;
    }

    function getSelectedValues(select) {
        var vals = [];
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].selected && select.options[i].value) {
                vals.push(select.options[i].value);
            }
        }
        return vals;
    }

    function countActiveFilters() {
        var filters = getActiveFilters();
        var count = 0;
        for (var key in filters) {
            if (filters[key] && filters[key].length > 0) count++;
        }
        return count;
    }

    function updateFilterCount() {
        var countEl = document.getElementById('filterCount');
        var n = countActiveFilters();
        if (countEl) {
            countEl.textContent = n;
            countEl.style.display = n > 0 ? 'inline-flex' : 'none';
        }
    }

    function clearAllFilters() {
        var selects = document.querySelectorAll('.filter-group select');
        for (var i = 0; i < selects.length; i++) {
            var sel = selects[i];
            for (var j = 0; j < sel.options.length; j++) {
                sel.options[j].selected = false;
            }
        }
        updateFilterCount();
        applyFilters();
    }

    function applyFilters() {
        var filters = getActiveFilters();
        updateFilterCount();

        // Show loading state
        document.body.style.cursor = 'wait';

        fetch('/sales/dashboard/filter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(filters),
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            dashData = data;
            updateAll(data);
            document.body.style.cursor = '';
        })
        .catch(function (err) {
            console.error('Filter error:', err);
            document.body.style.cursor = '';
        });
    }

    // ══════════════════════════════════════════════════════
    // MASTER RENDER
    // ══════════════════════════════════════════════════════

    function updateAll(data) {
        setChartDefaults();
        updateKPIs(data);
        renderTerritoryChart(data);
        renderProductLineChart(data);
        renderSalesmanChart(data);
        updateCustomerTable(data);
    }

    // ── Theme change observer ──
    var observer = new MutationObserver(function (mutations) {
        for (var i = 0; i < mutations.length; i++) {
            if (mutations[i].attributeName === 'data-theme') {
                updateAll(dashData);
                break;
            }
        }
    });

    // ── Init on DOM ready ──
    document.addEventListener('DOMContentLoaded', function () {
        // Observe theme changes
        observer.observe(document.documentElement, { attributes: true });

        // Initial render
        updateAll(dashData);

        // Filter toggle
        var toggleBtn = document.getElementById('filterToggleBtn');
        if (toggleBtn) toggleBtn.addEventListener('click', toggleFilterPanel);

        // Apply filters button
        var applyBtn = document.getElementById('filterApplyBtn');
        if (applyBtn) applyBtn.addEventListener('click', applyFilters);

        // Clear filters button
        var clearBtn = document.getElementById('filterClearBtn');
        if (clearBtn) clearBtn.addEventListener('click', clearAllFilters);
    });

})();