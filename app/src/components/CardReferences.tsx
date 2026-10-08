/**
 * A card's references: the images the user adds to show what they want.
 *
 * An art direction is shown before it is written: a nebula, a screen from
 * another game, a color sheet. They are dropped on the workbench, pasted, or
 * picked; they are stored next to the card (`<name>.references/`,
 * `service/cards.py`), follow it when it is renamed, and the agent discussing
 * it looks at them. The text's "References" section says what to keep from
 * each one, by file name.
 *
 * `useImageDrop` also serves a section's "New": images are dropped there with
 * the request.
 */

import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { documentImageUrl, type CardReferenceImage } from "../api";
import { listenFileDrop } from "../lib/host";
import { useAddCardReferences, useDeleteCardReference } from "../lib/queries";
import { Badge, CloseCross, Dialog, Empty } from "./ui";
import { t } from "../lib/i18n";

/** The formats a reference accepts: those the server checks (`REFERENCE_TYPES`). */
export const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
const IMAGE_SUFFIX = /\.(png|jpe?g|webp|gif)$/i;
/** The constraint, stated where files are dropped: it avoids a refusal. */
export const IMAGE_LIMITS = t("PNG · JPEG · WebP · GIF — 25 MB");

/** What a drop brings: bytes (browser, clipboard), or paths (shell). */
export interface Dropped {
  files: File[];
  paths: string[];
}

const isImage = (file: File) => file.type.startsWith("image/") || IMAGE_SUFFIX.test(file.name);

/**
 * Dropping or pasting images on an area. What is not an image is silently
 * ignored; pasted text stays with the field that receives it.
 *
 * `bind` goes on the area's element, `ref` included: in the shell, a file
 * dragged from the file manager does not reach the DOM, the webview reports
 * it with its path (`listenFileDrop`). Outside the shell, the DOM `drop`
 * carries the bytes.
 */
export function useImageDrop(onDrop: (dropped: Dropped) => void) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  // The area registers once; it calls the latest `onDrop`.
  const latest = useRef(onDrop);
  useEffect(() => {
    latest.current = onDrop;
  });

  const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer.types).includes("Files");

  const ref = useCallback((element: HTMLElement | null) => {
    if (!element) return;
    return listenFileDrop(element, {
      hover: (over) => {
        depth.current = 0;
        setDragging(over);
      },
      drop: (paths) => {
        const images = paths.filter((path) => IMAGE_SUFFIX.test(path));
        if (images.length) latest.current({ files: [], paths: images });
      },
    });
  }, []);

  const bind = {
    ref,
    onDragEnter: (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: () => {
      // Hovering a child leaves the parent: count the entries.
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer.files).filter(isImage);
      if (files.length) onDrop({ files, paths: [] });
    },
    onPaste: (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData.items)
        .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter((file): file is File => file !== null);
      if (!files.length) return;
      event.preventDefault();
      onDrop({ files, paths: [] });
    },
  };
  return { dragging, bind };
}

/** The button that opens the file picker, limited to images. */
export function PickImages({ onPick, label = t("Add images"), disabled = false }: {
  onPick: (files: File[]) => void;
  label?: string;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        {label}
      </button>
      <input
        ref={input}
        type="file"
        accept={IMAGE_ACCEPT}
        multiple
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []).filter(isImage);
          event.target.value = "";
          if (files.length) onPick(files);
        }}
      />
    </>
  );
}

export default function CardReferences({ project, folder, name, references }: {
  project: string;
  folder: string;
  name: string;
  references: CardReferenceImage[];
}) {
  const add = useAddCardReferences();
  const [viewing, setViewing] = useState<CardReferenceImage | null>(null);
  const deposit = (dropped: Dropped) => add.mutate({ project, folder, name, ...dropped });
  const { dragging, bind } = useImageDrop(deposit);
  const url = (entry: CardReferenceImage) =>
    `${documentImageUrl(project, folder, `${name}.references/${entry.file}`)}&v=${encodeURIComponent(entry.added_at)}`;

  return (
    <div className={`card-refs ${dragging ? "is-dragging" : ""}`} tabIndex={-1} {...bind}>
      <div className="card-refs-bar">
        <PickImages disabled={add.isPending} onPick={(files) => deposit({ files, paths: [] })} />
        <span className="hint">{IMAGE_LIMITS}</span>
        <span className="spacer" />
        {add.isPending && <Badge tone="quiet">{t("Uploading…")}</Badge>}
        {add.isError && <Badge tone="danger">{(add.error as Error).message}</Badge>}
      </div>
      {references.length === 0 ? (
        <Empty title={dragging ? t("Drop") : t("No reference")} />
      ) : (
        <ul className="card-refs-grid">
          {references.map((entry) => (
            <ReferenceCard
              key={entry.file}
              project={project}
              folder={folder}
              name={name}
              entry={entry}
              src={url(entry)}
              onOpen={() => setViewing(entry)}
            />
          ))}
        </ul>
      )}
      {viewing && (
        <Dialog wide title={viewing.file} onClose={() => setViewing(null)}>
          <div className="card-refs-view board">
            <img src={url(viewing)} alt={viewing.file} />
          </div>
        </Dialog>
      )}
    </div>
  );
}

function ReferenceCard({ project, folder, name, entry, src, onOpen }: {
  project: string;
  folder: string;
  name: string;
  entry: CardReferenceImage;
  src: string;
  onOpen: () => void;
}) {
  const remove = useDeleteCardReference();
  const [asking, setAsking] = useState(false);
  const size = entry.width && entry.height ? `${entry.width} × ${entry.height}` : null;

  return (
    <li className="card-ref">
      <button type="button" className="card-ref-image board check" onClick={onOpen} title={entry.file}>
        <img src={src} alt={entry.file} loading="lazy" />
      </button>
      <div className="card-ref-cap">
        <span className="mono" title={entry.path}>{entry.file}</span>
        {size && <span className="num">{size}</span>}
      </div>
      {asking ? (
        <button
          type="button"
          className="btn btn-danger btn-sm card-ref-remove"
          disabled={remove.isPending}
          title={t("Remove “{file}”: nothing brings it back", { file: entry.file })}
          onClick={() => remove.mutate({ project, folder, name, file: entry.file })}
          onBlur={() => !remove.isPending && setAsking(false)}
          autoFocus
        >
          {remove.isPending ? t("Removing…") : t("Remove")}
        </button>
      ) : (
        <button
          type="button"
          className="btn btn-ghost btn-icon card-ref-remove"
          aria-label={t("Remove {file}", { file: entry.file })}
          title={t("Remove")}
          onClick={() => setAsking(true)}
        >
          <CloseCross />
        </button>
      )}
    </li>
  );
}
