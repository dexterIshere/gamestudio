/**
 * Whole-pixel scrolling: a scroller left between two pixels is put back on one when it rests.
 *
 * WebKit (WebKitGTK is the shell's engine) can leave a scroller at a
 * fractional offset after a wheel notch -- 120.4 px. It then resamples the
 * scroller's composited content: every text in it turns blurry, and shimmers
 * while anything on the page animates. Script only reads the rounded offset
 * (120) and writing that same value changes nothing, so the snap steps one
 * pixel away and back within the same task: nothing is painted in between,
 * and the second write lands on the whole pixel.
 *
 * Terminals are left alone: xterm keeps its own viewport and scrollback in
 * step with its scroll position.
 */

/** How long a scroller must stay still before it is snapped. */
const REST_MS = 120;

/** WebKit, without Chromium: the engines that leave fractional offsets on screen. */
function isWebKit(): boolean {
  const agent = navigator.userAgent;
  return /AppleWebKit/.test(agent) && !/Chrome|Chromium|Edg\//.test(agent);
}

export function snapScrollingToPixels(): void {
  if (!isWebKit()) return;
  const timers = new WeakMap<Element, number>();
  const snapping = new WeakSet<Element>();

  const snap = (element: Element) => {
    snapping.add(element);
    if (element.scrollHeight > element.clientHeight) {
      const top = element.scrollTop;
      element.scrollTo({ top: top > 0 ? top - 1 : top + 1, behavior: "instant" });
      element.scrollTo({ top, behavior: "instant" });
    }
    if (element.scrollWidth > element.clientWidth) {
      const left = element.scrollLeft;
      element.scrollTo({ left: left > 0 ? left - 1 : left + 1, behavior: "instant" });
      element.scrollTo({ left, behavior: "instant" });
    }
    // The scroll events these writes raise are not a gesture to snap again.
    requestAnimationFrame(() => requestAnimationFrame(() => snapping.delete(element)));
  };

  document.addEventListener("scroll", (event) => {
    const element = event.target instanceof Element ? event.target : document.scrollingElement;
    if (!element || snapping.has(element) || element.closest(".xterm")) return;
    window.clearTimeout(timers.get(element));
    timers.set(element, window.setTimeout(() => snap(element), REST_MS));
  }, { capture: true, passive: true });
}
