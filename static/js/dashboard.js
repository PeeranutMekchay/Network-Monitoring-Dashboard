// Refresh device status cards from /api/status without reloading the page.
(function () {
  const grid = document.getElementById('device-grid');
  const filter = document.getElementById('filter');

  function applyFilter() {
    if (!filter) return;
    const q = filter.value.trim().toLowerCase();
    document.querySelectorAll('.device-col').forEach(col => {
      col.classList.toggle('d-none', q && !col.textContent.toLowerCase().includes(q));
    });
  }

  function updateCard(d) {
    const col = grid.querySelector(`.device-col[data-id="${d.id}"]`);
    if (!col) return false;
    const card = col.querySelector('.device-card');
    card.classList.remove('status-online', 'status-offline', 'status-unknown');
    card.classList.add(`status-${d.status}`);
    card.querySelector('.status-badge').textContent = d.status.toUpperCase();
    card.querySelector('.latency').textContent = d.latency_ms != null ? `${d.latency_ms.toFixed(1)} ms` : '-';
    card.querySelector('.http').textContent = d.http_status ? `HTTP ${d.http_status}` : '';
    card.querySelector('.checked-at').textContent = d.checked_at || 'ยังไม่ได้ตรวจ';
    const open = d.open_ports.split(',');
    card.querySelectorAll('.port-badge').forEach(b => {
      const isOpen = open.includes(b.textContent.trim().split(' ')[0]);
      b.classList.toggle('bg-success', isOpen);
      b.classList.toggle('bg-secondary', !isOpen);
    });
    return true;
  }

  async function refresh() {
    try {
      const resp = await fetch('/api/status', { headers: { Accept: 'application/json' } });
      if (resp.status === 401) { window.location.reload(); return; }
      const data = await resp.json();
      for (const key of ['total', 'online', 'offline', 'unknown']) {
        document.getElementById(`stat-${key}`).textContent = data.summary[key];
      }
      const shown = grid ? grid.querySelectorAll('.device-col').length : 0;
      // Device list changed (added/removed elsewhere): re-render server side.
      if (shown !== data.devices.length || (grid && !data.devices.every(updateCard))) {
        window.location.reload();
        return;
      }
      document.getElementById('last-refresh').textContent = data.server_time;
    } catch (err) {
      console.warn('Status refresh failed', err);
    }
  }

  if (filter) filter.addEventListener('input', applyFilter);
  document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString('th-TH');
  setInterval(refresh, window.DASHBOARD_REFRESH_MS || 15000);
})();
