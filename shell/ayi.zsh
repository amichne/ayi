# Sourced explicitly via eval "$(ayi shell init zsh)" after compinit.
_ayi() {
    emulate -L zsh
    local -a args rows fields description
    local row i
    for (( i=2; i<CURRENT; i++ )); do
        args+=("${(Q)words[i]}")
    done
    args+=("${(Q)PREFIX}")
    rows=("${(@f)$("$_ayi_root/bin/ayi" complete --zsh "${words[1]:t}" "$((CURRENT-2))" "${args[@]}" 2>/dev/null)}")
    for row in "${rows[@]}"; do
        fields=("${(@ps:\t:)row}")
        (( ${#fields} >= 2 )) || continue
        description=("${fields[2]}${fields[3]:+ -- ${fields[3]}}")
        if [[ ${fields[1]} == directory ]]; then
            compadd -S '' -d description -- "${fields[2]}"
        else
            compadd -d description -- "${fields[2]}"
        fi
    done
}

_ayi_refresh() {
    emulate -L zsh
    # Globs use shell primitives. Python runs only when registry/link membership changes.
    local -a entries names
    local name snapshot
    entries=("$_ayi_root"/tasks/*(N) "$HOME"/.local/bin/*(N@))
    snapshot="${(j:|:)entries}"
    [[ ${_ayi_snapshot-} == "$snapshot" ]] && return 0
    names=("${(@f)$("$_ayi_root/bin/ayi" complete --commands)}") || return
    for name in "${_ayi_registered[@]}"; do
        [[ ${_comps[$name]-} == _ayi ]] && compdef -d "$name"
    done
    compdef _ayi ayi
    for name in "${names[@]}"; do
        [[ -n $name ]] && compdef _ayi "$name"
    done
    _ayi_registered=("${names[@]}")
    _ayi_snapshot=$snapshot
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
