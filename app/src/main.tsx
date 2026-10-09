/**
 * Front-end entry point.
 *
 * The API address is resolved before the first render: the Rust shell picked a
 * free port, and the whole interface (down to thumbnail URLs computed during
 * render) assumes that address is known.
 *
 * On first launch the language is chosen before anything else: the choice
 * reloads the window, which starts again here with a known language.
 *
 * Every scroller is kept on whole pixels (`lib/scroll.ts`): under WebKitGTK a
 * fractional offset blurs everything that scrolls.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { resolveApiBase } from "./api";
import LanguageChoice from "./components/LanguageChoice";
import { syncShellLang } from "./lib/host";
import { langChosen, t } from "./lib/i18n";
import { snapScrollingToPixels } from "./lib/scroll";
import { StudioProvider } from "./lib/store";
import "./styles.css";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // The studio is local: a failed request is not a network hiccup, the
      // server is just not there. Insisting does not help.
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 5000,
    },
  },
});

snapScrollingToPixels();

const root = createRoot(document.getElementById("root")!);

if (!langChosen()) {
  root.render(<LanguageChoice />);
} else {
  void syncShellLang();
  resolveApiBase()
    .then(() => {
      root.render(
        <StrictMode>
          <QueryClientProvider client={client}>
            <StudioProvider>
              <App />
            </StudioProvider>
          </QueryClientProvider>
        </StrictMode>,
      );
    })
    .catch((error: unknown) => {
      root.render(
        <div className="empty" style={{ height: "100%" }}>
          <div>{t("The studio server did not start.")}</div>
          <div className="hint mono">{String(error)}</div>
        </div>,
      );
    });
}
