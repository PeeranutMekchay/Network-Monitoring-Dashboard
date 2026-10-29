// Quick-select buttons for common ports on the add/edit device form.
(function () {
  const input = document.getElementById('ports');
  if (!input) return;
  const chips = document.querySelectorAll('.port-chip');

  const current = () => input.value.split(',').map(s => s.trim()).filter(Boolean);
  const sync = () => {
    const ports = current();
    chips.forEach(c => c.classList.toggle('active', ports.includes(c.dataset.port)));
  };

  chips.forEach(chip => chip.addEventListener('click', () => {
    let ports = current();
    const p = chip.dataset.port;
    ports = ports.includes(p) ? ports.filter(x => x !== p) : ports.concat(p);
    input.value = ports.sort((a, b) => a - b).join(',');
    sync();
  }));
  input.addEventListener('input', sync);
  sync();
})();
