-- Lem — schéma SQLite.
--
-- Trois couches :
--   1. LEXIQUE   (statique, reconstruit par l'ingestion)        : entree, forme, sens, concept, domaine, morpheme, relation, embedding
--   2. JOURNAL   (ajout seul, la seule vérité sur l'utilisateur)  : session, evenement, notation, mnemo
--   3. DÉRIVÉ    (caches recalculables à partir du journal)       : trace_etat, connaissance, theta
--
-- Règle d'or : on n'écrit jamais d'opinion dans la couche 3 ; on la recalcule.

PRAGMA foreign_keys = ON;

------------------------------------------------------------------------------
-- 1. LEXIQUE
------------------------------------------------------------------------------

-- Toute unité lexicale : mot, mot composé, expression, nom propre, sigle, mot étranger.
-- « les sages de la rue Montpensier » est une entrée de type 'expression'.
CREATE TABLE IF NOT EXISTS entree (
    id          INTEGER PRIMARY KEY,
    forme       TEXT NOT NULL,                 -- lemme / forme canonique
    langue      TEXT NOT NULL DEFAULT 'fr',
    pos         TEXT NOT NULL DEFAULT '',      -- nom, verbe, adj, adv, expr, nom_propre…
    type        TEXT NOT NULL DEFAULT 'mot',   -- mot | compose | expression | nom_propre | sigle | etranger
    zipf        REAL,                          -- fréquence (échelle Zipf, 0 = absent des corpus)
    phon        TEXT,                          -- transcription phonétique (Lexique ou API)
    syllabes    TEXT,                          -- 'a-cro-nyme'
    origine     TEXT,                          -- grec, latin, anglais, onomatopée…
    etymologie  TEXT,
    UNIQUE (forme, langue, pos)
);
CREATE INDEX IF NOT EXISTS idx_entree_forme ON entree(forme);

-- Formes fléchies : ce que les jeux acceptent comme réponse.
CREATE TABLE IF NOT EXISTS forme (
    forme     TEXT NOT NULL,
    norme     TEXT NOT NULL,                 -- minuscules sans accents (comparaison tolérante)
    entree_id INTEGER NOT NULL REFERENCES entree(id) ON DELETE CASCADE,
    PRIMARY KEY (forme, entree_id)
);
CREATE INDEX IF NOT EXISTS idx_forme_entree ON forme(entree_id);
CREATE INDEX IF NOT EXISTS idx_forme_norme ON forme(norme);

-- Un concept = une idée que plusieurs entrées peuvent désigner
-- (« Conseil constitutionnel » ← « les sages de la rue Montpensier »).
CREATE TABLE IF NOT EXISTS concept (
    id    INTEGER PRIMARY KEY,
    label TEXT NOT NULL
);

-- Polysémie : une entrée a un ou plusieurs sens. La mémoire est suivie par sens.
CREATE TABLE IF NOT EXISTS sens (
    id                 INTEGER PRIMARY KEY,
    entree_id          INTEGER NOT NULL REFERENCES entree(id) ON DELETE CASCADE,
    rang               INTEGER NOT NULL DEFAULT 1,   -- 1 = sens principal
    definition         TEXT NOT NULL,
    definition_precise TEXT,                         -- définition non ambiguë (cartes, mots fléchés), générée hors ligne
    concept_id         INTEGER REFERENCES concept(id),
    exemple            TEXT,
    registre           TEXT                          -- littéraire, soutenu, familier, vieilli…
);
CREATE INDEX IF NOT EXISTS idx_sens_entree ON sens(entree_id);

-- Domaines : hiérarchie (métallurgie ⊂ science) et appartenance multiple (chevauchements).
CREATE TABLE IF NOT EXISTS domaine (
    id        INTEGER PRIMARY KEY,
    nom       TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES domaine(id)
);
CREATE TABLE IF NOT EXISTS sens_domaine (
    sens_id    INTEGER NOT NULL REFERENCES sens(id) ON DELETE CASCADE,
    domaine_id INTEGER NOT NULL REFERENCES domaine(id) ON DELETE CASCADE,
    PRIMARY KEY (sens_id, domaine_id)
);

-- Morphèmes : acro-, -onyme, chrono-… Ce sont des leviers : un morphème appris débloque une famille.
CREATE TABLE IF NOT EXISTS morpheme (
    id      INTEGER PRIMARY KEY,
    forme   TEXT NOT NULL,          -- 'acro-', '-onyme', 'chron'
    glose   TEXT NOT NULL DEFAULT '',
    origine TEXT,
    UNIQUE (forme, glose)
);
CREATE TABLE IF NOT EXISTS entree_morpheme (
    entree_id   INTEGER NOT NULL REFERENCES entree(id) ON DELETE CASCADE,
    morpheme_id INTEGER NOT NULL REFERENCES morpheme(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entree_id, morpheme_id)
);

-- Relations entre entrées. type :
--   synonyme | antonyme | hyperonyme | paronyme | association (JeuxDeMots)
--   voisin_semantique | voisin_formel        (calculés)
--   confusion_perso                          (calculé à partir du journal : TES confusions)
CREATE TABLE IF NOT EXISTS relation (
    source_id INTEGER NOT NULL REFERENCES entree(id) ON DELETE CASCADE,
    cible_id  INTEGER NOT NULL REFERENCES entree(id) ON DELETE CASCADE,
    type      TEXT NOT NULL,
    poids     REAL NOT NULL DEFAULT 1.0,
    origine   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, cible_id, type)
);
CREATE INDEX IF NOT EXISTS idx_relation_cible ON relation(cible_id, type);

-- Vecteurs (bge-m3, 1024 dimensions, float16) pour les entrées et les sens.
CREATE TABLE IF NOT EXISTS embedding (
    objet    TEXT NOT NULL,          -- 'entree' | 'sens'
    objet_id INTEGER NOT NULL,
    modele   TEXT NOT NULL,
    vecteur  BLOB NOT NULL,
    PRIMARY KEY (objet, objet_id, modele)
);

------------------------------------------------------------------------------
-- 2. JOURNAL (ajout seul)
------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session (
    id       INTEGER PRIMARY KEY,
    jeu      TEXT NOT NULL,
    params   TEXT NOT NULL DEFAULT '{}',  -- json : paramètres exacts du jeu
    debut    TEXT NOT NULL,
    fin      TEXT,
    contexte TEXT NOT NULL DEFAULT '{}'   -- json : heure locale, auto-évaluation de fatigue…
);

-- Un événement = un stimulus montré ou une réponse donnée, dans n'importe quel jeu.
-- C'est la matière première de TOUT : FSRS, carte de connaissance, cartographie de tes confusions.
CREATE TABLE IF NOT EXISTS evenement (
    id                 INTEGER PRIMARY KEY,
    ts                 TEXT NOT NULL,              -- ISO 8601 UTC, millisecondes
    session_id         INTEGER REFERENCES session(id),
    jeu                TEXT NOT NULL,
    type               TEXT NOT NULL,              -- stimulus | reponse | indice | revelation | vie | auto_note
    entree_id          INTEGER REFERENCES entree(id),
    sens_id            INTEGER REFERENCES sens(id),
    trace              TEXT,                       -- recep | prod | forme
    reponse            TEXT,
    correct            INTEGER,                    -- 1 / 0 / NULL (non jugé)
    note_auto          INTEGER,                    -- 1..4, note provisoire (algorithmique, en direct)
    latence_ms         INTEGER,                    -- stimulus → validation
    premiere_frappe_ms INTEGER,                    -- stimulus → première touche
    frappes            INTEGER,
    effacements        INTEGER,
    data               TEXT NOT NULL DEFAULT '{}'  -- json : candidats montrés, similarités, étiquettes…
);
CREATE INDEX IF NOT EXISTS idx_evenement_entree ON evenement(entree_id, trace);
CREATE INDEX IF NOT EXISTS idx_evenement_session ON evenement(session_id);

-- Notation différée (Claude en mode headless aujourd'hui, Jev demain). Versionnée, jamais écrasée.
CREATE TABLE IF NOT EXISTS notation (
    id                    INTEGER PRIMARY KEY,
    evenement_id          INTEGER NOT NULL REFERENCES evenement(id),
    correcteur            TEXT NOT NULL,           -- 'embedding:bge-m3' | 'claude:sonnet' | 'jev:1.13'
    ts                    TEXT NOT NULL,
    note                  INTEGER,                 -- 1..4 (échelle FSRS)
    precision             INTEGER,                 -- 1..5 (échelle de précision, cf. grading/taxonomie.py)
    sens_vise_id          INTEGER REFERENCES sens(id),
    erreur                TEXT,                    -- valeur de la taxonomie d'erreurs
    mot_reellement_defini TEXT,
    confiance             REAL,
    brut                  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_notation_evenement ON notation(evenement_id);

-- Mnémotechnie : images mentales (écrites ou dictées), dessins, étymologie personnelle.
CREATE TABLE IF NOT EXISTS mnemo (
    id        INTEGER PRIMARY KEY,
    entree_id INTEGER NOT NULL REFERENCES entree(id),
    sens_id   INTEGER REFERENCES sens(id),
    type      TEXT NOT NULL,                       -- image_mentale | dessin | texte | etymologie
    contenu   TEXT NOT NULL DEFAULT '',
    fichier   TEXT,
    source    TEXT NOT NULL DEFAULT 'manuel',      -- verbal_memory | cartes | manuel
    ts        TEXT NOT NULL
);

------------------------------------------------------------------------------
-- 3. DÉRIVÉ (recalculable : `lem rebuild`)
------------------------------------------------------------------------------

-- État FSRS d'une trace mémorielle. sens_id = 0 pour une trace au niveau du lemme.
CREATE TABLE IF NOT EXISTS trace_etat (
    entree_id  INTEGER NOT NULL REFERENCES entree(id),
    sens_id    INTEGER NOT NULL DEFAULT 0,
    trace      TEXT NOT NULL,                      -- recep | prod
    card_json  TEXT NOT NULL,
    due        TEXT NOT NULL,
    stabilite  REAL,
    difficulte REAL,
    reps       INTEGER NOT NULL DEFAULT 0,
    lapses     INTEGER NOT NULL DEFAULT 0,
    maj        TEXT NOT NULL,
    PRIMARY KEY (entree_id, sens_id, trace)
);
CREATE INDEX IF NOT EXISTS idx_trace_due ON trace_etat(due);

-- Carte de connaissance : P(je connais ce mot), pour TOUT le lexique, testé ou non.
CREATE TABLE IF NOT EXISTS connaissance (
    entree_id INTEGER PRIMARY KEY REFERENCES entree(id),
    p_prior   REAL NOT NULL,       -- a priori « adulte cultivé »
    p_connu   REAL NOT NULL,       -- a posteriori (prior + tes θ + tes observations)
    residu    REAL NOT NULL DEFAULT 0,
    n_obs     INTEGER NOT NULL DEFAULT 0,
    maj       TEXT NOT NULL
);

-- Tes paramètres latents : global, par origine, par domaine, par morphème, par type.
CREATE TABLE IF NOT EXISTS theta (
    dimension TEXT PRIMARY KEY,    -- 'global' | 'origine:grec' | 'domaine:science' | 'morph:-onyme' | 'type:expression'
    valeur    REAL NOT NULL DEFAULT 0,
    n_obs     INTEGER NOT NULL DEFAULT 0,
    maj       TEXT NOT NULL
);

-- Réglages persistants (calibrage du prior, paramètres FSRS optimisés, poids d'intérêt par domaine).
CREATE TABLE IF NOT EXISTS reglage (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL
);
