export default function ({ data, parentElement, setTriggerValue }) {
  const list = parentElement.querySelector('.pack-list');
  const status = parentElement.querySelector('[role=status]');
  // Stop an in-flight interaction before Streamlit supplies a newer selection.
  list.cancelDrag?.();
  list.replaceChildren();
  list.dataset.revision = String(data.revision);
  list.tabIndex = -1;
  let drag = null;
  const ids = () => [...list.children].map(row => row.dataset.id);
  const emit = (removedId = null) => {
    setTriggerValue('action', {
      ordered_ids: ids(), removed_id: removedId, revision: data.revision,
      event_id: crypto.randomUUID(),
    });
  };
  const restore = order => {
    const rows = new Map([...list.children].map(row => [row.dataset.id, row]));
    for (const id of order) list.append(rows.get(id));
  };
  const finish = commit => {
    if (!drag) return;
    const previous = drag;
    drag = null;
    cancelAnimationFrame(previous.frame);
    previous.row.classList.remove('dragging');
    if (list.hasPointerCapture(previous.pointerId)) list.releasePointerCapture(previous.pointerId);
    if (!commit) {
      restore(previous.order);
      return;
    }
    if (ids().join(',') !== previous.order.join(',')) {
      status.textContent = `Moved ${previous.row.querySelector('.pack-title').textContent} to position ${ids().indexOf(previous.row.dataset.id) + 1}`;
      emit();
    }
  };
  const place = () => {
    if (!drag?.active) return;
    const next = [...list.children].find(candidate => {
      if (candidate === drag.row) return false;
      const bounds = candidate.getBoundingClientRect();
      return drag.y < bounds.top + bounds.height / 2;
    }) || null;
    if (drag.row.nextElementSibling !== next) list.insertBefore(drag.row, next);
  };
  const autoScroll = () => {
    if (!drag?.active) return;
    let panel = parentElement.host;
    while (panel) {
      if (panel.scrollHeight > panel.clientHeight && /auto|scroll/.test(getComputedStyle(panel).overflowY)) break;
      panel = panel.parentElement;
    }
    if (panel) {
      const bounds = panel.getBoundingClientRect();
      const top = Math.max(60, bounds.top);
      const bottom = Math.min(innerHeight, bounds.bottom);
      if (drag.y < top + 32) panel.scrollTop -= 10;
      else if (drag.y > bottom - 32) panel.scrollTop += 10;
      place();
    }
    drag.frame = requestAnimationFrame(autoScroll);
  };
  list.cancelDrag = () => finish(false);
  list.onpointerdown = event => {
    if (event.button !== 0 || event.isPrimary === false || drag || data.items.length < 2) return;
    if (event.target.closest('.remove')) return;
    const row = event.target.closest('.pack-row');
    if (!row) return;
    event.preventDefault();
    drag = {
      row, pointerId: event.pointerId, order: ids(), active: false,
      startX: event.clientX, startY: event.clientY, y: event.clientY,
    };
    list.focus({ preventScroll: true });
    // The capture target never moves when a row changes its position in the DOM.
    list.setPointerCapture(event.pointerId);
  };
  list.onpointermove = event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.y = event.clientY;
    if (!drag.active) {
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4) return;
      drag.active = true;
      drag.row.classList.add('dragging');
      drag.frame = requestAnimationFrame(autoScroll);
    }
    event.preventDefault();
    place();
  };
  list.onpointerup = event => {
    if (drag && event.pointerId === drag.pointerId) finish(true);
  };
  list.onpointercancel = event => {
    if (drag && event.pointerId === drag.pointerId) finish(false);
  };
  list.onlostpointercapture = event => {
    if (drag && event.pointerId === drag.pointerId) finish(false);
  };
  list.onkeydown = event => {
    if (event.key === 'Escape' && drag) {
      event.preventDefault();
      finish(false);
    }
  };
  list.ondragstart = event => event.preventDefault();
  for (const item of data.items) {
    const row = document.createElement('div');
    row.className = 'pack-row';
    row.dataset.id = item.id;
    row.setAttribute('role', 'listitem');
    const handle = document.createElement('button');
    handle.className = 'drag-handle';
    handle.textContent = '⠿';
    handle.setAttribute('aria-label', `Reorder ${item.title}`);
    handle.title = 'Drag this row to reorder. Keyboard: Arrow Up or Arrow Down.';
    const title = document.createElement('span');
    title.className = 'pack-title';
    title.textContent = item.title;
    const remove = document.createElement('button');
    remove.className = 'remove';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${item.title}`);
    remove.title = 'Remove from Pack';
    remove.onclick = () => {
      if (drag) return;
      row.remove();
      emit(item.id);
    };
    handle.onkeydown = (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      if (drag) return;
      const other = event.key === 'ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
      if (!other) return;
      list.insertBefore(row, event.key === 'ArrowUp' ? other : other.nextElementSibling);
      status.textContent = `Moved ${item.title} to position ${ids().indexOf(item.id) + 1}`;
      emit();
    };
    row.append(handle, title, remove);
    list.append(row);
  }
}
