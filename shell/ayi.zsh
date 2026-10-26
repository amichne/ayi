# Sourced explicitly via eval "$(ayi shell init zsh)" after compinit.
_ayi() {
    emulate -L zsh
    setopt extendedglob
    local -a args rows fields candidates directories files
    local row i value description tag=values ret=1
    local command=${words[1]:t}
    for (( i=2; i<CURRENT; i++ )); do
        args+=("${(Q)words[i]}")
    done
    args+=("${(Q)PREFIX}")
    rows=("${(@f)$("$_ayi_root/bin/ayi" complete --zsh "${words[1]:t}" "$((CURRENT-2))" "${args[@]}" 2>/dev/null)}")
    for row in "${rows[@]}"; do
        fields=("${(@ps:\t:)row}")
        (( ${#fields} >= 2 )) || continue
        # _describe uses a colon-delimited display format, not shell syntax.
        value=${fields[2]//\\/\\\\}
        value=${value//:/\\:}
        description=${fields[3]//\\/\\\\}
        description=${description//:/\\:}
        if [[ ${fields[1]} == directory ]]; then
            directories+=("$value:$description")
        elif [[ ${fields[1]} == file ]]; then
            files+=("$value:$description")
        else
            candidates+=("$value:$description")
        fi
    done
    if [[ $command == ayi ]]; then
        if [[ $PREFIX == -* ]]; then
            tag=options
        elif [[ $CURRENT == 2 || ( $CURRENT == 3 && ${words[2]} == (install|shell) ) ]]; then
            tag=commands
        fi
    fi
    if (( ${#candidates} )); then
        _describe -t "$tag" "$command $tag" candidates && ret=0
    fi
    if (( ${#directories} )); then
        _describe -t directories "$command directories" directories -S '' && ret=0
    fi
    if (( ${#files} )); then
        _describe -t files "$command files" files && ret=0
    fi
    return $ret
}

_ayi_refresh() {
    emulate -L zsh
    # Globs use shell primitives. Python runs only when registry/link membership changes.
    local -a entries targets names
    local name snapshot output
    entries=("$_ayi_root"/tasks/*(N) "$HOME"/.local/bin/*(N@))
    # Include link targets: replacing a link at the same path can change ownership.
    targets=("${(@)entries:A}")
    snapshot="${(j:|:)entries}|${(j:|:)targets}"
    [[ ${_ayi_snapshot-} == "$snapshot" ]] && return 0
    output=$("$_ayi_root/bin/ayi" complete --commands 2>/dev/null) || return 0
    names=("${(@f)output}")
    for name in "${_ayi_registered[@]}"; do
        [[ ${_comps[$name]-} == _ayi ]] && compdef -d "$name"
    done
    for name in ayi "${names[@]}"; do
        # Sourcing integration opts into Ayi-owned names, not replacing another
        # tool's completion. Users can explicitly compdef _ayi NAME to take over.
        [[ -n $name && ( -z ${_comps[$name]-} || ${_comps[$name]} == _ayi ) ]] && compdef _ayi "$name"
    done
    _ayi_registered=("${names[@]}")
    _ayi_snapshot=$snapshot
    return 0
}

if (( ! $+functions[compdef] )); then
    print -u2 'ayi: run autoload -Uz compinit && compinit before ayi shell init zsh'
else
    typeset -ga _ayi_registered
    autoload -Uz add-zsh-hook
    add-zsh-hook precmd _ayi_refresh
    unset _ayi_snapshot
    _ayi_refresh
fi
