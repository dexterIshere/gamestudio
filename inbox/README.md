# Inbox

What you drop into the studio to give to an agent.

    inbox/
      attachments/
        2026-09-29_201420_9bfab051.png    one drop = one file

## What it is for

An agent reads files. Pasting an image into a terminal tells it nothing, and a
clipboard object exists nowhere on disk. The inbox is therefore the one place
to **drop** things — and what the agent receives is a **path**, written into
the conversation:

    inbox/attachments/2026-09-29_201420_9bfab051.png

Three gestures lead here, and all three write the path:

- **paste a screenshot** into the Chats window;
- **drag a file** onto it (under Tauri, the real path is used; in a browser,
  the bytes are sent);
- the **Inbox button** of the Chats window, or `gamestudio inbox add <path>`.

## How files are named

`<date>_<time>_<first eight characters of the hash>.<extension>`

Two intended consequences: dropping the same content twice gives **the same
path** (nothing is duplicated), and a drop can **never overwrite** an earlier
one. An image whose bytes are not an image is refused at drop time — better to
say so at once than to leave a file nobody will open.

The inbox is emptied by hand (`gamestudio inbox rm <name>`, or by deleting the
file): the studio keeps no copy of it elsewhere, and no project claims
anything in it. It is not the library: what is produced goes into
`<project folder>/.gamestudio/library/`.
