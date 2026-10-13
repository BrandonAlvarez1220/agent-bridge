# Worktrees e integración controlada

## Por qué worktrees

Un **worktree** es una carpeta adicional enlazada al mismo repositorio, con su propia rama. Dos agentes en dos worktrees nunca escriben en la misma carpeta, así que no pueden pisarse archivos a mitad de una edición. Todos comparten el historial (`.git`), de modo que crear un worktree es instantáneo y no copia el repositorio.

Lo que un worktree **no** resuelve: que dos ramas cambien la misma línea (conflicto de Git) o que cambios compatibles línea a línea rompan juntos el programa (conflicto semántico). Para eso está la integración controlada.

## Crear worktrees

```bash
# Uno por agente
agentbridge worktree create --project-id demo --agent-id claude
#   → carpeta ../app-claude, rama claude/work

# Uno por tarea (la tarea debe existir)
agentbridge worktree create --project-id demo --agent-id opencode --task-id 7
#   → carpeta ../app-opencode-t7, rama opencode/task-7-agregar-login

agentbridge worktree list --project-id demo
```

`create` **nunca reutiliza** una carpeta o rama existentes: si ya existen, falla. Además actualiza en AgentBridge el worktree y la rama del agente; así la integración sabe qué rama corresponde a qué agente. Después ejecuta `agentbridge setup` en la carpeta nueva.

## Integración

```bash
# 1. Revisar: no modifica nada
agentbridge integrate check --project-id demo --branch opencode/task-7-agregar-login \
    --test-cmd "uv run pytest -q"

# 2. Fusionar: repite la revisión y solo fusiona si el veredicto es 'ready'
agentbridge integrate merge --project-id demo --branch opencode/task-7-agregar-login \
    --test-cmd "uv run pytest -q" --yes
```

### Qué hace `check`

1. **Fusión en memoria** con `git merge-tree --write-tree` (Git ≥ 2.38). Obtiene el árbol resultante o la lista de archivos en conflicto sin tocar ninguna rama ni ningún worktree.
2. **Pruebas sobre el resultado fusionado.** Convierte ese árbol en un commit temporal que no queda en ninguna rama, lo saca en un worktree temporal fuera del repo, ejecuta `--test-cmd` y lo elimina. Se prueba la combinación de `main` con la rama, no la rama aislada: dos ramas pueden pasar sus pruebas por separado y fallar juntas.
3. **Cruce con AgentBridge.** Avisa de archivos cambiados que ningún informe de tarea declaró y de archivos que ahora están reservados por otro agente.

| Veredicto | Significado | Código de salida |
| --- | --- | --- |
| `ready` | Sin conflictos y pruebas en verde sobre el resultado fusionado | 0 |
| `ready_without_tests` | Sin conflictos, pero no se pasó `--test-cmd` | 0 |
| `conflicts` | Git no puede fusionar sin intervención; no se ejecutan pruebas | 3 |
| `tests_failed` | Las pruebas fallan sobre el resultado fusionado | 3 |
| `nothing_to_integrate` | La rama no tiene commits nuevos respecto de la base | 3 |

### Salvaguardas de `merge`

- Exige `--yes` y `--test-cmd`. El veredicto debe ser `ready`; `ready_without_tests` no alcanza.
- El worktree principal debe estar **en la rama base y sin cambios**. No cambia de rama por ti.
- Si la base avanzó entre la revisión y la fusión, se niega y pide revisar de nuevo.
- Usa `git merge --no-ff`, así la integración queda visible en el historial. Si Git falla, ejecuta `git merge --abort`.
- Después de fusionar, compara el árbol resultante con el que se probó y avisa si difieren.
- Marca como `done` las tareas en `review` del agente (actor `integrator`) salvo `--keep-tasks`, y lo audita.
- **Nunca hace `git push`.** Publicar es decisión tuya.

### Comando de pruebas

Se divide con `shlex` y se ejecuta **sin shell**: no hay inyección, pero tampoco pipes ni `&&`. Si necesitas varios pasos, ponlos en un script (`./scripts/ci.sh`) y pásalo como comando.

## Recuperación

| Situación | Qué hacer |
| --- | --- |
| Un worktree temporal quedó tras un corte de energía | `git worktree prune` (los temporales viven en el directorio temporal del sistema) |
| Conflicto en `check` | El agente dueño de la rama hace merge o rebase de `main` en su worktree, resuelve, hace commit y se vuelve a revisar |
| Pruebas en rojo solo al fusionar | Conflicto semántico: crea una tarea para el agente dueño y adjunta `test_output_tail` (`--json`) |
| Borrar un worktree ya integrado | `git worktree remove ../app-claude-t7 && git branch -d claude/task-7-...` |
