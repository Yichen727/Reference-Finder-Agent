export default function ({ data, parentElement }) {
  const root = parentElement.querySelector('.preview');
  const images = data.images;
  const identity = images.map(image => image.image).join('|');
  const previousIndex = root.previewIdentity === identity ? Number(root.dataset.index || 0) : 0;
  root.previewIdentity = identity;
  root.replaceChildren();
  root.classList.toggle('detail', data.detail);
  root.setAttribute('aria-label', `${data.title} images`);
  root.tabIndex = 0;
  const image = document.createElement('img');
  image.decoding = 'async';
  const counter = document.createElement('span');
  counter.className = 'counter';
  counter.setAttribute('aria-live', 'polite');
  counter.setAttribute('aria-atomic', 'true');
  let index = Math.min(previousIndex, images.length - 1);
  const show = (next) => {
    index = (next + images.length) % images.length;
    root.dataset.index = String(index);
    image.src = images[index].image;
    image.alt = `${data.title} — image ${index + 1} of ${images.length}`;
    counter.textContent = `${index + 1}/${images.length}`;
  };
  const arrow = (direction, label, symbol) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = direction < 0 ? 'previous' : 'next';
    button.textContent = symbol;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.onclick = (event) => { event.stopPropagation(); show(index + direction); };
    return button;
  };
  root.append(image, arrow(-1, 'Previous image', '‹'), arrow(1, 'Next image', '›'), counter);
  root.onkeydown = (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      event.stopPropagation();
      show(index + (event.key === 'ArrowRight' ? 1 : -1));
    }
  };
  show(index);
}
