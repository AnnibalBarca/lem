# Base de données LEM (instantané du 25/10/2026)

Copie cohérente de `~/.lem/lem.sqlite` (2,71 Go), compressée en xz et découpée en morceaux de 95 Mo.
Elle correspond au code de la branche `local/jeux`.

## Restaurer sur le Mac

```sh
git clone --branch data/lem-db --single-branch --depth 1 git@github.com:AnnibalBarca/lem.git lem-db
cd lem-db
mkdir -p ~/.lem
[ -f ~/.lem/lem.sqlite ] && mv ~/.lem/lem.sqlite ~/.lem/lem.sqlite.ancien
cat lem.sqlite.xz.part-* | xz -d > ~/.lem/lem.sqlite
(cd ~/.lem && shasum -a 256 -c "$OLDPWD/lem.sqlite.sha256")   # doit afficher : lem.sqlite: OK
```

`xz` est fourni par `brew install xz` s'il manque. Les caches (`~/.lem/cache`) se reconstruisent seuls.
