---
name: art-direction
description: Find an art direction, get it approved image by image, train it as a LoRA and lock it into a project's recipe.
---

# Finding and locking an art direction

Goal: a reusable style, described by a LoRA and a trigger word written into the
recipe. It is what makes a roster consistent — without it, each character is
generated independently and the batch does not hold together.

## Explore

`enqueue_style_explore(recipe_path, subject, count=8, confirm=true)` produces
eight variants for about $0.015 — **paid**: like any spending, it waits for
the user's explicit agreement. This is the only random search: later
iterations narrow down, they do not start over.

Reread the recipe before launching. `prompt_prefix`, `prompt_suffix` and
`negative_prompt` weigh more than the subject: a failed exploration is almost
always a prefix problem, not bad luck.

## Approve

Look at **each** image with `view_asset`. A thumbnail sheet lies — a variant
can look right small and be unusable large. Discard freely: a mediocre style,
once trained, costs $1.45 and contaminates the whole project.

Keep at least 10 to 20 images. Fewer, and the LoRA has nothing to learn from.

## Train

`enqueue_style_train(recipe_path, image_ids, steps=1000, confirm=true)` —
**paid, about $1.45** for 1000 steps; requires the user's explicit agreement in
the conversation.

## Lock

Copy `lora_air` and `trigger_word` into the recipe's `style:` section. While
they are empty or commented out, only the prompts act and the style is not
reproducible from one session to the next — the studio's most expensive
mistake, because it only shows on the second batch.

Done when: two characters generated on different days with the same recipe
look alike. If they do not, `lora_air` is not wired — check it before touching
the prompts.

Example: a Celtic fantasy project. Explore on a knight, keep fourteen
cold-palette images, train, then write `lora_air: gamestudio:celtic-style@1`
and `trigger_word: celtic_style`.

Dependencies: a recipe (`.gamestudio/recipe.yaml`), a Runware key, the user's
agreement for both paid operations. See `roster`.
