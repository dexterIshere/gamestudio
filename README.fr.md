# gamestudio

*[English](README.md) · Français*

Studio de génération d'assets de jeu : d'une fiche du monde à une entité 3D
riggée et animée, prête à ouvrir dans Godot — et rendue en planches de sprites
quand le jeu est en 2D.

```
fiche ──► concepts (pose imposée) ──► mesh 3D ──────────► rig + animations ──► .glb ──► Godot
           Runware                     Runware, Tripo      un agent, Blender    └──► sprites N dir.
                                       ou local (gratuit)
```

Le dépôt est en anglais ; l'interface de bureau parle anglais ou français. Ce
fichier est la traduction française de [`README.md`](README.md).

---

## Ce que le système fait, et ce qu'il ne fait pas

**Deux voies payantes fournissent l'apparence.** Runware : génération d'images,
LoRA de style entraînée sur vos propres références, ControlNet, détourage, et
passage en 3D (Tripo, Hunyuan 3D, TRELLIS). L'API Tripo en direct, facultative :
les modèles que Runware n'héberge pas (P2 et son quad natif, P1, la famille H).
Une voie gratuite existe à côté : forger le mesh en local (`img2threejs`).

**Un agent fait le rig et les animations.** Aucun modèle ne rigge bien, une
animation par diffusion dérive d'une image à l'autre, et aucun outil de rig codé
dans le studio ne vaut les logiciels faits pour ça. L'étape 3D d'une fiche
confie donc son entité à un agent (Claude Code, Codex…), qui la rigge et l'anime
dans Blender — un squelette pour un être, des pièces pour un bâtiment ou une
machine — et rend un GLB qui porte toutes ses animations.

**Le code du studio fait ce qui doit être exact** : l'inventaire du GLB livré,
le rendu en sprites (un cadrage et une palette pour toutes les frames), l'export
Godot et sa validation headless.

**Toute dépense exige une confirmation explicite** : `confirm=true` pour un
outil MCP ou une route, un dialogue qui annonce le montant dans l'interface, une
confirmation au CLI.

---

## L'idée centrale : imposer la pose plutôt que la subir

La génération ne produit pas une image quelconque qu'il faudrait ensuite analyser.
Elle produit un concept dans une **pose de référence connue à l'avance**, guidé
par un squelette OpenPose passé en ControlNet. Les générateurs 3D reconstruisent
nettement mieux une A-pose propre qu'une pose arbitraire, et l'agent qui riggera
le mesh trouve des membres dégagés. L'estimation de pose (RTMPose, locale)
vérifie qu'un concept tient la pose avant qu'on paie sa 3D.

---

## Le rig : un agent, une convention

Le pivot est la **convention de nommage des os** (`hips`, `upper_arm.L`,
`shin.R`…, `domain/skeleton.py`). L'agent qui rigge un corps qui s'y prête
(bipède, quadrupède, bipède ailé) l'emploie ; ailleurs, il nomme ses os ou ses
pièces en clair. À la livraison, le studio mesure la part de noms canoniques —
une mesure, pas une condition. Un cycle se nomme `<nom>_loop` : Godot le fait
boucler à l'import et l'appelle `<nom>`, comme les planches de sprites.

---

## Prérequis

Le studio tourne **sous Linux** : la coquille emploie `prctl` et `setsid`, la
mise à jour passe par `make`. macOS et Windows ne sont pas pris en charge.

| Outil | Version | Sert à | |
|---|---|---|---|
| Python | ≥ 3.11 | le serveur, le CLI, le serveur MCP | requis |
| Node.js | ≥ 20.19 ou ≥ 22.12 (Vite 8) | construire le front | requis pour l'application |
| Rust (`rustup`) | ≥ 1.77 | la coquille Tauri | requis pour l'application |
| webkit2gtk-4.1, libayatana-appindicator | — | la fenêtre, l'icône de la barre système ; avec les autres [prérequis Linux de Tauri](https://v2.tauri.app/start/prerequisites/#linux) | requis pour l'application |
| Blender | ≥ 4.2 (CI : 5.2.1) | sprites, inventaire d'un GLB, test de fumée ; l'agent qui rigge y travaille | requis pour la 3D |
| Godot | 4 (CI : 4.4) | validation headless, rendus du jeu, vitrines, banc de direction artistique, éditeur d'écran | requis pour Godot |
| gamescope ou xvfb-run | — | dessiner Godot hors écran (sans eux, une fenêtre s'ouvre un instant à chaque rendu) | recommandé |
| git | — | l'éditeur d'écran (une branche par écran) | requis pour l'éditeur |
| ffmpeg | — | extraire les frames d'une vidéo de référence | facultatif |
| extra `rigtools` | `make install-rigtools` | pose RTMPose et détourage locaux (rtmlib, onnxruntime, rembg) | facultatif |
| extra `vector` | `.venv/bin/pip install -e '.[vector]'` | rastériser une icône SVG en PNG (cairosvg) | facultatif |

Les clés vont dans `.env` : `RUNWARE_API_KEY` pour toute génération payante,
`TRIPO_API_KEY` (facultative) pour l'API Tripo en direct. Sans clé, tout ce qui
est local fonctionne.

---

## Installation

Le studio se lance **depuis son dépôt** : il n'y a pas de paquet à installer.

```bash
git clone <le dépôt> && cd gamestudio
make install                 # .venv + dépendances Python
make install-app             # dépendances du front (npm)
make install-rigtools        # facultatif : pose RTMPose et détourage locaux
cp .env.example .env         # y mettre RUNWARE_API_KEY (et TRIPO_API_KEY)
make link                    # `gamestudio` dans ~/.local/bin
gamestudio doctor            # ce qui marche ici, ce qui manque, et quoi faire
```

La première `make studio` compile la coquille — quelques minutes ; les
suivantes démarrent aussitôt. `gamestudio doctor --check` sort en erreur s'il y
a un vrai blocage, `--json` rend le rapport brut. Sans `make link`, la commande
est `.venv/bin/gamestudio`.

`gamestudio` trouve la racine du studio en remontant jusqu'au premier `.env`,
comme `git` trouve son dépôt. **Depuis le dossier d'un jeu**, qui n'en a pas,
il faut lui dire où est le studio — une fois pour toutes, dans le profil du
shell :

```bash
export GAMESTUDIO_HOME=/chemin/vers/gamestudio
```

Blender se trouve dans le `PATH`, ou par `BLENDER_BIN` dans `.env`.
`gamestudio mesh providers` dit quelles voies 3D sont prêtes et à quel prix,
`gamestudio mesh balance` ce qui reste sur le compte Tripo (lecture seule,
gratuite).

**Première mise en route** : [`docs/getting-started.md`](docs/getting-started.md)
(en anglais) donne l'ordre des gestes et dit où chaque chose atterrit.

L'interface parle anglais ou français : la langue se choisit au premier
lancement, puis dans Pilotage › Ce poste.

---

## Utilisation

### Dans l'application

```bash
make studio           # la fenêtre s'ouvre, le terminal rend la main
```

1. Sélecteur de workspace (en tête du rail) › **Ouvrir un dossier…** : le
   dossier du jeu devient le projet. Tout ce que le studio tient pour ce jeu
   ira dans son `.gamestudio/`.
2. **Le monde** › « + » : déclarer une rubrique (Personnages, Bâtiments…), puis
   y écrire la fiche d'une entité — ce qu'elle est, à quoi elle sert, son
   apparence.
3. L'**atelier** de la fiche enchaîne **Concepts** (des lots amorcés par la
   fiche ; on en retient un), **3D** (le mesh, payant et confirmé, ou un `.glb`
   forgé en local), **Animer** (le brief confié à un agent, qui rigge et anime
   dans Blender), puis **Sprites** et **Exporter**.

`make studio` détache l'application : fermer le terminal ne ferme pas le
studio. La fenêtre lance elle-même le serveur Python sur un port libre et
l'arrête en partant — aucun processus ne lui survit, y compris si elle est tuée
par un signal. Les traces vont dans `data/studio.log`. `make desktop-entry`
ajoute l'entrée au menu d'applications.

L'application suit le code : à chaque démarrage, elle demande au `Makefile` si
elle est à jour (`make update-check`) et, sinon, se reconstruit dans une
fenêtre de mise à jour avant de s'ouvrir.

### En ligne de commande

```bash
export GAMESTUDIO_HOME=/chemin/vers/gamestudio
cd ~/jeux/mon-jeu && gamestudio init mon-jeu   # crée .gamestudio/recipe.yaml

# 1. Trouver la direction artistique (payant)
gamestudio style explore .gamestudio/recipe.yaml -s "a knight in leather armour" --confirm
# retenir 10 à 20 images, puis :
gamestudio style train .gamestudio/recipe.yaml -i <id> -i <id> ... --confirm
# reporter lora_air et trigger_word dans la recipe : le style est figé

# 2. Fabriquer le roster : références en A-pose, meshes nus (payant)
gamestudio build .gamestudio/recipe.yaml --confirm
gamestudio status .gamestudio/recipe.yaml
```

Le rig et les animations passent par la fiche de l'entité. Une entité
construite par `build` n'en a pas encore : Pilotage › « Donner une fiche » la
rattache à une rubrique du monde. Une fois la fiche passée en 3D,
`gamestudio world brief <projet> <rubrique> <fiche>` écrit le brief que l'agent
suit (c'est ce que fait « Animer » dans l'atelier). Le GLB livré se valide par
Godot :

```bash
gamestudio validate-godot . --scene res://characters/<entité>/<entité>.glb
```

Autres points d'entrée :

```bash
make studio-dev               # développement : Vite en rechargement à chaud
gamestudio worker             # worker seul (autre machine, ou dédié au rendu)
gamestudio mcp                # serveur MCP pour un agent (worker intégré)
gamestudio serve              # le serveur seul, sans fenêtre
```

Pourquoi un thread et pas un second processus : le worker ne calcule presque
rien lui-même, il attend Blender (`subprocess`) et le réseau (`httpx`), qui
libèrent tous deux le GIL. L'interface reste réactive pendant un rendu. Les
séparer n'a d'intérêt que pour isoler réellement les deux — machine dédiée, ou
redémarrage sans interrompre un entraînement.

### La recipe

Un fichier YAML, `.gamestudio/recipe.yaml`, décrit **ce qu'on veut**, pas comment
le fabriquer :

```yaml
version: 1
project: mon-jeu
style:
  prompt_prefix: "hand-painted game character, muted earth palette"
  lora_air: gamestudio:mon-jeu-style@1     # une fois entraînée
characters:
  - id: garde
    subject: "a forest ranger in a green hooded cloak, short bow"
    pipelines: [mesh3d]
```

C'est l'unité de ré-exécution. Chaque étape porte une empreinte calculée sur ses
entrées : modifier une entité et relancer ne recalcule que celle-là. Ce qui
compte quand une étape coûte 0,40 $ ou dix minutes. Tous les champs :
[`docs/recipe.md`](docs/recipe.md) (en anglais).

---

## L'application de bureau

Une coquille **Tauri** (moins de 2 000 lignes de Rust) et un front **React**. Le
Rust choisit un port libre, y lance le serveur Python et l'arrête en partant ;
il ouvre les fenêtres (le studio, les Chats, la mise à jour), tient l'icône de
la barre système et la reconstruction. Tout le métier reste en Python, dans
`service/`, partagé mot pour mot avec le serveur MCP et le CLI — une opération
y est définie une fois, et les trois interfaces la servent.

L'interface est dense et sombre, et la couleur y porte un sens et un seul : une
seule famille vive, de l'orange à l'or, pour l'action, la sélection et le
focus ; l'émeraude pour ce qui est terminé ou connecté ; l'ambre pour ce qui
coûte ou attend une relecture ; le rouge pour ce qui est détruit ou a échoué.
La seule surface claire est réservée aux œuvres : elle sert à juger une image.

Le rail range les pages par intention :

- **Découvrir** — **Pilotage** : ce qui est à faire maintenant (échecs, roster à
  construire, sorties à relire, outils absents), le projet actif (entités,
  journal des tâches, recipe, coût) et le poste (diagnostic, connexions MCP,
  procédures, langue). **Contexte** : ce que tout agent lit en arrivant.
- **Équipe › Au travail** — les agents qui travaillent, chacun dans son
  terminal, en direct : on les regarde sans les piloter.
- **Le monde** — vide d'office : les rubriques que l'utilisateur déclare.
  Chaque fiche ouvre son **atelier** (Fiche → Concepts → 3D). L'étape 3D est un
  viewport : navigation de Blender (`1/3/7` face/profil/dessus, `F` pour
  cadrer), squelette en surimpression, animations du GLB, turntable.
- **Game design** — **Mécaniques** ; **Interface**, dont chaque fiche d'écran
  montre le rendu du jeu par Godot et s'édite sur une branche git à elle, avec
  **Icônes** et **Props**, vitrines des éléments du jeu ; **VFX**, le concept
  d'un effet, confié à un agent qui le construit dans Godot ; **Direction
  artistique**, chaque shader du jeu en échantillon vivant.
- **Développement** — **Idées**, **Notes**, **Devlog**, **Documents** (les textes
  du projet), **Bibliothèque** (tout ce que le projet a produit) et
  **Comparer** (deux images ou deux meshes sous un même regard).
- **Chats** — une fenêtre à part : un onglet par agent (Claude Code, Codex,
  Kimi Code…), lancé dans le dossier du projet avec les outils du studio.

### Du mesh 3D aux sprites 2D

Depuis l'étape 3D d'une fiche, l'outil « Sprites » rend un mesh sous N directions et en
fait une planche par animation et par direction, plus un atlas JSON. C'est
**gratuit** : Blender headless en local, aucun appel réseau. Le résultat se
range dans `3d/<entité>/sprites/`.

Trois styles, qui diffèrent par le moteur autant que par la lumière :

| style | ce qu'il fait | quand le choisir |
|---|---|---|
| **normal** | Workbench en lumière plate, couleurs exactes de la texture | la direction artistique vient déjà de l'image 2D ; c'est aussi le plus rapide |
| **prérendu** | EEVEE, éclairage trois points, ombres et occlusion, rendu au double puis réduit | on veut du volume cuit dans le sprite — le pré-calculé façon Diablo |
| **pixel art** | aucun anticrénelage, rendu à la taille cible, palette réduite | le sprite doit rester net à la loupe |

Deux règles portent la qualité du résultat, et découlent de la même idée : ce
qui définit le personnage se calcule **une fois pour toutes ses frames et toutes
ses directions**, jamais image par image.

- Le **recadrage** est commun : rogner chaque frame sur son propre contenu ferait
  sautiller le personnage, puisque son pivot bougerait.
- La **palette** du style pixel est commune : une palette par frame ferait sauter
  les couleurs d'un angle à l'autre.

L'élévation choisit le genre : `0°` vue de côté (plateformer), `30-45°`
isométrique (RPG, tactique), `90°` vue de dessus. Un mesh sans animation donne un
tour d'horizon — une frame par direction, ce qu'on veut d'un objet ou d'un décor.

### Piloter le studio avec un agent

Le fichier `.mcp.json` du dépôt connecte Claude Code au serveur MCP du studio
(`gamestudio mcp`) ; `.codex/config.toml` et `.gemini/settings.json` font de
même pour Codex et Gemini. L'agent peut inspecter l'état, regarder les images
produites, mettre en file des constructions, découper une planche, et suivre le
brief qui lui confie le rig et les animations d'une entité — les opérations
payantes exigent une confirmation explicite. Le serveur embarque son propre
worker : rien d'autre à lancer. Voir `CLAUDE.md`.

Les mêmes fichiers déclarent deux compagnons facultatifs, `blender`
(blender-mcp) et `godot` (godot-mcp), par les lanceurs `scripts/mcp/`. Ce sont
des projets tiers, que vous installez vous-même si vous les voulez ; le studio
fonctionne sans eux (skill `blender-godot-bridge`).

#### Depuis un autre projet — un dépôt de jeu, par exemple

C'est le cas utile : l'agent qui travaille sur le jeu veut piocher dans la
bibliothèque du studio. Il suffit d'un `.mcp.json` dans ce dépôt-là, avec le
chemin absolu du binaire et **une seule variable**, `GAMESTUDIO_HOME` :

```json
{
  "mcpServers": {
    "gamestudio": {
      "command": "/chemin/vers/gamestudio/.venv/bin/gamestudio",
      "args": ["mcp", "--no-worker"],
      "env": { "GAMESTUDIO_HOME": "/chemin/vers/gamestudio" }
    }
  }
}
```

`GAMESTUDIO_HOME` désigne l'installation du studio : le `.env` qui s'y trouve
fournit le reste — dossier de données, clés, Blender. Sans elle, le serveur
chercherait un `.env` au-dessus du répertoire courant, donc au-dessus du dépôt
de jeu, et ne trouverait ni les assets ni la clé.

`--no-worker` est le bon réglage depuis un dépôt de jeu : l'agent lit et met en
file, mais c'est le studio qui produit. Retirez-le pour qu'il produise aussi.

### Le stockage, inspectable

**Un projet est un dossier** : tout ce que le studio tient pour un jeu vit dans
son `.gamestudio/` — recipe, documents, contexte, base, store, bibliothèque,
rapport, paquets — et rien n'est jamais mélangé entre projets. Le studio, lui,
n'y recopie jamais son moteur (code, outils, skills, clés). Le store adresse les
fichiers par hash (`.gamestudio/assets/`) ; la **bibliothèque**
(`.gamestudio/library/`) en est le miroir lisible, en liens durs. Un
`.gitignore` laisse passer les seuls textes. Le rangement ne mélange pas
davantage les dimensions :

- `generations/<date>_<prompt>_<lot>/` — chaque génération libre a son dossier,
  avec ses images et un `batch.json` (prompt, modèle, date) ;
- `2d/<entité>/` — `concept.png` et `concepts/<lot>/`, tant que l'entité n'a
  pas de mesh ;
- `3d/<entité>/` — `<entité>.glb` (nu, ou riggé et animé par l'agent),
  `<entité>-bare.glb` (le mesh d'origine), `sprites/`, `concept.png` ;
- `icons/<planche>/` — une planche découpée, un fichier par élément (icônes SVG
  ou PNG, frames d'une spritesheet), plus un `sheet.json` (source, stratégie
  de découpe, cadres) ;
- `effects/<effet>/` — le dernier rendu d'un effet visuel ;
- `renders/` et `briefing/<rubrique>/` — les scènes du jeu dessinées par Godot ;
- `style/` — les références de la direction artistique.

Resynchronisée par le worker après chaque production (générations comprises),
ou à la main : `gamestudio library sync <projet>`.

**C'est cette arborescence qu'on donne à un autre outil ou à un agent**, jamais
le store : un chemin de bibliothèque se lit et reste stable, un hash non.

```bash
gamestudio library ls mon-jeu                          # les dossiers du projet
gamestudio library ls mon-jeu icons/mon-pack           # le contenu d'une planche
gamestudio library ls mon-jeu icons/mon-pack --paths   # un chemin absolu par ligne
gamestudio library path mon-jeu icons                  # le chemin du dossier, brut
```

Un agent branché au serveur MCP dispose de l'équivalent en un appel :
`library_tree(projet, folder, depth, pattern)` renvoie sous-dossiers et fichiers
avec leur **chemin absolu**, leur asset et leur taille — et resynchronise au
passage, donc les chemins renvoyés existent. Chaque dossier de planche ou de lot
porte en plus un JSON (`sheet.json`, `batch.json`) qui dit d'où vient
son contenu : un agent sait ainsi *ce qu'il ouvre*, pas seulement où c'est.

Ce rangement est décrit une seule fois, par `Librarian.index_project` : le
disque en est l'écriture, la page **Bibliothèque** en est la lecture. Elle
montre les fichiers du projet filtrés par média (2D, 3D) et par nature
(concepts, références, meshes, sprites, images libres, planches…), avec une
recherche par nom ; un mesh s'y regarde en 3D, et chaque pièce mène à l'étape
de son atelier où elle se reprend, ou au comparateur. Le clic droit offre
« Copier le chemin », « Afficher dans le dossier » et « Supprimer » ; le
panneau d'une pièce, « Renommer ou supprimer ». Tout n'est pas modifiable, et
c'est volontaire :

| | Renommer, ranger ailleurs | Supprimer |
|---|---|---|
| Morceau de planche (icône, frame) | oui | oui — base, store et miroir |
| Image libre (`generations/`) | non | oui |
| Ce qu'une entité ou un style tient (concept, mesh, sprite, référence) | refusé, avec le nom de ce qui le retient | refusé |

Un agent renomme et range (`library_rename`, `library_move`) ; supprimer reste
un geste de l'utilisateur. Comme le miroir est l'écriture exacte de l'index, un
élément supprimé, renommé ou rangé ailleurs ne laisse rien derrière lui sur le
disque.

### Découper une planche multi-éléments (SVG ou PNG)

Un jeu consomme ses assets un par un ; un pack d'icônes ou une spritesheet
arrivent en un seul fichier. La Bibliothèque a donc un bouton **« Importer une
planche… »** : le fichier (SVG, PNG, JPEG, WebP), une stratégie (`auto` par
défaut), un écart (`gap`, automatique par défaut) et le détourage local.
**Inspecter** annonce, sans rien écrire, combien d'éléments seront extraits et
sous quels noms ; **Importer** écrit un fichier autonome par élément sous
`icons/<planche>/`. Un agent fait de même avec `inspect_sheet` puis
`import_sheet`, qui prennent en plus `rows` et `columns` (grille jointive),
`keep` (ne retenir que certains éléments) et `raster_size` (un PNG par icône
SVG) — skill `sheet-split`.

**Une seule règle sépare les éléments**, quel que soit le média : *deux morceaux
qui se touchent appartiennent au même dessin.* Reste à savoir à partir de quel
écart deux morceaux cessent de se toucher — et cet écart, la planche le dit
elle-même : les vides internes d'un dessin et les vides entre deux dessins
forment deux populations distinctes, séparées par un saut franc. Le seuil est
donc lu dans cette distribution (coupure d'Otsu sur les écarts de l'arbre
couvrant minimal), pas fixé à l'avance. Une constante se serait trompée à
chaque changement d'échelle.

Il reste réglable à la main (`gap`) quand une planche sort de l'ordinaire :
l'augmenter recolle un dessin éclaté, le baisser sépare deux dessins collés.

Ce qui change d'un média à l'autre, c'est seulement *comment on lit la planche* :

| | SVG | PNG, JPEG, WebP |
|---|---|---|
| Ce qu'on lit | la structure du document | les pixels |
| Découpe | `<symbol>`, un `<g>` par icône, ou les `<path>` à plat d'un optimiseur | composantes connexes sur le premier plan |
| Fond | sans objet | alpha, sinon détourage local (BiRefNet) ou couleur dominante des bords |
| Sortie | SVG autonome : viewBox ajustée, transformations aplaties, styles hérités recopiés, seules les définitions référencées | PNG RGBA découpé |

Les planches bitmap ont en plus deux régimes, choisis automatiquement. Une
**grille de frames** est reconnue à la régularité de ses gouttières : toutes ses
cases reçoivent alors **un seul et même cadre**, l'union de leurs contenus —
elles gardent donc la même taille, sans traîner les marges de la planche.
Rogner chaque frame sur son propre contenu ferait sauter le pivot du personnage
d'une image à l'autre : c'est le piège que `sprites.py` évite déjà à
l'assemblage. Un **pack libre** d'icônes, lui, est rogné élément par élément. Si
les cases se touchent, aucune gouttière ne les trahit : on impose alors `rows`
et `columns`.

Un piège propre au bitmap mérite d'être connu : sur fond sombre, beaucoup de
planches portent un **halo** autour de chaque dessin — trop proche du fond pour
être un dessin, trop loin pour être retiré avec lui. Il relie alors les voisins
et la planche ne fait plus qu'un seul morceau. Le symptôme est net (un morceau
couvre une bonne part de la planche), donc la correction est automatique : la
tolérance du fond s'élargit jusqu'à ce que les dessins se détachent, et le
rapport de découpe le dit.

Le nom de chaque fichier vient de la planche quand elle en porte un (`id`,
`data-name`, `<title>` d'un SVG) ; les identifiants d'outil (`path12`,
`Layer_1`) sont ignorés au profit de la position dans la grille, comme les
`icon-01…` et `frame-01…` d'une planche bitmap. Aucun appel réseau, aucun coût.

### Une image venue d'ailleurs

Un dessin, un scan ou le concept d'un artiste se traite comme un concept du
studio : il entre par `import_image` (détourage local en option), sa pose se
mesure avec `detect_pose` (extra `rigtools`) avant de payer quoi que ce soit, puis
il devient le concept retenu d'une fiche et passe en 3D comme les autres. La
procédure est le skill `external-image`.

---

## Architecture

La carte du code — un fichier par ligne, avec ce qu'il fait — est
[`context/codemap.md`](context/codemap.md), régénérée depuis l'en-tête de chaque
fichier et vérifiée par `make check`. C'est le point de départ d'une
modification ; les procédures des agents sont dans `.claude/skills/`.

**Pourquoi une couche `service/`.** Sans elle, le CLI, l'API et le serveur MCP
feraient chacun le même travail, avec trois occasions de diverger. Une opération
y est définie une fois et rendue dans sa langue par chaque interface : un
code HTTP pour l'API, un message lisible pour un agent. C'est aussi ce qui rend
les garde-fous de coût réellement uniformes — une opération payante refuse
partout, ou nulle part.

**Pourquoi Tauri et pas Electron.** La fenêtre utilise le moteur web du système
(WebKitGTK) au lieu d'embarquer un Chromium : l'application pèse quelques
mégaoctets au lieu de cent cinquante. Et le Rust reste une coquille — le jour où
le serveur Python change, rien à recompiler.

**Pourquoi Blender en backend et pas en interface.** Un addon Blender enfermerait
tout dans son UI et rendrait le traitement par lots pénible. Blender headless
derrière une API, en revanche, c'est exactement le bon usage pour ce qui doit
être exact et répétable : le rendu des sprites, l'inventaire d'un GLB. Le
Blender ouvert reste celui de l'agent qui rigge, et le vôtre.

**Pourquoi SQLite et pas Postgres + Redis.** Le studio tourne sur un poste de
travail, un worker suffit, et le mode WAL supporte très bien un producteur et
quelques lecteurs. Une dépendance de moins à faire tourner.

---

## Vérification

```bash
make check                  # ruff + pytest + skills + carte du code + types et traductions du front
python scripts/smoke_3d.py  # livraison d'agent simulée, exige Blender ; Godot valide si présent
```

Ni l'un ni l'autre n'appelle le réseau. `make check` échoue si les dépendances
du front manquent (`make install-app`).

Le test de fumée tient lieu de l'agent : Blender fabrique un personnage riggé
(un cycle `idle_loop`, un geste `wave`) et une machine sans os dont une roue
tourne. Le studio en relève os et animations, rend une planche par animation et
par direction sous un seul cadrage, écrit les ressources Godot, puis **lance
Godot en headless pour les valider** : chaque animation jouée, chaque piste
résolue, les cycles en boucle et seulement eux. C'est la seule façon de
garantir qu'un fichier écrit hors de l'éditeur est réellement chargeable.

Les mêmes vérifications tournent en CI sur chaque push et chaque pull request
(voir [`docs/ci.md`](docs/ci.md), en anglais) — sans clé d'API et sans dépense possible.

---

## Limites connues

- **Linux seulement.** La coquille et la mise à jour reposent sur des appels
  propres à Linux ; il n'y a pas de paquet, le studio se lance depuis son dépôt.
- **Le rig vaut ce que vaut l'agent qui le fait.** Un critique doit le regarder
  (planches de sprites, captures) avant qu'on le valide : un rig qui se charge
  n'est pas un rig qui se déforme bien.
- **La topologie des meshes générés est irrégulière.** Acceptable pour du
  prototype et pour le rendu vers sprites ; à retopologiser pour de la 3D temps
  réel exigeante (Tripo P2 en direct sort du quad natif).
- **La 3D ne lit qu'une vue.** Les prompts `multiview` et `turnaround` produisent
  les autres angles, mais le mesh n'est généré que depuis le concept retenu.
- **Une segmentation en pièces sort sans texture** quand elle vient de Tripo
  (`generateParts`, incompatible avec la texture) : un bâtiment à animer par
  pièces se segmente plutôt dans Blender, par l'agent.

---

## Coûts indicatifs

| Étape | Ordre de grandeur |
|---|---|
| Image de référence (FLUX.1 [dev], 768×1152) | ~0,006 $ |
| Exploration de style (8 images, FLUX.1 schnell) | ~0,01 $ |
| Entraînement LoRA (1000 pas, FLUX.1 [dev]) | ~1,45 $ |
| Mesh 3D par Runware (TRELLIS.2, Hunyuan 3D 3.1, Tripo v3.1 par défaut à 0,40 $) | 0,15 à 0,50 $ |
| Mesh 3D par l'API Tripo en direct (H3.1, P1, P2) | 0,30 à 1,25 $ selon le modèle et la texture |
| Rig et animations (un agent, dans Blender) | aucun coût de génération — le quota de l'agent |
| Mesh forgé en local, sprites, inventaire, export | 0 $ |

Les prix exacts de chaque modèle sont dans `gamestudio mesh providers` et dans
le dialogue qui confirme chaque dépense. Un roster de vingt entités en 3D avec
le modèle par défaut coûte environ 8 $, plus l'entraînement du style.

---

## Licence

Le code est public sans être open source au sens de l'OSI : le studio est sous
**PolyForm Shield 1.0.0** (`LICENSE`).

- **Permis** : l'utiliser, l'étudier, le modifier et le redistribuer, y compris
  pour produire les assets d'un jeu vendu.
- **Interdit** : en tirer un produit qui concurrence le studio (logiciel,
  service, plugin), qu'il soit gratuit ou payant.
- **Matériel tiers** : il garde sa propre licence (`THIRD_PARTY_NOTICES.md`).
  img2threejs est sous Apache 2.0, les scripts exécutés dans Blender sous GPL 3.0
  ou ultérieure, les polices Outfit et Prompt sous OFL 1.1, les logos des agents
  (tracés de LobeHub lobe-icons) sous MIT — les marques restent à leurs
  détenteurs.
- **Modèles téléchargés à l'exécution** : ils ne sont pas dans le dépôt. Les
  outils locaux facultatifs tirent au premier usage BiRefNet (détourage, par
  rembg : MIT) et RTMPose avec YOLOX (pose, par rtmlib : Apache 2.0).
- **Ce que le studio produit** : les images et maillages générés relèvent des
  conditions de Runware, de Tripo et des modèles employés, pas de cette licence.
  En particulier, une LoRA entraînée par `style train` dérive de FLUX.1 [dev] et
  relève de sa licence non commerciale (FLUX.1 [dev] Non-Commercial License) :
  la lire avant tout usage commercial de la LoRA.

Pour un usage que la licence ne permet pas, demander une licence à l'auteur.
Contribuer : [`CONTRIBUTING.md`](CONTRIBUTING.md) (en anglais). Signaler une
faille : [`SECURITY.md`](SECURITY.md).
