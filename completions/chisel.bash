_chisel_complete() {
  local current=${COMP_WORDS[COMP_CWORD]}
  mapfile -t COMPREPLY < <(chisel complete "$current")
  if [[ ${#COMPREPLY[@]} == 0 ]]; then
    mapfile -t COMPREPLY < <(compgen -f -- "$current")
  fi
}
complete -o filenames -F _chisel_complete chisel
