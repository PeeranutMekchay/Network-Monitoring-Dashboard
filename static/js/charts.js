// Latency line chart and daily uptime bar chart for the device detail page.
(function () {
  const api = window.DEVICE_API;
  let latencyChart, uptimeChart;

  async function loadLatency(hours) {
    const rows = await (await fetch(`${api.latency}?hours=${hours}`)).json();
    const labels = rows.map(r => r.t);
    const latency = rows.map(r => r.latency_ms);
    // Mark offline checks as red points on the x-axis so outages are visible.
    const offline = rows.map(r => (r.status === 'offline' ? 0 : null));

    const data = {
      labels,
      datasets: [
        {
          label: 'Latency (ms)', data: latency, borderColor: '#0d6efd',
          backgroundColor: 'rgba(13,110,253,.1)', fill: true, tension: .25,
          pointRadius: rows.length > 200 ? 0 : 2, spanGaps: false,
        },
        {
          label: 'Offline', data: offline, showLine: false, pointRadius: 4,
          pointBackgroundColor: '#dc3545', borderColor: '#dc3545',
        },
      ],
    };

    if (latencyChart) {
      latencyChart.data = data;
      latencyChart.update();
      return;
    }
    latencyChart = new Chart(document.getElementById('latencyChart'), {
      type: 'line',
      data,
      options: {
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10, callback(v) { return this.getLabelForValue(v).slice(5, 16); } } },
          y: { beginAtZero: true, title: { display: true, text: 'ms' } },
        },
      },
    });
  }

  async function loadUptime() {
    const data = await (await fetch(`${api.uptime}?days=7`)).json();
    const values = data.daily.map(d => d.uptime);
    const colors = values.map(v => (v == null ? '#adb5bd' : v >= 99 ? '#198754' : v >= 95 ? '#ffc107' : '#dc3545'));
    uptimeChart = new Chart(document.getElementById('uptimeChart'), {
      type: 'bar',
      data: {
        labels: data.daily.map(d => d.day.slice(5)),
        datasets: [{ label: '% Uptime', data: values, backgroundColor: colors }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => `${ctx.raw ?? '-'}% (${data.daily[ctx.dataIndex].checks} checks)` } },
        },
        scales: { y: { min: 0, max: 100, ticks: { callback: v => `${v}%` } } },
      },
    });
  }

  document.querySelectorAll('#range-buttons button').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('#range-buttons button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadLatency(btn.dataset.hours);
  }));

  loadLatency(24);
  loadUptime();
  // Keep the chart current while the page is open.
  setInterval(() => {
    const active = document.querySelector('#range-buttons .active');
    loadLatency(active ? active.dataset.hours : 24);
  }, 60000);
})();
