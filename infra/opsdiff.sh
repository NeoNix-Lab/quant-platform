#!/usr/bin/env bash
# opsdiff.sh (rev 2) — delta fra lo stato attuale del server e cio' che
# provision-marketdata.sh produrrebbe. Sola lettura, nessun privilegio.
#
# Verifica owner, GROUP, mode, ACL di accesso e ACL di default. Il confronto
# delle ACL e' ESATTO: una entry nominale in piu' (es. una residua che desse a
# neonix rwx su canonical/) viene segnalata, non ignorata. Senza questo il
# modello di accesso non e' verificabile, perche' e' costruito sulle ACL.
#
#   +  verra' creato        ~  esiste ma verra' modificato
#   =  gia' conforme        !  attenzione / non conforme
#
MD=/srv/marketdata
COLD=/archive/marketdata-cold
DEEPCOLD=/cold/marketdata-deepcold
PGDIR=/srv/databases/postgres-market-catalog
REPO=/opt/market-platform
OWNER_USER=neonix

nc=0; nm=0; nk=0; nw=0
add()  { printf '  \033[32m+\033[0m %s\n' "$*"; nc=$((nc+1)); }
mod()  { printf '  \033[33m~\033[0m %s\n' "$*"; nm=$((nm+1)); }
keep() { printf '  \033[90m=\033[0m %s\n' "$*"; nk=$((nk+1)); }
attn() { printf '  \033[31m!\033[0m %s\n' "$*"; nw=$((nw+1)); }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

HAVE_ACL=0
command -v getfacl >/dev/null 2>&1 && HAVE_ACL=1

# entry ACL nominali (nome non vuoto) di un path; $2 = 'a' accesso | 'd' default
named_acl() {
  getfacl -c"$2" -- "$1" 2>/dev/null \
    | grep -E '^(user|group):[^:]+:' \
    | sed 's/[[:space:]]*#.*$//' \
    | sort
}

# rwx -> cifra ottale (tollera s/t al posto di x)
perm2oct() {
  local s="$1" v=0
  [[ ${s:0:1} == r ]] && v=$((v+4))
  [[ ${s:1:1} == w ]] && v=$((v+2))
  [[ ${s:2:1} == x || ${s:2:1} == s || ${s:2:1} == t ]] && v=$((v+1))
  printf '%s' "$v"
}

# Modo EFFETTIVO di un path.
#
# stat %a non e' affidabile su file con ACL: quando esiste una entry nominale,
# i bit di gruppo di st_mode riportano la MASK, non i permessi del gruppo
# proprietario. Una directory creata 2750 con 'u:neonix:rwx' legge 2770 pur
# essendo esattamente com'e' stata voluta. Qui si ricostruisce il modo dalle
# entry user::/group::/other:: di getfacl, che sono i permessi veri.
eff_mode() {
  local p="$1" raw special u g o
  raw=$(stat -c %a -- "$p" 2>/dev/null) || return 1
  if (( HAVE_ACL )); then
    # niente ternario: con un modo a 3 cifre il ramo vero sarebbe una stringa
    # vuota, cioe' un errore di sintassi aritmetica che uccide la subshell
    special=""
    (( ${#raw} > 3 )) && special=${raw:0:$(( ${#raw} - 3 ))}
    # '0750' e '750' sono lo stesso modo: senza bit speciali non si prefissa
    # nulla, altrimenti il confronto con l'atteso fallisce su una stringa
    [[ "$special" == 0 ]] && special=""
    u=$(getfacl -pc -- "$p" 2>/dev/null | awk -F: '/^user::/{print $3; exit}')
    g=$(getfacl -pc -- "$p" 2>/dev/null | awk -F: '/^group::/{print $3; exit}')
    o=$(getfacl -pc -- "$p" 2>/dev/null | awk -F: '/^other::/{print $3; exit}')
    if [[ -n "$u" && -n "$g" && -n "$o" ]]; then
      printf '%s%s%s%s' "$special" "$(perm2oct "$u")" "$(perm2oct "$g")" "$(perm2oct "$o")"
      return 0
    fi
  fi
  printf '%s' "$raw"
}

# dircheck <path> <owner> <group> <mode> [acl_attese_in_notazione_getfacl...]
dircheck() {
  local p="$1" o="$2" g="$3" m="$4"; shift 4
  local expected; expected=$(printf '%s\n' "$@" | grep -v '^$' | sort)
  local nacl; nacl=$(printf '%s\n' "$@" | grep -vc '^$')

  if [[ ! -e "$p" ]]; then
    # Distinguere "non esiste" da "non ho i permessi per guardare": dentro una
    # dir 0750 di root, il test -e fallisce anche se il path esiste eccome.
    local err; err=$(LC_ALL=C stat -- "$p" 2>&1 >/dev/null)
    if [[ "$err" == *"Permission denied"* ]]; then
      attn "$p  non verificabile come $(id -un) (permesso negato sul percorso)"
      return
    fi
    if (( nacl > 0 )); then add "$p  ($o:$g $m + $nacl ACL)"; else add "$p  ($o:$g $m)"; fi
    return
  fi

  local co cg cm delta=""
  co=$(stat -c %U "$p"); cg=$(stat -c %G "$p"); cm=$(eff_mode "$p")
  [[ "$co" != "$o" ]] && delta+=" owner $co->$o"
  [[ "$cg" != "$g" ]] && delta+=" group $cg->$g"
  [[ "$cm" != "$m" ]] && delta+=" mode $cm->$m"

  if (( HAVE_ACL )); then
    local acl_a acl_d
    acl_a=$(named_acl "$p" a); acl_d=$(named_acl "$p" d)
    [[ "$acl_a" != "$expected" ]] && delta+=" acl-accesso"
    [[ "$acl_d" != "$expected" ]] && delta+=" acl-default"
  fi

  if [[ -z "$delta" ]]; then
    keep "$p  ($o:$g $m$( (( HAVE_ACL )) && [[ -n "$expected" ]] && echo ', ACL ok'))"
  else
    mod "$p $delta"
    if (( HAVE_ACL )) && [[ "$delta" == *acl-* ]]; then
      diff <(named_acl "$p" a) <(printf '%s\n' "$expected") 2>/dev/null \
        | grep -E '^[<>]' | sed 's/^/        /'
    fi
  fi
}

hdr "UTENTI E GRUPPI"
getent group marketdata >/dev/null && keep "gruppo marketdata" || add "gruppo marketdata (system)"
for u in mkt-collector mkt-transform mkt-agent; do
  id -u "$u" >/dev/null 2>&1 && keep "utente $u" || add "utente $u (system, nologin, gruppo marketdata)"
done
if id -nG "$OWNER_USER" | tr ' ' '\n' | grep -qx marketdata; then
  keep "$OWNER_USER e' in marketdata"
else
  mod "$OWNER_USER: aggiunto al gruppo marketdata (richiede nuovo login)"
fi

hdr "PACCHETTI"
if (( HAVE_ACL )); then
  keep "acl installato (ACL verificabili)"
else
  add "apt install acl"
  attn "getfacl assente: le ACL NON sono verificabili in questo passaggio"
fi

hdr "SANDISK  /archive  (tier COLD)"
dircheck /archive/backups          root          root       750
dircheck /archive/backups/postgres root          root       750
dircheck /archive/imports          mkt-collector marketdata 2750
dircheck /archive/imports/legacy   mkt-collector marketdata 2750
dircheck "$COLD"                   root          marketdata 2750 \
    user:mkt-collector:rwx user:mkt-transform:rwx group:marketdata:r-x

hdr "TOSHIBA  /cold  (tier COLD, storage root 'deepcold')"
# 'deepcold' e' uno storage_root_id, non un tier: nel catalogo resta tier='cold'
# come /archive. Le ACL attese sono percio' identiche a quelle di $COLD.
dircheck /cold                     root          root       755
dircheck /cold/backups             root          root       750
dircheck /cold/backups/postgres    root          root       750
dircheck "$DEEPCOLD"               root          marketdata 2750     user:mkt-collector:rwx user:mkt-transform:rwx group:marketdata:r-x

hdr "SAMSUNG  /srv/marketdata  (tier HOT)"
dircheck "$MD"                root          marketdata 755
dircheck "$MD/staging"        mkt-collector marketdata 2750
for d in raw imports staging/live tmp; do
  dircheck "$MD/$d" mkt-collector marketdata 2750 group:marketdata:r-x
done
for d in canonical canonical/trades canonical/footprint canonical/l2; do
  dircheck "$MD/$d" mkt-transform marketdata 2750 group:marketdata:r-x
done
for d in features features/trade_microstructure features/footprint_microstructure \
         features/l2_microstructure fixtures staging/dev; do
  dircheck "$MD/$d" mkt-agent marketdata 2750 "user:$OWNER_USER:rwx" group:marketdata:r-x
done
dircheck "$MD/quarantine" mkt-transform marketdata 2750 \
    user:mkt-collector:rwx user:mkt-agent:rwx "user:$OWNER_USER:rwx" group:marketdata:r-x

hdr "PROVE DI ACCESSO EFFETTIVO (le uniche che contano davvero)"
if (( HAVE_ACL )) && [[ -d "$MD/canonical" ]]; then
  for spec in "canonical:NON scrivibile" "features:scrivibile" "raw:NON scrivibile" "fixtures:scrivibile"; do
    d=${spec%%:*}; want=${spec#*:}
    if [[ -w "$MD/$d" ]]; then got="scrivibile"; else got="NON scrivibile"; fi
    if [[ "$got" == "$want" ]]; then
      keep "$OWNER_USER su $d/: $got (atteso)"
    else
      attn "$OWNER_USER su $d/: $got, atteso $want"
    fi
  done
else
  printf '  \033[90m·\033[0m albero non ancora creato: prove rimandate al post-provisioning\n'
fi

hdr "SYMLINK"
if [[ -L "$MD/schemas" ]]; then
  cur=$(readlink "$MD/schemas")
  if [[ "$cur" == "$REPO/schemas" ]]; then
    keep "$MD/schemas -> $cur"
  else
    attn "$MD/schemas punta a '$cur', atteso '$REPO/schemas' (lo script non lo ripunta)"
  fi
elif [[ -e "$MD/schemas" ]]; then
  attn "$MD/schemas esiste come dir reale: lo script NON la tocca, va risolta a mano"
else
  add "$MD/schemas -> $REPO/schemas"
fi

hdr "MOUNT E IDENTITA' DEI DISCHI"
# I device non sono mai riferiti per nome /dev/sdX: l'enumerazione SATA su
# questa macchina e' gia' cambiata una volta. Si risolve dal mount point e si
# confronta l'UUID, che e' l'unica identita' stabile.
DISKS=(
  "/srv:0b56b5ac-eb32-4e9a-a8b2-5aaba41f8676:Samsung 870 EVO"
  "/archive:4c63f36c-9885-40a6-89e0-a35a6271fc6f:SanDisk SSD PLUS"
  "/cold:98f9e6c7-adb9-4858-a671-b941dfb3e4f0:Toshiba MQ04UBD200"
)
for spec in "${DISKS[@]}"; do
  IFS=: read -r mp uuid label <<<"$spec"
  if ! mountpoint -q "$mp" 2>/dev/null; then
    attn "$mp NON e' un mount point ($label): il provisioning si fermerebbe qui"
    continue
  fi
  src=$(findmnt -no SOURCE "$mp")
  actual=$(lsblk -no UUID "$src" 2>/dev/null)
  if [[ "$actual" == "$uuid" ]]; then
    keep "$mp -> $src  $label  UUID atteso"
  else
    attn "$mp ($src) ha UUID $actual, atteso $uuid ($label)"
  fi
done

# findmnt --fstab fa il parsing vero: ignora i commenti, a differenza di grep
existing=$(findmnt --fstab -no SOURCE,FSTYPE,OPTIONS --mountpoint "$MD/imports/legacy" 2>/dev/null || true)
if [[ -z "$existing" ]]; then
  add "/etc/fstab: bind /archive/imports/legacy -> $MD/imports/legacy"
  add "backup /etc/fstab.bak-<timestamp>"
else
  read -r e_src e_type e_opts <<<"$existing"
  if [[ "$e_src" == "/archive/imports/legacy" ]] && [[ ",$e_opts," == *,bind,* ]]; then
    keep "voce fstab corretta (source=$e_src options=$e_opts)"
  else
    attn "voce fstab DIVERSA: source=$e_src type=$e_type options=$e_opts"
  fi
fi
mountpoint -q "$MD/imports/legacy" 2>/dev/null && keep "bind gia' montato" || add "mount $MD/imports/legacy"

hdr "POSTGRES"
dircheck /srv/databases root root 755
if [[ -e "$PGDIR" ]]; then
  u=$(stat -c %u "$PGDIR")
  [[ "$u" == 999 ]] && keep "$PGDIR (uid 999)" || mod "$PGDIR uid $u->999"
else
  add "$PGDIR  (uid 999 = postgres, mode 700)"
fi
dircheck /srv/docker/market-catalog "$OWNER_USER" "$OWNER_USER" 750

hdr "REPO CODICE"
for d in "" /schemas /db /db/init; do
  dircheck "$REPO$d" "$OWNER_USER" "$OWNER_USER" 755
done
[[ -d "$REPO/.git" ]] && keep "$REPO/.git" || add "git init -b main in $REPO"

hdr "RESERVED BLOCKS (ext4)"
for mp in /srv /archive /cold; do
  src=$(findmnt -no SOURCE "$mp")
  read -r bs blocks bfree bavail < <(stat -f -c '%s %b %f %a' "$mp")
  resv=$(( bfree - bavail )); pct=$(( resv * 100 / blocks ))
  if (( pct > 1 )); then
    mod "$mp ($src): riservato ${pct}% -> 1%, libera ~$(( (resv - blocks/100) * bs / 1073741824 ))G"
  else
    keep "$mp ($src): gia' all'${pct}%"
  fi
done

hdr "NON TOCCATO DAL PROVISIONING"
for p in /srv/market-data /srv/docker/coinbase-recorder /srv/staging /archive/market-data; do
  [[ -e "$p" ]] && attn "$p resta dov'e' (dismiss-legacy.sh, separato)"
done
docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx postgres-test && \
  attn "container postgres-test resta up sulla porta 5432 (il nuovo catalogo usa la 5433)"

hdr "TOTALI"
printf '  %d da creare, %d da modificare, %d senza operazioni, %d da guardare\n' \
       "$nc" "$nm" "$nk" "$nw"
