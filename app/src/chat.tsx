/**
 * Entry point of the Chats window.
 *
 * Same address resolution as the main window, but no `StudioProvider`: a
 * terminal belongs to no project, and giving it an active project would
 * suggest it follows one.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ChatApp from "./chat/ChatApp";
import { resolveApiBase } from "./api";
import "@xterm/xterm/css/xterm.css";
import "./styles.css";
import { t } from "./lib/i18n";

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 1000 },
  },
});

const root = createRoot(document.getElementById("root")!);

resolveApiBase()
  .then(() => {
    root.render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <ChatApp />
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
