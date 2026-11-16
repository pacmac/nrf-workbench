#!/bin/sh
# install.sh - the nrf-workbench bootstrap. POSIX sh. It assumes NOTHING about the host.
#
#   curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh
#   sh install.sh [--check] [--yes] [--prefix DIR] [--features a,b] [--ref COMMIT] [--git] ...
#
# What this file does, and ONLY this: detect what the host has, fetch the project, make sure a
# usable Python exists, hand over to install/install.py. Everything else is Python, so it can be
# tested. See docs/specs/install.md.
#
# Rules it keeps:
#   * No tool is assumed. A downloader (curl or wget), tar and gzip, and a Python of at least 3.10
#     are DETECTED; each missing one stops with the exact reason (exit 4). git is NOT needed.
#   * The whole script is one function called on the LAST line, so a download cut short runs
#     nothing (a syntax error stops it before anything executes).
#   * Standard input is the pipe it came from, so it never reads it. Questions go to the
#     terminal, opened explicitly; with no terminal it never blocks.
#   * It fetches the tarball of an EXACT commit (named by the GitHub API), not a clone.
#   * It has no sleep and no timeout of its own: a network step ends on the transfer ending or on
#     the human's Ctrl-C.
#
# Exit codes: 0 done, 1 failed, 2 usage, 3 needs a person (plan shown, nothing applied),
#             4 a prerequisite is missing or unsupported.

nrfwb_say()  { printf '%s\n' "nrf-workbench: $*"; }
nrfwb_warn() { printf '%s\n' "nrf-workbench: $*" >&2; }
nrfwb_die()  { _rc=$1; shift; nrfwb_warn "$*"; NRFWB_RC=$_rc; return "$_rc"; }

nrfwb_usage() {
  cat <<'EOF'
nrf-workbench installer

  sh install.sh [options]          (or: curl -fsSL <url>/install/install.sh | sh -s -- [options])

  --check            show the preflight and the plan, change nothing
  --yes              accept only the SAFE defaults when there is no terminal to ask on
  --prefix DIR       install into DIR (no folder is assumed; required with no terminal)
  --features LIST    comma-separated: core,flash,console,build   (default: core,flash,console)
  --ref COMMIT       install this commit, tag or branch (default: main); pin a commit to repeat
  --git              make a developer git checkout at the prefix instead of unpacking a tarball
  --wheelhouse DIR   install packages from DIR with no network
  --add-user NAME    add NAME to the group that owns serial devices (asked, never automatic)
  --uninstall        remove the venv and the install record; keeps configuration and logs
  --no-verify        skip the verify step (not recommended)
  -h, --help         this text

Exit codes: 0 done, 1 failed, 2 usage, 3 needs a person, 4 prerequisite missing.
EOF
}

# True if a controlling terminal can be opened for reading (stdin may be a pipe).
nrfwb_have_tty() { ( : </dev/tty ) 2>/dev/null; }

# nrfwb_ask "question" -> 0 for yes, 1 for no or no terminal. Reads the terminal, never stdin.
nrfwb_ask() {
  nrfwb_have_tty || return 1
  printf '%s [y/N] ' "$1" >&2
  _ans=
  read -r _ans </dev/tty || return 1
  case $_ans in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

# nrfwb_dl URL DEST [HEADER]: download with curl, else wget. Returns the tool's status.
nrfwb_dl() {
  if [ -n "$NRFWB_CURL" ]; then
    if [ -n "$3" ]; then curl -fsSL -H "$3" -o "$2" "$1"; else curl -fsSL -o "$2" "$1"; fi
  else
    if [ -n "$3" ]; then wget -q --header="$3" -O "$2" "$1"; else wget -q -O "$2" "$1"; fi
  fi
}

# Find a Python of at least 3.10. Sets NRFWB_PY. Reports what it found if none qualifies.
nrfwb_find_python() {
  NRFWB_PY=
  NRFWB_SEEN=
  for _c in python3 python3.13 python3.12 python3.11 python3.10 python; do
    command -v "$_c" >/dev/null 2>&1 || continue
    _v=$("$_c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || continue
    if "$_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      NRFWB_PY=$(command -v "$_c")
      return 0
    fi
    NRFWB_SEEN="$NRFWB_SEEN $_c=$_v"
  done
  return 1
}

nrfwb_main() {
  NRFWB_RC=0
  NRFWB_REPO=${NRF_WORKBENCH_REPO:-pacmac/nrf-workbench}
  NRFWB_API=${NRF_WORKBENCH_API:-https://api.github.com}
  NRFWB_WEB=${NRF_WORKBENCH_WEB:-https://github.com}
  NRFWB_REF=main
  NRFWB_GIT=0
  NRFWB_STAGE=

  # Split our own flags (--ref, --git, --help) from the ones that belong to the Python installer.
  _n=$#; _i=0
  while [ "$_i" -lt "$_n" ]; do
    _a=$1; shift; _i=$((_i + 1))
    case $_a in
      --ref)
        if [ "$_i" -ge "$_n" ]; then nrfwb_die 2 "--ref needs a value"; return 2; fi
        NRFWB_REF=$1; shift; _i=$((_i + 1)) ;;
      --ref=*) NRFWB_REF=${_a#--ref=} ;;
      --git)   NRFWB_GIT=1; set -- "$@" "$_a" ;;
      -h|--help) nrfwb_usage; return 0 ;;
      *) set -- "$@" "$_a" ;;
    esac
  done

  # Plain http is refused unless the caller deliberately overrides the endpoints for a test.
  case $NRFWB_API$NRFWB_WEB in
    https://*https://*) : ;;
    *) if [ -z "$NRF_WORKBENCH_ALLOW_HTTP" ]; then
         nrfwb_die 4 "refusing a non-https endpoint; set NRF_WORKBENCH_ALLOW_HTTP=1 only for a test"; return 4
       fi ;;
  esac

  # Are we being run from a checkout (a file with install.py beside it) or piped from curl?
  NRFWB_LOCAL=
  if [ -f "$0" ]; then
    _d=$(dirname "$0")
    if [ -f "$_d/install.py" ] && [ -d "$_d/nrfwb_install" ]; then
      NRFWB_LOCAL=$(cd "$_d" && pwd)
    fi
  fi

  # --- prerequisites, each detected, each with a stated outcome --------------------------------
  NRFWB_CURL=; NRFWB_WGET=
  command -v curl >/dev/null 2>&1 && NRFWB_CURL=1
  command -v wget >/dev/null 2>&1 && NRFWB_WGET=1

  if [ -z "$NRFWB_LOCAL" ]; then
    if [ -z "$NRFWB_CURL" ] && [ -z "$NRFWB_WGET" ]; then
      nrfwb_die 4 "neither curl nor wget was found, so the project cannot be downloaded. Install one of them (on Debian and Ubuntu: apt-get install curl), or download the project by hand and run install/install.sh from it."
      return 4
    fi
    for _t in tar gzip; do
      if ! command -v "$_t" >/dev/null 2>&1; then
        nrfwb_die 4 "$_t was not found and is needed to unpack the download (on Debian and Ubuntu: apt-get install $_t)."
        return 4
      fi
    done
  fi
  if [ "$NRFWB_GIT" = 1 ] && ! command -v git >/dev/null 2>&1; then
    nrfwb_die 4 "--git was asked for but git was not found. git is not needed for a normal install; drop --git, or install git."
    return 4
  fi

  # Python of at least 3.10 (the floor). Never built from source here.
  if ! nrfwb_find_python; then
    _seen=${NRFWB_SEEN:-none}
    nrfwb_warn "no Python of at least 3.10 was found (found:$_seen)."
    _pk="python3 python3-venv"
    if [ "$(id -u 2>/dev/null)" = 0 ] && command -v apt-get >/dev/null 2>&1; then
      _cmd="apt-get update && apt-get install -y $_pk"
      if nrfwb_ask "Run this now?  $_cmd"; then
        # shellcheck disable=SC2086
        if apt-get update && apt-get install -y $_pk; then
          if ! nrfwb_find_python; then
            nrfwb_die 4 "installed $_pk but the Python it provides is still older than 3.10 (found:$NRFWB_SEEN). This distribution is too old for nrf-workbench; use a newer Python or distribution."
            return 4
          fi
        else
          nrfwb_die 4 "$_cmd failed; fix that and run again."
          return 4
        fi
      else
        nrfwb_die 4 "a Python of at least 3.10 is needed. As root, run:  $_cmd   then run this again. (Not run: no terminal to ask on, or you declined.)"
        return 4
      fi
    else
      _sudo=; [ "$(id -u 2>/dev/null)" != 0 ] && command -v sudo >/dev/null 2>&1 && _sudo="sudo "
      nrfwb_die 4 "a Python of at least 3.10 is needed. On Debian and Ubuntu: ${_sudo}apt-get update && ${_sudo}apt-get install -y $_pk   then run this again. On another distribution, install Python 3.10 or newer with its package manager."
      return 4
    fi
  fi

  # --- get the project --------------------------------------------------------------------------
  if [ -n "$NRFWB_LOCAL" ]; then
    _src=$(cd "$NRFWB_LOCAL/.." && pwd)
    nrfwb_say "running from a checkout: $_src"
    if nrfwb_have_tty; then
      "$NRFWB_PY" "$NRFWB_LOCAL/install.py" --source "$_src" --commit local "$@" </dev/tty
    else
      "$NRFWB_PY" "$NRFWB_LOCAL/install.py" --source "$_src" --commit local "$@" </dev/null
    fi
    return $?
  fi

  NRFWB_STAGE=$(mktemp -d 2>/dev/null) || {
    NRFWB_STAGE="${TMPDIR:-/tmp}/nrf-workbench.$$"
    mkdir -m 700 "$NRFWB_STAGE" || { nrfwb_die 1 "could not make a scratch directory"; return 1; }
  }
  trap 'rm -rf "$NRFWB_STAGE"' EXIT HUP INT TERM

  # The exact commit the ref points at. No git needed: the GitHub API answers with a bare SHA.
  NRFWB_SHA=
  case $NRFWB_REF in
    *[!0-9a-f]*) : ;;
    *) [ ${#NRFWB_REF} -eq 40 ] && NRFWB_SHA=$NRFWB_REF ;;
  esac
  if [ -z "$NRFWB_SHA" ]; then
    nrfwb_say "asking GitHub which commit '$NRFWB_REF' is (Ctrl-C to abort)"
    if nrfwb_dl "$NRFWB_API/repos/$NRFWB_REPO/commits/$NRFWB_REF" "$NRFWB_STAGE/sha" "Accept: application/vnd.github.sha"; then
      _s=$(tr -d '[:space:]' <"$NRFWB_STAGE/sha")
      case $_s in
        *[!0-9a-f]*) : ;;
        *) [ ${#_s} -eq 40 ] && NRFWB_SHA=$_s ;;
      esac
    fi
  fi

  if [ -n "$NRFWB_SHA" ]; then
    nrfwb_say "installing commit $NRFWB_SHA"
    _url="$NRFWB_WEB/$NRFWB_REPO/archive/$NRFWB_SHA.tar.gz"
    nrfwb_dl "$_url" "$NRFWB_STAGE/src.tar.gz" || { nrfwb_die 1 "could not download $_url"; return 1; }
  else
    nrfwb_warn "could not resolve '$NRFWB_REF' to a commit through the GitHub API (it may be unreachable or rate-limited); falling back to the branch tarball, UNPINNED."
    NRFWB_SHA=unpinned
    _url="$NRFWB_WEB/$NRFWB_REPO/archive/refs/heads/$NRFWB_REF.tar.gz"
    if ! nrfwb_dl "$_url" "$NRFWB_STAGE/src.tar.gz"; then
      _url="$NRFWB_WEB/$NRFWB_REPO/archive/refs/tags/$NRFWB_REF.tar.gz"
      nrfwb_dl "$_url" "$NRFWB_STAGE/src.tar.gz" || { nrfwb_die 1 "could not download '$NRFWB_REF' as a branch or a tag from $NRFWB_WEB/$NRFWB_REPO"; return 1; }
    fi
  fi

  tar -tzf "$NRFWB_STAGE/src.tar.gz" >/dev/null 2>&1 || { nrfwb_die 1 "the download is not a valid gzip tar file"; return 1; }
  mkdir "$NRFWB_STAGE/src" && tar -xzf "$NRFWB_STAGE/src.tar.gz" -C "$NRFWB_STAGE/src" || { nrfwb_die 1 "could not unpack the download"; return 1; }
  _cnt=0; _src=
  for _d in "$NRFWB_STAGE"/src/*; do _cnt=$((_cnt + 1)); _src=$_d; done
  if [ "$_cnt" -ne 1 ] || [ ! -d "$_src" ] || [ ! -f "$_src/install/install.py" ]; then
    nrfwb_die 1 "the downloaded tree is not shaped as expected (no install/install.py)"; return 1
  fi

  # Hand over. The installer's input is the terminal, or nothing; never the pipe we came from.
  if nrfwb_have_tty; then
    "$NRFWB_PY" "$_src/install/install.py" --source "$_src" --staged --commit "$NRFWB_SHA" --ref "$NRFWB_REF" "$@" </dev/tty
  else
    "$NRFWB_PY" "$_src/install/install.py" --source "$_src" --staged --commit "$NRFWB_SHA" --ref "$NRFWB_REF" "$@" </dev/null
  fi
  _rc=$?
  return "$_rc"
}

# The LAST line, a single command (the shell exits with its status). Everything above is only
# definitions, so a download cut short runs nothing: a cut inside a function is a syntax error, and
# a cut inside this line is a command that does not exist.
nrfwb_main "$@"
