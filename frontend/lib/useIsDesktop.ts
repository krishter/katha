"use client";

import { useSyncExternalStore } from "react";

// A mouse or trackpad that can hover: a laptop or desktop. A phone or
// tablet reports a coarse pointer and no hover. Width alone is the wrong
// test: a wide tablet still opens wa.me links in WhatsApp.
const QUERY = "(hover: hover) and (pointer: fine)";

function subscribe(onChange: () => void) {
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/**
 * True on a laptop or desktop. False on the server and on first render, so
 * the phone layout (the one most families see) is what renders first.
 */
export function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
