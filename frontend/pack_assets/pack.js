export default function ({ data, parentElement, setTriggerValue }) {
  const list = parentElement.querySelector('.pack-list');
  const status = parentElement.querySelector('[role=status]');
  list.replaceChildren();
  let dragging = null;
  let startOrder = null;
  const ids = () => [...list.children].map(row => row.dataset.id);
  const emit = (removedId = null) => {
    setTriggerValue('action', {
      ordered_ids: ids(), removed_id: removedId, revision: data.revision,
      event_id: crypto.randomUUID(),
    });
  };
  for (const item of data.items) {
    const row = document.createElement('div');
    row.className = 'pack-row';
    row.dataset.id = item.id;
    row.setAttribute('role', 'listitem');
    const handle = document.createElement('button');
    handle.className = 'drag-handle';
    handle.textContent = '⠿';
    handle.setAttribute('aria-label', `Reorder ${item.title}`);
    handle.title = 'Drag to reorder. Keyboard: Arrow Up or Arrow Down.';
    const title = document.createElement('span');
    title.className = 'pack-title';
    title.textContent = item.title;
    const remove = document.createElement('button');
    remove.className = 'remove';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${item.title}`);
    remove.title = 'Remove from Pack';
    remove.onclick = () => { row.remove(); emit(item.id); };
    handle.onkeydown = (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const other = event.key === 'ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
      if (!other) return;
      list.insertBefore(row, event.key === 'ArrowUp' ? other : other.nextElementSibling);
      status.textContent = `Moved ${item.title} to position ${ids().indexOf(item.id) + 1}`;
      emit();
    };
    handle.onpointerdown = (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      dragging = row;
      startOrder = ids().join(',');
      row.classList.add('dragging');
      handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = (event) => {
      if (dragging !== row) return;
      const rows = [...list.children].filter(candidate => candidate !== row);
      const next = rows.find(candidate => {
        const bounds = candidate.getBoundingClientRect();
        return event.clientY < bounds.top + bounds.height / 2;
      });
      list.insertBefore(row, next || null);
      const panel = parentElement.host?.closest('.st-key-pack_panel')?.closest('[data-testid=stColumn]');
      if (panel) {
        const bounds = panel.getBoundingClientRect();
        if (event.clientY > bounds.bottom - 40) panel.scrollTop += 10;
        if (event.clientY < bounds.top + 40) panel.scrollTop -= 10;
      }
    };
    handle.onpointerup = (event) => {
      if (dragging !== row) return;
      dragging = null;
      row.classList.remove('dragging');
      handle.releasePointerCapture(event.pointerId);
      if (ids().join(',') !== startOrder) emit();
    };
    handle.onpointercancel = () => {
      dragging = null;
      row.classList.remove('dragging');
      // Keep Python and the displayed list consistent if the operating system cancels a drag.
      if (startOrder && ids().join(',') !== startOrder) emit();
    };
    row.append(handle, title, remove);
    list.append(row);
  }
}
