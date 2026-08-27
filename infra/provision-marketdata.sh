#!/usr/bin/env bash
#
# provision-marketdata.sh (rev 2) — crea la baseline market-platform su homelab
#
#   Samsung 870 EVO -> /srv       : tier HOT, tutto /srv/marketdata + il catalogo
#   SanDisk SSD PLUS -> /archive  : tier COLD, backup, sorgente import legacy
#   Toshiba MQ04UBD200 -> /cold   : tier COLD, deep-cold bulk read-mostly
#
# 'deepcold' e' uno storage_root_id, NON un tier nuovo: il contratto del
# catalogo resta tier IN ('hot','cold') e questo disco e' cold come /archive.
# La distinzione fra i due e' operativa (SSD veloce vs HDD da 2TB), non
# semantica, e vive nell'id del root.
#
# Questo script fa SOLO provisioning. Non migra, non sposta e non rimuove nulla
# di preesistente: la dismissione del vecchio recorder e' in dismiss-legacy.sh.
#
# Idempotente. Usa --dry-run per vedere cosa farebbe senza toccare nulla.
#
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SRV_UUID="0b56b5ac-eb32-4e9a-a8b2-5aaba41f8676"   # /srv     (Samsung 870 EVO)
ARC_UUID="4c63f36c-9885-40a6-89e0-a35a6271fc6f"   # /archive (SanDisk SSD PLUS)
DPC_UUID="98f9e6c7-adb9-4858-a671-b941dfb3e4f0"   # /cold    (Toshiba MQ04UBD200)

MD=/srv/marketdata
COLD=/archive/marketdata-cold
DEEPCOLD=/cold/marketdata-deepcold
PGDIR=/srv/databases/postgres-market-catalog
REPO=/opt/market-platform
OWNER_USER=neonix          # utente umano che opera il repo; e' anche Codex

run() {
  if (( DRY_RUN )); then printf '  [dry-run] %s\n' "$*"; else "$@"; fi
}
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[33m   ! %s\033[0m\n' "$*"; }

# dir <path> <owner> <mode> — crea una directory dell'albero marketdata.
# ATTENZIONE: il gruppo e' SEMPRE marketdata, non e' parametrico. Va usata solo
# per directory che il gruppo deve poter leggere. Per tutto il resto (backup,
# datadir di Postgres, repo) si chiama install esplicitamente.
dir() { info "$1  ($2:marketdata, $3)"; run install -d -m "$3" -o "$2" -g marketdata "$1"; }

# acl <path> <spec...> — applica ACL correnti E di default, ricorsivamente.
# Nota: va SEMPRE dopo install/chmod, perche' chmod ricalcola la mask.
acl() {
  local p="$1"; shift
  local s
  for s in "$@"; do
    run setfacl -R -m  "$s" "$p"
    run setfacl -R -dm "$s" "$p"
  done
}

# ---------------------------------------------------------------- preflight
say "Preflight"
[[ $EUID -eq 0 ]] || { echo "Va eseguito come root (sudo)."; exit 1; }

# I device NON sono mai riferiti per nome /dev/sdX: l'enumerazione SATA su questa
# macchina e' gia' cambiata una volta. Si risolvono dal mount point e si
# verificano contro l'UUID atteso.
declare -A DEV
MOUNTS=(
  "/srv:$SRV_UUID:Samsung 870 EVO:SRV"
  "/archive:$ARC_UUID:SanDisk SSD PLUS:ARC"
  "/cold:$DPC_UUID:Toshiba MQ04UBD200:DPC"
)
for spec in "${MOUNTS[@]}"; do
  IFS=: read -r mp uuid label key <<<"$spec"
  mountpoint -q "$mp" || { echo "FATAL: $mp non e' un mount point."; exit 1; }
  src=$(findmnt -no SOURCE "$mp")
  actual=$(blkid -s UUID -o value "$src")
  [[ "$actual" == "$uuid" ]] || {
    echo "FATAL: $mp ($src) ha UUID $actual, atteso $uuid ($label)."
    echo "       Server sbagliato o dischi rimappati. Interrompo."
    exit 1
  }
  DEV[$key]="$src"
  info "$mp -> $src  $label  OK  ($(df -h --output=avail "$mp" | tail -1 | tr -d ' ') liberi)"
done

id -u "$OWNER_USER" >/dev/null 2>&1 || { echo "FATAL: utente $OWNER_USER inesistente."; exit 1; }

if ! command -v setfacl >/dev/null 2>&1; then
  info "installo il pacchetto 'acl' (le ACL sono parte del modello di accesso)"
  run apt-get update -qq
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl
fi

# ------------------------------------------------------------ utenti/gruppi
say "Utenti e gruppi"
#   marketdata     gruppo condiviso: LETTURA su tutto l'albero
#   mkt-collector  scrive raw/, staging/live/, tmp/, imports/
#   mkt-transform  legge raw/, scrive canonical/
#   mkt-agent      legge raw/ e canonical/, scrive features/, fixtures/, staging/dev/
#   neonix (Codex) equiparato a mkt-agent: NON puo' scrivere raw/ ne' canonical/
getent group marketdata >/dev/null || { info "creo gruppo marketdata"; run groupadd --system marketdata; }

for u in mkt-collector mkt-transform mkt-agent; do
  if id -u "$u" >/dev/null 2>&1; then
    info "utente $u gia' presente"
  else
    info "creo utente di sistema $u"
    run useradd --system --no-create-home --shell /usr/sbin/nologin -g marketdata "$u"
  fi
done

info "aggiungo $OWNER_USER al gruppo marketdata (sola lettura sull'albero)"
run usermod -aG marketdata "$OWNER_USER"

# --------------------------------------------- SanDisk: tier cold e backup
say "SanDisk (/archive) — tier COLD"
# I backup NON passano da dir(): quel helper assegna sempre il gruppo
# marketdata, e qui non serve. Un dump del catalogo non ha ragione di essere
# leggibile da collector, transform, agent e neonix — root:root e basta.
info "/archive/backups  (root:root, 0750)"
run install -d -m 0750 -o root -g root /archive/backups
info "/archive/backups/postgres  (root:root, 0750)"
run install -d -m 0750 -o root -g root /archive/backups/postgres
dir /archive/imports            mkt-collector  2750
dir /archive/imports/legacy     mkt-collector  2750
# Il cold tier rispecchia il layout relativo dell'hot tier: le sottodirectory
# le crea il job di tiering, non il provisioning.
dir "$COLD"                     root           2750
acl "$COLD" u:mkt-collector:rwx u:mkt-transform:rwx g:marketdata:rx

# ------------------------------------------------- Samsung: albero dati HOT
say "Toshiba (/cold) — tier COLD, storage root 'deepcold'"
# Stesse ACL di /archive/marketdata-cold: e' lo stesso tier, quindi gli stessi
# attori scrivono e lo stesso gruppo legge. Divergere qui creerebbe due
# politiche di accesso per un'unica semantica.
info "/cold/backups  (root:root, 0750)"
run install -d -m 0750 -o root -g root /cold/backups
info "/cold/backups/postgres  (root:root, 0750)"
run install -d -m 0750 -o root -g root /cold/backups/postgres
dir "$DEEPCOLD"                 root          2750
acl "$DEEPCOLD" u:mkt-collector:rwx u:mkt-transform:rwx g:marketdata:rx

say "Samsung (/srv) — albero marketdata (tier HOT)"
dir "$MD" root 0755

# --- collector-only: raw immutabile, staging live, scratch, import ---------
for d in raw staging staging/live tmp imports imports/legacy; do
  dir "$MD/$d" mkt-collector 2750
done
acl "$MD/raw"          g:marketdata:rx
acl "$MD/imports"      g:marketdata:rx
acl "$MD/staging/live" g:marketdata:rx
acl "$MD/tmp"          g:marketdata:rx

# --- transform-only: canonical e' derivato, non si corregge a mano ---------
for d in canonical canonical/trades canonical/footprint canonical/l2; do
  dir "$MD/$d" mkt-transform 2750
done
acl "$MD/canonical" g:marketdata:rx

# --- agent + neonix/Codex: features, fixtures, staging di sviluppo ---------
for d in features features/trade_microstructure features/footprint_microstructure \
         features/l2_microstructure fixtures staging/dev; do
  dir "$MD/$d" mkt-agent 2750
done
for d in features fixtures staging/dev; do
  acl "$MD/$d" "u:$OWNER_USER:rwx" g:marketdata:rx
done

# --- quarantine: ci scrivono TUTTI gli attori della pipeline ---------------
# mkt-transform e' owner; gli altri tre hanno bisogno di una ACL nominale,
# perche' l'appartenenza a marketdata da' solo r-x.
dir "$MD/quarantine" mkt-transform 2750
acl "$MD/quarantine" u:mkt-collector:rwx u:mkt-agent:rwx "u:$OWNER_USER:rwx" g:marketdata:rx

# ------------------------------------------- bind mount imports/legacy
say "Bind mount imports/legacy -> SanDisk"
FSTAB_SRC=/archive/imports/legacy
FSTAB_TGT="$MD/imports/legacy"
FSTAB_LINE="$FSTAB_SRC $FSTAB_TGT none bind 0 0"

# Non basta cercare il target con grep: lo troverebbe anche in una riga
# commentata o in una vecchia entry sbagliata. findmnt --fstab fa il parsing
# vero del file e ignora i commenti.
existing=$(findmnt --fstab -no SOURCE,FSTYPE,OPTIONS --mountpoint "$FSTAB_TGT" 2>/dev/null || true)
if [[ -z "$existing" ]]; then
  info "aggiungo a /etc/fstab: $FSTAB_LINE"
  run cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d-%H%M%S)"
  if (( DRY_RUN )); then
    printf '  [dry-run] echo "%s" >> /etc/fstab\n' "$FSTAB_LINE"
  else
    printf '\n# marketdata: import legacy su SanDisk (sorgente read-once)\n%s\n' "$FSTAB_LINE" >> /etc/fstab
  fi
else
  read -r e_src e_type e_opts <<<"$existing"
  if [[ "$e_src" == "$FSTAB_SRC" ]] && [[ ",$e_opts," == *,bind,* ]]; then
    info "voce fstab gia' corretta ($e_src, $e_opts)"
  else
    warn "/etc/fstab ha gia' una voce per $FSTAB_TGT ma e' DIVERSA:"
    warn "  trovato: source=$e_src type=$e_type options=$e_opts"
    warn "  atteso:  source=$FSTAB_SRC options=bind"
    warn "  NON la modifico: risolvila a mano e rilancia."
    FSTAB_BAD=1
  fi
fi
run systemctl daemon-reload
if [[ -z "${FSTAB_BAD:-}" ]]; then
  mountpoint -q "$FSTAB_TGT" || { info "monto $FSTAB_TGT"; run mount "$FSTAB_TGT"; }
else
  warn "salto il mount finche' la voce fstab non e' corretta"
fi

# --------------------------------------------------------- repo codice
say "Repo codice — $REPO"
for d in "" /schemas /db /db/init; do
  info "$REPO$d"
  run install -d -m 0755 -o "$OWNER_USER" -g "$OWNER_USER" "$REPO$d"
done
if [[ ! -d "$REPO/.git" ]]; then
  info "git init"
  run sudo -u "$OWNER_USER" git -C "$REPO" init -q -b main
else
  info "repo git gia' inizializzato"
fi

say "Symlink schemas"
if [[ -L "$MD/schemas" ]]; then
  # Un symlink che esiste non basta: deve puntare al posto giusto. Uno che
  # punta altrove servirebbe schemi sbagliati restando invisibile.
  cur=$(readlink "$MD/schemas")
  if [[ "$cur" == "$REPO/schemas" ]]; then
    info "symlink gia' corretto -> $cur"
  else
    warn "$MD/schemas punta a '$cur', atteso '$REPO/schemas'"
    warn "  NON lo ripunto: verifica cosa c'e' dall'altra parte e risolvi a mano."
  fi
elif [[ -e "$MD/schemas" ]]; then
  warn "$MD/schemas esiste ed e' una dir reale: NON la tocco, risolvi a mano"
else
  info "$MD/schemas -> $REPO/schemas"
  run ln -s "$REPO/schemas" "$MD/schemas"
fi

# ---------------------------------------------------------- postgres
say "PostgreSQL — datadir sotto /srv/databases"
run install -d -m 0755 -o root -g root /srv/databases
run install -d -m 0700 -o 999 -g 999 "$PGDIR"
info "$PGDIR (uid 999 = postgres nell'image)"
run install -d -m 0750 -o "$OWNER_USER" -g "$OWNER_USER" /srv/docker/market-catalog

# ------------------------------------------- reserved blocks 5% -> 1%
say "Reserved blocks (ext4) 5% -> 1% sui filesystem dati"
for dev in "${DEV[SRV]}" "${DEV[ARC]}" "${DEV[DPC]}"; do
  cur=$(tune2fs -l "$dev" | awk -F: '/Reserved block count/{gsub(/ /,"",$2); print $2}')
  tot=$(tune2fs -l "$dev" | awk -F: '/^Block count/{gsub(/ /,"",$2); print $2}')
  pct=$(( cur * 100 / tot ))
  if (( pct > 1 )); then
    info "$dev: riservato ${pct}% -> 1% (libera ~$(( (cur - tot/100) * 4096 / 1024/1024/1024 ))G)"
    run tune2fs -m 1 "$dev"
  else
    info "$dev: gia' all'${pct}%"
  fi
done

# ------------------------------------------------------------- report
say "Fatto"
if (( DRY_RUN )); then
  echo "   (dry-run: nessuna modifica applicata)"
  exit 0
fi

echo
find "$MD" -maxdepth 2 \( -type d -o -type l \) -printf '%M %u:%g  %p\n' | sort -k3
echo
df -hT /srv /archive /cold
echo
warn "$OWNER_USER e' stato aggiunto a marketdata: serve un nuovo login perche' faccia effetto."
[[ -d /srv/market-data ]] && \
  warn "/srv/market-data (legacy) e' ancora al suo posto: dismettila con dismiss-legacy.sh."
exit 0
