/**
 * Disk paths as the interface shows them: a folder's name, a file revealed on disk.
 */

import { revealPath } from "./host";
import { t } from "./i18n";
import { useStudio } from "./store";

/** `/home/me/games/my-game/` -> `my-game`: a folder is named by its last segment. */
export const folderName = (path: string): string => path.split("/").filter(Boolean).pop() ?? path;

/**
 * "Show on disk": the file manager opens on the path, or its refusal is
 * reported. A button that silently does nothing looks broken.
 */
export function useReveal(): (path: string) => Promise<void> {
  const { notify } = useStudio();
  return async (path: string) => {
    const refusal = await revealPath(path);
    if (refusal) notify({ kind: "error", title: t("The folder does not open"), body: refusal });
  };
}
