import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { createReadStream, readFileSync, readdirSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

// The fonts Excalidraw draws with (a card's sketch). Its interface font comes
// with its stylesheet, and Vite bundles it on its own; the canvas text fonts
// are paths the module builds at runtime from `window.EXCALIDRAW_ASSET_PATH`,
// which it would otherwise fetch from esm.sh, refused by the shell's CSP
// (`font-src 'self' data:`). So the application serves them itself under
// `excalidraw/fonts/`, next to its pages: read from node_modules in
// development, copied into dist/ at build time, never committed.
// Skipped: Assistant (the interface font, already bundled by Vite); Xiaolai
// (13 MB of Chinese, Japanese and Korean glyphs, which a Latin-script sketch
// does not need: such a character then falls back to a system font);
// Liberation (GPLv2 + font exception: it would ship in dist/ without its
// license).
const EXCALIDRAW_FONTS_URL = "excalidraw/fonts";
const EXCALIDRAW_FONTS_SKIPPED = ["Assistant", "Xiaolai", "Liberation"];

function excalidrawFonts(): Plugin {
  // The `fonts/` folder next to the module: found through Node's resolution,
  // it does not depend on the node_modules layout.
  const require = createRequire(import.meta.url);
  const root = join(dirname(require.resolve("@excalidraw/excalidraw")), "fonts");
  const served = (file: string) =>
    !EXCALIDRAW_FONTS_SKIPPED.includes(file.split(sep)[0] ?? "") && file.endsWith(".woff2");
  return {
    name: "gamestudio:excalidraw-fonts",
    configureServer(server) {
      server.middlewares.use(`/${EXCALIDRAW_FONTS_URL}`, (request, response, next) => {
        const file = join(root, decodeURIComponent((request.url ?? "").split("?")[0] ?? ""));
        const name = relative(root, file);
        if (name.startsWith("..") || !served(name) || !statSync(file, { throwIfNoEntry: false })?.isFile()) {
          return next();
        }
        response.setHeader("Content-Type", "font/woff2");
        createReadStream(file).pipe(response);
      });
    },
    generateBundle() {
      for (const name of readdirSync(root, { recursive: true, encoding: "utf8" })) {
        if (!served(name)) continue;
        this.emitFile({
          type: "asset",
          fileName: `${EXCALIDRAW_FONTS_URL}/${name.split(sep).join("/")}`,
          source: readFileSync(join(root, name)),
        });
      }
    },
  };
}

// The front is served by Tauri: relative paths, and no attempt to open a
// browser or guess the host. The port is fixed because the Rust shell must
// know where to point the view in development.
export default defineConfig({
  plugins: [react(), excalidrawFonts()],
  base: "./",
  clearScreen: false,
  server: {
    port: 5183,
    strictPort: true,
    // Hot reload must reach Tauri's web view, which is not a regular browser:
    // giving it the host explicitly stops it from connecting to an origin the
    // view does not know.
    host: "127.0.0.1",
  },
  build: {
    // One page per window, not a single one with a switch: the Chats window
    // never loads three.js, the studio window never loads xterm, and the update
    // window loads neither. Each has its own entry point, and none has to guess
    // which window it runs in.
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        chat: fileURLToPath(new URL("./chat.html", import.meta.url)),
        update: fileURLToPath(new URL("./update.html", import.meta.url)),
      },
    },
    outDir: "dist",
    emptyOutDir: true,
    // Tauri's web views are recent: a high target avoids needless
    // transpilation, notably around three.js.
    target: "es2022",
    sourcemap: false,
    // The gzip size of each chunk helps nobody (Tauri compresses the front its
    // own way), and computing it lengthens every update.
    reportCompressedSize: false,
  },
});
