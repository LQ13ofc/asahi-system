# Conclusão: KEEP

O fluxo agora coleta o snapshot de processos inicial antes do início dos contadores do sistema, captura CPU/PSI ao redor da timeline e obtém os contadores finais antes da varredura completa final. As leituras cgroup ficam fora do delta system-wide. As porcentagens de CPU por processo usam e exportam `process_observation_window_seconds`, separado de `observation_window_seconds`.

Um teste de regressão com chamadas instrumentadas confirma a ordem: scan inicial → contador inicial → janela → contador final → scan final. A suíte também verifica o campo de duração novo no schema.

Os diagnósticos antes/depois continuam mostrando que o coletor no Cloud usa cerca de 0,4 s de CPU/wall para 1.197 leituras `smaps_rollup` por varredura. Esse custo não é subtraído de nenhum benchmark e não demonstra desempenho no M1; apenas foi isolado das métricas system-wide da sessão. O A/B final de hardware ainda é `M1_REQUIRED`.
