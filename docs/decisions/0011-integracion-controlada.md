# ADR-0011: integración controlada con fusión en memoria

**Estado:** aceptada

## Contexto

Los worktrees aíslan la edición, pero el riesgo real aparece al juntar el trabajo:

- conflictos de Git;
- conflictos semánticos: dos cambios que no chocan línea a línea pero rompen el programa al combinarse;
- cambios que el agente no declaró en su informe.

La especificación prohíbe el merge automático a la rama principal sin reglas, pruebas y aprobación.

## Decisión

- `integrate check` **no modifica nada**. Usa `git merge-tree --write-tree` para fusionar en memoria y ejecuta las pruebas sobre ese árbol fusionado, sacado en un worktree temporal desechable.
- `integrate merge` es un comando humano con `--yes`. Solo fusiona con veredicto `ready`, con el worktree principal limpio y en la base, y si la base no cambió desde la revisión. Usa `--no-ff`, verifica que el árbol fusionado sea el probado y nunca hace push.
- El commit temporal de prueba usa una identidad fija (`AgentBridge`) para no depender de la configuración del usuario ni hacerse pasar por él. Nunca entra al historial.

## Por qué probar el árbol fusionado

Si `main` agregó `base.txt` y la rama agregó `feature.txt`, una prueba que necesite los dos archivos falla en la rama aislada y pasa en el resultado fusionado. También ocurre lo contrario: las dos ramas pasan por separado y la combinación falla. La prueba `test_tests_run_on_the_merged_result_not_the_isolated_branch` cubre este caso. Además se verificó que falla si el código prueba la rama aislada.

## Consecuencias

- Requiere Git ≥ 2.38.
- El comando de pruebas se ejecuta sin shell. Los flujos complejos van en un script.
- Fusionar sigue siendo una decisión humana. AgentBridge prepara la evidencia (diff, conflictos, pruebas, avisos) y la deja auditada.
